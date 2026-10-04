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


TRANSPORT = ROOT / "outputs" / "transport" / "options_summary.csv"
ROLE_LABEL = {"utility_infrastructure": "Utility network (pilot)", "existing_heat_network": "Existing heat network",
              "thermal_plant": "Campus thermal plant", "third_party_heat_source": "Third-party heat source",
              "potential_anchor_sink": "Anchor heat buyer", "third_party_anchor": "Third-party anchor", "incumbent": "Incumbent supplier"}
# third parties named in the team's METHODOLOGY.md that are not in the Site 1 infrastructure inventory
METHODOLOGY_PARTIES = {
    "lansing": [
        {"company": "Cornell University", "role": "third_party_anchor", "assets": ["Cornell central heating plant (~30 km route)"],
         "fits_method": "17 km insulated transmission main", "note": "Team model: transmission main to a third-party anchor; "
         "not viable at today's costs (median system net about -$12M/yr).", "source_url": "METHODOLOGY.md"},
        {"company": "NYSEG", "role": "incumbent", "assets": ["Natural gas distribution (33% of homes)"],
         "fits_method": "incumbent / backup heat", "note": "Incumbent gas utility; with trucked oil and propane, the heat the network displaces.",
         "source_url": "METHODOLOGY.md"},
    ],
}


def transport_companies(site: SiteId) -> list[dict]:
    """Third parties for the heat transport, from the team's GitHub data: the reviewed infrastructure inventory
    (data/infrastructure, Site 1, grouped by operator) plus the third-party anchors in METHODOLOGY.md."""
    out: dict[str, dict] = {}
    if INFRA.exists():
        for f in json.loads(INFRA.read_text()).get("features", []):
            pr = f.get("properties", {})
            if pr.get("site_id") != site:
                continue
            op = pr.get("operator_if_known") or "unknown"
            key = op if op != "unknown" else pr.get("asset_name")
            row = out.setdefault(key, {"company": op if op != "unknown" else pr.get("asset_name"), "role": pr.get("role"),
                                       "assets": [], "fits_method": ", ".join(t.replace("_", " ") for t in (pr.get("candidate_transport_types") or [])),
                                       "note": pr.get("evidence_note"), "source_url": pr.get("source_url"),
                                       "distance_m": pr.get("distance_m"), "status": pr.get("existing_or_proposed"),
                                       "confidence": pr.get("confidence")})
            row["assets"].append(pr.get("asset_name"))
    rows = list(out.values()) + METHODOLOGY_PARTIES.get(site, [])
    for r in rows:
        r["role_label"] = ROLE_LABEL.get(r.get("role") or "", (r.get("role") or "").replace("_", " "))
    order = ["utility_infrastructure", "existing_heat_network", "third_party_anchor", "thermal_plant", "third_party_heat_source",
             "potential_anchor_sink", "incumbent"]
    return sorted(rows, key=lambda r: (order.index(r["role"]) if r.get("role") in order else 99, r.get("distance_m") or 1e9))


TRANSPORT_LABEL = {
    "4gdh_hot_water": "New hot-water network (65-70 C), central heat pump",
    "5gdh_ambient": "Ambient loop (~25 C), heat pump in each building",
    "campus_anchor": "Hot-water mains to existing campus plants only",
    "4gdh_local": "New hot-water network to nearby homes / schools",
    "5gdh_local": "Ambient loop with home heat pumps",
    "cornell_transmission": "17 km transmission main to Cornell",
    "greenhouse_anchor": "Greenhouse / fish farm next door, heat pump to 65 C",
    "greenhouse_direct": "Greenhouse heated directly by 45 C cooling water",
    "mobile_storage": "Heat-battery containers by truck",
}


def transport_options(site: SiteId) -> dict | None:
    """The team's transport-method comparison (transport_optimization.py): every method designed
    cost-optimal and carbon-weighted, each stress-tested with 1,000 Monte Carlo draws."""
    if not TRANSPORT.exists():
        return None
    import csv
    key = "site1" if site == "chelsea" else "site2"
    num = lambda v: float(v) if v not in (None, "") else None
    rows = []
    with TRANSPORT.open() as fh:
        for r in csv.DictReader(fh):
            if r["site"] != key:
                continue
            rows.append({"option": r["option"], "label": TRANSPORT_LABEL.get(r["option"], r["option"]),
                         "description": r["description"], "objective": r["objective"],
                         **{k: num(r.get(k)) for k in ("connected", "pipe_km", "heat_gwh", "dc_heat_share_p50", "capex_musd_p50",
                                                       "lcoh_p50", "tariff_p50", "net_co2_t_p10", "net_co2_t_p50",
                                                       "system_net_musd_p10", "system_net_musd_p50", "p_everyone_warm",
                                                       "p_every_party_profits", "abatement_usd_per_t_p50")}})
    ok = lambda r: (r["connected"] or 0) > 0 and r["system_net_musd_p50"] is not None
    best = {}
    for obj in ("cost-optimal", "carbon-weighted"):
        cand = [r for r in rows if r["objective"] == obj and ok(r)]
        # most efficient = highest expected system net value among designs that keep everyone warm and everyone paid
        reliable = [r for r in cand if (r["p_everyone_warm"] or 0) >= 0.9 and (r["p_every_party_profits"] or 0) >= 0.5] or cand
        if reliable:
            best[obj] = max(reliable, key=lambda r: r["system_net_musd_p50"])["option"]
    return {"options": rows, "best": best, "companies": transport_companies(site),
            "method": "each method designed twice (carbon $0 and $190/t), then 1,000 Monte Carlo draws of weather, "
                      "data-center output, demand, prices, costs and pipe failures; best = highest median system net value "
                      "among designs with P(everyone warm) >= 90% and P(every party profits) >= 50%"}
