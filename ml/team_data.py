"""The team pipeline's outputs, plugged into the HeatOS contracts.

Reads `out/plan.json` / `out/plan_lansing.json` (written by the team's
optimize.py from real LL84 / PLUTO / parcel data) and
`data/infrastructure/infrastructure_candidates.geojson` (verified Site 1 assets).

- TeamBuildingProvider: the real candidate inventory as `Building` rows. Fields
  the team file only carries for chosen buildings (heat demand, fuel price) are
  estimated for the rest and the row is flagged is_estimated.
- team_overlay(site): the team plan (chosen buildings, street-routed pipes,
  totals, ledger, Monte Carlo robustness) and the infrastructure assets, in the
  control room's local coordinates, for the 3D overlay and the Team view.

ML_TEAM_INTEGRATION: selected in engine/providers.py (HEATOS_BUILDINGS=team).
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from engine.config import load_site
from engine.contracts import Building, ModelCard, SiteId

ROOT = Path(__file__).resolve().parents[1]
PLAN = {"chelsea": ROOT / "out" / "plan.json", "lansing": ROOT / "out" / "plan_lansing.json"}
INFRA = ROOT / "data" / "infrastructure" / "infrastructure_candidates.geojson"
INTENSITY_KWH_M2 = {"residential": 140, "public_housing": 160, "office": 95, "retail": 85, "food": 260,
                    "clinic": 210, "school": 120, "greenhouse": 350, "aquaculture": 450, "home": 150}
DC_HALF = {"chelsea": (140.0, 40.0), "lansing": (60.0, 60.0)}


def available(site: SiteId = "chelsea") -> bool:
    return PLAN[site].exists()


@lru_cache(maxsize=4)
def _plan(site: SiteId) -> dict:
    return json.loads(PLAN[site].read_text())


def _local(site: SiteId, lat: float, lon: float) -> tuple[float, float, float, float]:
    """(east, north, grid_x, grid_y) metres from the site origin."""
    s = load_site(site).site
    e = (lon - s.lon) * 111_320 * math.cos(math.radians(s.lat))
    n = (lat - s.lat) * 111_320
    r = math.radians(s.grid_rotation_deg)
    return e, n, e * math.cos(r) - n * math.sin(r), e * math.sin(r) + n * math.cos(r)


def _use_type(b: dict) -> str:
    t = (b.get("property_type") or "").lower()
    name = (b.get("address") or "").lower()
    if "fish" in name or "aquaculture" in t or "aquaculture" in name:
        return "aquaculture"
    if "greenhouse" in t:
        return "greenhouse"
    if "home" in t:
        return "home"
    if "multifamily" in t or "residence" in t or "housing" in t:
        return "public_housing" if b.get("public_housing") else "residential"
    if "hotel" in t:
        return "residential"
    if "school" in t or "college" in t or "university" in t:
        return "school"
    if "hospital" in t or "medical" in t or "clinic" in t or "urgent" in t:
        return "clinic"
    if "restaurant" in t or "food" in t or "supermarket" in t or "grocery" in t:
        return "food"
    if "retail" in t or "store" in t or "mall" in t:
        return "retail"
    return "office"


def _heating(site: SiteId, use: str, year: int | None) -> str:
    # ESTIMATE: the team file has no fuel for non-chosen buildings.
    if site == "lansing":
        return "propane"
    if use in ("office", "retail", "residential") and (year or 1960) < 1945:
        return "steam"                       # older Manhattan commercial buildings are often on Con Ed steam
    return "gas_boiler"


def _required_temp(site: SiteId, use: str, heating: str, year: int | None) -> float:
    if site == "lansing":
        return {"greenhouse": 40.0, "aquaculture": 28.0}.get(use, 45.0)
    if heating == "steam":
        return 110.0
    if use in ("food", "clinic"):
        return 70.0
    y = year or 1960
    return 75.0 if y < 1980 else 60.0 if y < 2005 else 50.0


def _street_distance(site: SiteId, gx: float, gy: float, footprint: float) -> float:
    hx, hy = DC_HALF[site]
    half = math.sqrt(max(footprint, 1.0)) / 2
    if site == "chelsea":
        return round(max(0, abs(gx) - hx - half) + max(0, abs(gy) - hy - half) + 20, 1)
    return round(math.hypot(gx, gy) * 1.05 + 30, 1)


def _building(site: SiteId, b: dict) -> Building:
    cfg = load_site(site)
    use = _use_type(b)
    year = int(b["year_built"]) if b.get("year_built") else None
    heat_sys = _heating(site, use, year)
    floor = max(float(b.get("floor_area_ft2") or 0) * 0.0929, 150.0)
    h = float(b.get("height_m") or (12 if site == "chelsea" else 6))
    footprint = floor / max(1, round(h / 3.4)) if use != "home" else floor
    e, n, gx, gy = _local(site, b["lat"], b["lon"])
    eff = (cfg.backup_efficiency.get(heat_sys) or cfg.backup_efficiency["unknown"]).value
    fuel = cfg.prices.fuels_usd_per_mwh.get(heat_sys) or cfg.prices.fuels_usd_per_mwh["gas_boiler"]
    chosen = bool(b.get("chosen"))
    heat = float(b["heat_demand_mwh_year"]) if chosen and b.get("heat_demand_mwh_year") else floor * INTENSITY_KWH_M2[use] / 1000
    cost = float(b["price_now_usd_per_mmbtu"]) * 3.412 if chosen and b.get("price_now_usd_per_mmbtu") else fuel.value / eff + 8.0
    equity = 0.95 if b.get("public_housing") else min(1.0, max(0.05, float(b.get("dac_percentile") or 0) / 100))
    measured = b.get("data_quality") == "measured"
    prefix = "CH" if site == "chelsea" else "LA"
    notes = [f"BBL {b['bbl']}", b.get("property_type") or ""]
    if not (chosen and b.get("heat_demand_mwh_year")):
        notes.append("heat estimated from floor area")
    notes.append("fuel estimated" if not chosen else "team model customer")
    if b.get("dac"):
        notes.append("disadvantaged community")
    if b.get("proposed"):
        notes.append("proposed (does not exist yet)")
    return Building(
        id=f"{prefix}-{b['bbl']}", name=(b.get("address") or str(b["bbl"])).title(), site=site,
        lat=round(b["lat"], 6), lon=round(b["lon"], 6), x_m=round(gx, 1), y_m=round(gy, 1),
        street_distance_m=_street_distance(site, gx, gy, footprint), height_m=round(max(h, 3.0), 1),
        footprint_m2=round(max(footprint, 50.0), 1), floor_area_m2=round(floor, 1), use_type=use, year_built=year,
        heating_system=heat_sys, annual_heat_mwh=round(max(heat, 1.0), 1),
        current_heat_cost_usd_per_mwh=round(cost, 2),
        required_supply_temp_c=_required_temp(site, use, heat_sys, year),
        boiler_age_years=None, equity_score=round(equity, 3), is_estimated=not measured,
        notes="; ".join(x for x in notes if x))


class TeamBuildingProvider:
    """Real candidate inventory from the team pipeline (out/plan*.json)."""

    def get_buildings(self, site: SiteId) -> list[Building]:
        return [_building(site, b) for b in _plan(site)["buildings"] if b.get("lat") and b.get("lon")]

    def model_card(self) -> ModelCard:
        return ModelCard(
            name="Team building inventory", version=_plan("chelsea").get("meta", {}).get("schema_version", "0.1"),
            method="LL84 benchmarking + PLUTO (Chelsea), parcels + OpenStreetMap (Lansing), via the team's optimize.py",
            datasets=["NYC LL84 annual benchmarking", "NYC PLUTO", "Tompkins County parcels", "OpenStreetMap"],
            assumptions=["non-chosen buildings: heat = floor area x use-type intensity (estimated)",
                         "non-chosen buildings: fuel estimated (pre-1945 Manhattan commercial = steam, else gas; Lansing = propane)",
                         "equity = NYS disadvantaged-community percentile; public housing = 0.95"],
            is_mock=False)


def team_overlay(site: SiteId) -> dict | None:
    """Team plan + infrastructure assets in local metres (east, north), for the UI."""
    if not available(site):
        return None
    p = _plan(site)
    pipes = []
    for pp in p.get("pipes", []):
        pts = [_local(site, lat, lon)[:2] for lon, lat in pp.get("coords", [])]
        if len(pts) >= 2:
            pipes.append({"kind": pp.get("kind"), "phase": pp.get("phase"), "peak_kw_th": pp.get("peak_kw_th"),
                          "length_m": pp.get("length_m"), "points": [[round(e, 1), round(n, 1)] for e, n in pts]})
    chosen = [b for b in p["buildings"] if b.get("chosen")]
    prefix = "CH" if site == "chelsea" else "LA"
    infra = []
    if INFRA.exists():
        for f in json.loads(INFRA.read_text()).get("features", []):
            pr = f.get("properties", {})
            if pr.get("site_id") != site or pr.get("latitude") is None:
                continue
            e, n, _, _ = _local(site, pr["latitude"], pr["longitude"])
            infra.append({"id": pr.get("asset_id"), "name": pr.get("asset_name"), "type": pr.get("asset_type"),
                          "role": pr.get("role"), "east": round(e, 1), "north": round(n, 1),
                          "distance_m": pr.get("distance_m"), "operator": pr.get("operator_if_known"),
                          "status": pr.get("existing_or_proposed"), "confidence": pr.get("confidence"),
                          "note": pr.get("evidence_note"), "source": pr.get("source_url"),
                          "transport": pr.get("candidate_transport_types")})
    methods_csv = ROOT / "data" / "infrastructure" / "transport_methods.csv"
    methods = []
    if methods_csv.exists():
        import csv
        with methods_csv.open() as fh:
            for row in csv.DictReader(fh):
                methods.append({k: row.get(k) for k in ("method_id", "method_name", "description", "requires_heat_pump",
                                                        "requires_new_pipe", "can_use_existing_network", "distance_sensitivity")})
    dc = p.get("datacenter", {})
    return {
        "site": p.get("site"), "generated_at": p.get("meta", {}).get("generated_at"),
        "datacenter": {k: dc.get(k) for k in ("name", "heat_mw_th", "usable_mw_th")},
        "chosen": [{"id": f"{prefix}-{b['bbl']}", "address": b.get("address"), "phase": b.get("phase"),
                    "tier": b.get("tier"), "heat_delivered_mwh_year": b.get("heat_delivered_mwh_year"),
                    "yearly_savings_usd": b.get("yearly_savings_usd"), "co2_avoided_t_year": b.get("co2_avoided_t_year"),
                    "fit_score": (b.get("fit") or {}).get("score")} for b in chosen],
        "pipes": pipes, "totals": p.get("totals"), "phases": p.get("phases"), "comparison": p.get("comparison"),
        "ledger": p.get("ledger"), "robustness": {k: v for k, v in (p.get("robustness") or {}).items() if k != "frequency"},
        "match_scorecard": p.get("match_scorecard"), "reliability": p.get("reliability"),
        "infrastructure": infra, "transport_methods": methods,
    }
