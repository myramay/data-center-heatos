"""Live runs, the per-site static bundle, and WebSocket frame assembly.

Shared by api/server.py (live) and scripts/record.py (recordings for Replay
mode), so replayed frames are byte-for-byte the same shape as live ones.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from datetime import datetime
from functools import lru_cache

import numpy as np

from engine import providers
from engine.config import load_site
from engine.contracts import SiteId
from engine.explain_tree import explain_building, site_tree
from engine.guarantees import DealReport, everyone_wins
from engine.impact import GAL_TO_M3, LATENT_HEAT_MJ_PER_KG, EVAPORATION_SHARE_OF_TOWER_WATER, impact
from engine.ledger import compute_ledger, hour_money
from engine.physics import pipe_tree
from engine.recommend import plan_details
from engine.scenarios import scenarios_for
from engine.sim import Simulation
from engine.site_scoring import site_scores
from engine.verdict import verdict

DEFAULT_START = {"chelsea": datetime(2026, 1, 12), "lansing": datetime(2026, 1, 12)}
DEFAULT_HOURS = 168
CONFIDENCE_EVERY_H = 6
LIVE_FUTURES = 40
DEAL_FUTURES = 16


def _street_y(y: float) -> float:
    """Nearest Chelsea street centreline (W(n) at y = 40 + 80 k)."""
    return 40.0 + 80.0 * round((y - 40.0) / 80.0)


def pipe_polylines(site: SiteId, buildings) -> list[dict]:
    pos = {"DC": (0.0, 0.0), **{b.id: (b.x_m, b.y_m) for b in buildings}}
    out = []
    for e in pipe_tree(site, buildings):
        (ax, ay), (bx, by) = pos[e.a], pos[e.b]
        if site == "chelsea":                      # run under streets: up to a street, across, then down
            sy = _street_y((ay + by) / 2)
            pts = [(ax, ay), (ax, sy), (bx, sy), (bx, by)]
        else:
            pts = [(ax, ay), (bx, by)]
        clean = [pts[0]] + [p for i, p in enumerate(pts[1:], 1) if p != pts[i - 1]]
        out.append({"from": e.a, "to": e.b, "length_m": e.length_m, "points": [[round(x, 1), round(y, 1)] for x, y in clean]})
    return out


@lru_cache(maxsize=4)
def deal_report(site: SiteId) -> DealReport:
    return everyone_wins(site, plan_details(site).plan, n_futures=DEAL_FUTURES)


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return _jsonable(o.model_dump())
    if hasattr(o, "__dataclass_fields__"):
        return _jsonable({k: getattr(o, k) for k in o.__dataclass_fields__})
    return o


@lru_cache(maxsize=4)
def bundle(site: SiteId) -> dict:
    """Everything static the UI needs for a site (also saved for Replay mode)."""
    cfg = load_site(site)
    buildings = providers.get_buildings(site)
    details = plan_details(site)
    connected = [b for b in buildings if b.id in set(details.plan.connected_ids())]
    year = Simulation(site, start=datetime(2026, 1, 1), hours=8760, narrate=False)
    yr = year.run()
    deal = deal_report(site)
    led = compute_ledger(year, yr, deal.terms, deal.premiums_usd)
    parties = []
    for p in led.parties.values():
        d = _jsonable(p)
        d["p_ahead"] = deal.p_ahead.get(p.id)
        d["cumulative"] = list(np.cumsum(p.cashflows))
        parties.append(d)
    tree = site_tree(site)
    return _jsonable({
        "site": site,
        "config": {
            "name": cfg.site.name, "address": cfg.site.address, "owner": cfg.site.owner, "recommended": cfg.site.recommended,
            "lat": cfg.site.lat, "lon": cfg.site.lon, "grid_rotation_deg": cfg.site.grid_rotation_deg,
            "data_center": {"name": cfg.data_center.name, "capacity_mw_th": cfg.data_center.capacity_mw_th.value,
                            "capture_fraction": cfg.data_center.capture_fraction.value,
                            "liquid_cooled": cfg.data_center.liquid_cooled,
                            "compute_follows_heat": cfg.data_center.compute_follows_heat,
                            "flexible_compute_share": cfg.data_center.flexible_compute_share.value},
            "loop": {"type": cfg.loop.type, "supply_temp_c": cfg.loop.supply_temp_c.value,
                     "return_temp_c": cfg.loop.return_temp_c.value, "sells_cooling": cfg.loop.sells_cooling},
            "storage": [{"id": s.id, "type": s.type, "capacity_mwh": s.capacity_mwh.value} for s in cfg.storage],
            "parties": [{"id": p.id, "name": p.name, "role": p.role} for p in cfg.parties],
            "electricity_usd_per_mwh": cfg.prices.electricity_usd_per_mwh.value,
            "carbon_price_usd_per_t": cfg.policy["carbon_price_usd_per_t"].value if "carbon_price_usd_per_t" in cfg.policy else None,
        },
        "buildings": buildings,
        "plan": details.plan,
        "plan_summary": {"budget_kw": details.budget_kw, "used_kw": details.used_kw, "order": details.order},
        "evals": {k: v.to_dict() for k, v in details.evals.items()},
        "pipes": pipe_polylines(site, connected),
        "tree": tree.to_json(),
        "explanations": {b.id: explain_building(site, b.id) for b in buildings},
        "scenarios": [{"name": s.name, "label": s.label, "hours": s.hours, "severity": s.severity} for s in scenarios_for(site)],
        "site_scores": site_scores(),
        "deal": deal,
        "annual": {"summary": yr.summary, "impact": impact(year, yr), "parties": parties,
                   "sankey": led.sankey(), "capex": led.capex},
        "model_cards": providers.model_cards(),
    })


class LiveRun:
    """One running simulation plus the state needed to build frames."""

    def __init__(self, run_id: str, site: SiteId, start: datetime | None = None, hours: int = DEFAULT_HOURS,
                 autopilot: bool = False, speed: float = 10.0):
        self.run_id, self.site = run_id, site
        self.sim = Simulation(site, start=start or DEFAULT_START[site], hours=hours,
                              autopilot="mpc" if autopilot else "rules", run_id=run_id)
        self.cfg = self.sim.cfg
        self.speed = speed
        self.paused = False
        self.deal = deal_report(site)
        self.confidence: dict | None = None
        self.conf_hour = -10**9
        self.conf_dirty = True
        self.conf_lock = threading.Lock()
        self.party_totals = {p.id: 0.0 for p in self.cfg.parties}
        self.refunds = {bid: 0.0 for bid in self.sim.plan.guaranteed_ids()}
        self.missed = {bid: 0 for bid in self.sim.plan.guaranteed_ids()}
        self.sankey_window: deque = deque(maxlen=24)
        self.running = {"heat_mwh": 0.0, "co2_t": 0.0, "water_m3": 0.0, "backup_mwh": 0.0, "cooling_mwh": 0.0}
        self.history: deque = deque(maxlen=200)

    # ------------------------------------------------------------------ confidence

    def needs_confidence(self) -> bool:
        return self.conf_dirty or self.sim.h - self.conf_hour >= CONFIDENCE_EVERY_H

    def compute_confidence(self) -> None:
        """Blocking (run in a thread). Monte Carlo through the engine, branching from this run."""
        if not self.conf_lock.acquire(blocking=False):
            return
        try:
            state = self.sim.state()
            hour = self.sim.h
            res = providers.get_confidence(self.site, self.sim.plan, state, LIVE_FUTURES)
            self.confidence = _jsonable(res)
            self.conf_hour = hour
            self.conf_dirty = False
        finally:
            self.conf_lock.release()

    def mark_dirty(self) -> None:
        self.conf_dirty = True

    # ------------------------------------------------------------------ frames

    def step_frame(self) -> dict:
        sim = self.sim
        sim.step()
        h = sim.h - 1
        frame = sim.frame()
        money = hour_money(sim, h, self.deal.terms, self.deal.premiums_usd)
        for p, v in money["party_net"].items():
            self.party_totals[p] = self.party_totals.get(p, 0.0) + v
        for bid, v in money["refunds_by_building"].items():
            self.refunds[bid] += v
        self.sankey_window.append(money["links"])
        agg: dict[tuple, float] = {}
        for links in self.sankey_window:
            for l in links:
                k = (l["source"], l["target"], l["label"])
                agg[k] = agg.get(k, 0.0) + l["value_usd"]
        names = {p.id: p.name for p in self.cfg.parties}
        names.update({"grid": "Electric grid", "fuel": "Fuel / steam suppliers", "lenders": "Lenders",
                      "town": "Town of Lansing"})
        nodes = sorted({n for k in agg for n in k[:2]})

        b_by_id = {b["id"]: b for b in frame["buildings"]}
        for bid in self.missed:
            if b_by_id.get(bid, {}).get("backup_on"):
                self.missed[bid] += 1

        state = sim.state()
        jev = providers.get_jev_opinion(state)
        conf = self.confidence
        p_mc = conf["p_all_warm"] if conf else None
        guarantees = []
        for b in sim.net.buildings:
            if b.id in self.refunds:
                guarantees.append({
                    "building_id": b.id, "name": b.name,
                    "premium_usd_per_yr": round(self.deal.premiums_usd.get(b.id, 0.0), 2),
                    "p_kept": conf["p_guarantee_kept"].get(b.id) if conf else None,
                    "missed_hours": self.missed[b.id], "refunds_owed_usd": round(self.refunds[b.id], 2),
                    "on_backup": bool(b_by_id.get(b.id, {}).get("backup_on"))})

        r = sim._rec[h]
        send = float(sim.der.send_kw[h].sum())
        usable = sum(max(s - u.min_kwh, 0.0) for u, s in zip(sim.net.storages, r.soc_kwh))
        dis_cap = sum(u.max_discharge_kw for u in sim.net.storages)
        margin_pct = (r.supply_kw + r.cooling_in_kw + dis_cap - send) / max(send, 1.0) * 100
        flows = sim._flows(slice(h, h + 1))
        grid = self.cfg.emissions.grid_t_per_mwh.value
        elec = float(flows.hp_elec_kw.sum() + flows.pump_kw.sum()) / 1000
        displaced = float((flows.delivered_kw[0] / sim.net.backup_eff * sim.net.fuel_t_per_mwh).sum()) / 1000
        chiller = r.dc_used_kw / self.cfg.data_center.chiller_cop.value / 1000
        self.running["heat_mwh"] += float(flows.delivered_kw.sum()) / 1000
        self.running["backup_mwh"] += float(flows.backup_kw.sum()) / 1000
        self.running["co2_t"] += displaced - elec * grid + chiller * grid
        self.running["cooling_mwh"] += float(frame["cooling_sold_kw"]) / 1000
        if self.cfg.data_center.cooling_towers:
            tower = r.dc_used_kw / 1000 * 3600 / LATENT_HEAT_MJ_PER_KG / 1000 / EVAPORATION_SHARE_OF_TOWER_WATER
            self.running["water_m3"] += tower - elec * 1000 * self.cfg.emissions.water_gal_per_kwh_electricity.value * GAL_TO_M3

        frame.update({
            "type": "frame", "run_id": self.run_id, "site": self.site, "hours_total": sim.hours,
            "speed": self.speed, "paused": self.paused, "active_scenarios": list(sim.active_scenarios),
            "demand_kw": round(float(sim.inp.demand_kw[h].sum()), 1),
            "network_kw": round(float(flows.delivered_kw.sum()), 1), "backup_kw": round(float(flows.backup_kw.sum()), 1),
            "money": {"hour": money["links"], "party_net_hour": money["party_net"],
                      "party_totals": {k: round(v, 2) for k, v in self.party_totals.items()},
                      "sankey_24h": {"nodes": [{"id": n, "name": names.get(n, n)} for n in nodes],
                                     "links": [{"source": s, "target": t, "label": lab, "value_usd": round(v, 2)}
                                               for (s, t, lab), v in agg.items() if v > 0.5]}},
            "confidence": conf,
            "confidence_hour": self.conf_hour if conf else None,
            "jev": _jsonable(jev) if jev.available else None,
            "verdict": verdict(p_mc, jev) if p_mc is not None else None,
            "guarantees": guarantees,
            "margin": {"supply_margin_pct": round(margin_pct, 1),
                       "storage_cover_h": round(usable / max(send, 1.0), 1),
                       "storage_usable_mwh": round(usable / 1000, 1)},
            "impact_running": {k: round(v, 2) for k, v in self.running.items()},
        })
        frame = _jsonable(frame)
        self.history.append(frame)
        return frame

    def hello(self) -> dict:
        return {"type": "hello", "run_id": self.run_id, "site": self.site, "hours_total": self.sim.hours,
                "start": self.sim.inp.times[0].isoformat(), "autopilot": self.sim.policy.name, "speed": self.speed,
                "history": list(self.history)[-72:]}
