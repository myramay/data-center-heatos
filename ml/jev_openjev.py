"""Jev provider backed by OpenJev (https://github.com/razorback16/openjev).

OpenJev speaks TypeSafe Jev's wire API (POST /v1/systemone), so this client
also works against TypeSafe's hosted Jev or Codiv's hosted OpenJev.

    HEATOS_JEV_URL      base URL            default http://127.0.0.1:8080
    HEATOS_JEV_MODEL    model id            default jev-latest (alias every OpenJev server accepts;
                        or openjev-latest / laya-1.0 / verdict-1.4)
    HEATOS_JEV_API_KEY  bearer token        optional (Codiv / TypeSafe)
    HEATOS_JEV_TIMEOUT  seconds             default 8.0 (queried in the background, never blocks the stream)

If the server is unreachable or errors, get_jev_opinion returns
available=False and the UI shows Monte Carlo only (contract behaviour).

ML_TEAM_INTEGRATION: selected in engine/providers.py.
"""

from __future__ import annotations

import os
import time

import httpx

from engine.config import load_site
from engine.contracts import JevOpinion, ModelCard, Playbook, SimState

PLAYBOOKS: dict[Playbook, str] = {
    "draw_storage": "discharge thermal storage to cover the gap between heat supply and demand",
    "start_steam_hp": "switch on steam heat pumps in steam-heated buildings",
    "start_backup": "fire the buildings' own backup boilers for the shortfall",
    "shift_compute": "run deferred flexible data center compute now to produce more heat",
    "curtail_cooling_export": "stop accepting office cooling heat into the loop (summer surplus)",
}
FALLBACK: Playbook = "draw_storage"


def describe_state(state: SimState) -> str:
    """Plain-language snapshot of the network for the model to read."""
    cfg = load_site(state.site)
    soc = state.storage_soc_mwh / state.storage_capacity_mwh if state.storage_capacity_mwh else 0.0
    gap = state.supply_kw - state.demand_kw
    lines = [
        f"District heat network at {cfg.site.name}, fed by waste heat from a data center ({cfg.site.address}).",
        f"Time: {state.time:%A %d %B %Y %H:%M}. Outdoor temperature {state.t_out_c:.1f} C.",
        f"Data center heat available now: {state.supply_kw / 1000:.2f} MW. Building heat demand now: {state.demand_kw / 1000:.2f} MW "
        f"({'surplus' if gap >= 0 else 'shortfall'} of {abs(gap) / 1000:.2f} MW).",
        f"Thermal storage: {state.storage_soc_mwh:,.0f} of {state.storage_capacity_mwh:,.0f} MWh ({soc:.0%} full).",
        f"Heat currently supplied by backup boilers: {state.backup_kw / 1000:.2f} MW.",
        f"Electricity price: ${state.electricity_price_usd_per_mwh:,.0f}/MWh.",
        f"Flexible data center compute that can be shifted: {'yes' if state.flexible_compute_available else 'no'}.",
        f"Steam heat pumps installed: {'yes' if _plan_has_steam_hp(state.site) else 'no'}.",
        f"Active stress events: {', '.join(s.replace('_', ' ') for s in state.active_scenarios) or 'none'}.",
    ]
    return "\n".join(lines)


def _plan_has_steam_hp(site: str) -> bool:
    try:
        from engine.recommend import plan_details
        return any(i.connect and i.option == "steam_hp" for i in plan_details(site).plan.items)
    except Exception:
        return site == "chelsea"


def questions(state: SimState) -> dict:
    steam = _plan_has_steam_hp(state.site)
    criteria = {k: v for k, v in PLAYBOOKS.items()
                if not (k == "start_steam_hp" and not steam)
                and not (k == "shift_compute" and not state.flexible_compute_available)
                and not (k == "curtail_cooling_export" and state.site != "chelsea")}
    return {
        "playbook": {"type": "choice", "criteria": criteria,
                     "instructions": "Which single operating action should the network operator take this hour?"},
        "supply_ok": {"type": "noul",
                      "instructions": "Will data center heat plus storage cover every guaranteed building's demand "
                                      "over the next 48 hours without backup boilers?"},
    }


class OpenJevProvider:
    def __init__(self, url: str | None = None, model: str | None = None, api_key: str | None = None,
                 timeout: float | None = None):
        self.url = (url or os.environ.get("HEATOS_JEV_URL", "http://127.0.0.1:8080")).rstrip("/")
        self.model = model or os.environ.get("HEATOS_JEV_MODEL", "jev-latest")
        self.api_key = api_key if api_key is not None else os.environ.get("HEATOS_JEV_API_KEY", "")
        self.timeout = timeout or float(os.environ.get("HEATOS_JEV_TIMEOUT", "8.0"))
        self._client = httpx.Client(timeout=self.timeout)
        self._down_until = 0.0
        self.last_error: str | None = None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _unavailable(self, err: str) -> JevOpinion:
        self.last_error = err
        self._down_until = time.monotonic() + 15.0      # don't hammer a dead server every simulated hour
        return JevOpinion(playbook=FALLBACK, playbook_probability=0.0, p_supply_meets_guarantees=0.0,
                          latency_ms=0.0, available=False)

    def get_jev_opinion(self, state: SimState) -> JevOpinion:
        if time.monotonic() < self._down_until:
            return JevOpinion(playbook=FALLBACK, playbook_probability=0.0, p_supply_meets_guarantees=0.0,
                              latency_ms=0.0, available=False)
        body = {"model": self.model, "state": describe_state(state), "questions": questions(state)}
        t0 = time.perf_counter()
        try:
            r = self._client.post(f"{self.url}/v1/systemone", json=body, headers=self._headers())
        except httpx.HTTPError as e:
            return self._unavailable(f"{type(e).__name__}: {e}")
        latency = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return self._unavailable(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            answers = r.json()["answers"]
            pb = answers["playbook"]
            choice = pb["choice"]
            p_choice = float(pb.get("probabilities", {}).get(choice, pb.get("confidence", 0.0)))
            p_ok = float(answers["supply_ok"]["noul"])
        except (KeyError, TypeError, ValueError) as e:
            return self._unavailable(f"unexpected response: {e}")
        self.last_error = None
        return JevOpinion(playbook=choice if choice in PLAYBOOKS else FALLBACK,
                          playbook_probability=round(min(max(p_choice, 0.0), 1.0), 4),
                          p_supply_meets_guarantees=round(min(max(p_ok, 0.0), 1.0), 4),
                          latency_ms=round(latency, 1), available=True)

    def model_card(self) -> ModelCard:
        return ModelCard(
            name=f"OpenJev ({self.model})", version="0.5", method="typed System One decisions read from model probabilities",
            datasets=["live network state rendered as text"],
            assumptions=[f"endpoint {self.url}", "playbook = choice question; P(supply meets guarantees) = noul question",
                         "unavailable -> Monte Carlo only"],
            is_mock=False)
