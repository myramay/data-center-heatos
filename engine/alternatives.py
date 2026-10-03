"""Compare the recommended plan against the obvious alternatives.

For each alternative, one simulated year gives every party's 20-yr NPV, the
total system cost (energy + annualized capex) and the carbon footprint, so the
UI can show whether HeatOS's plan is the best deal for everyone.

    status_quo        keep every building's existing heating (the baseline: all NPVs 0)
    standalone_ashp   the same buildings each install their own air-source heat pump, no network
    heatos            the recommended plan (NPV + carbon, firm capacity, deal terms)
    financial_only    the plan HeatOS would pick if carbon had no value
    connect_all       connect every building the data center can physically reach
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import lru_cache

import numpy as np

from engine import providers
from engine.config import load_site
from engine.contracts import Plan, PlanItem, SiteId
from engine.ledger import DealTerms, compute_ledger, crf
from engine.recommend import DESIGN_PEAK_FACTOR, build_plan, is_guaranteed, plan_details

YEAR = datetime(2026, 1, 1)
ASHP_ETA = 0.40                 # Carnot fraction of an air-source heat pump (assumption - verify)
ASHP_USD_PER_KW = 1100.0        # installed, incl. outdoor units (assumption - verify)


@dataclass
class Alternative:
    key: str
    label: str
    description: str
    buildings: int
    heat_mwh: float
    network_share: float
    backup_hours: int
    system_cost_usd_per_yr: float          # energy + annualized capex, all parties
    co2_t_per_yr: float                    # footprint of meeting the same heat demand
    co2_avoided_t_per_yr: float            # vs status quo for the same buildings
    party_npv_usd: dict[str, float]
    everyone_ahead: bool
    min_party: str | None
    carbon_waterfall: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _status_quo(site: SiteId, ids: list[str]) -> tuple[float, float, float]:
    """(heat MWh, cost $/yr, tCO2/yr) of heating these buildings as today."""
    cfg = load_site(site)
    by = {b.id: b for b in providers.get_buildings(site)}
    heat = cost = co2 = 0.0
    for i in ids:
        b = by[i]
        eff = (cfg.backup_efficiency.get(b.heating_system) or cfg.backup_efficiency["unknown"]).value
        ft = cfg.emissions.fuels_t_per_mwh.get(b.heating_system)
        heat += b.annual_heat_mwh
        cost += b.annual_heat_mwh * b.current_heat_cost_usd_per_mwh
        co2 += b.annual_heat_mwh / eff * (ft.value if ft else 0.181)
    return heat, cost, co2


def _run_plan(site: SiteId, key: str, label: str, desc: str, plan: Plan, terms: DealTerms | None,
              premiums: dict[str, float] | None, baseline_co2: float) -> Alternative:
    from engine.sim import Simulation

    cfg = load_site(site)
    sim = Simulation(site, start=YEAR, hours=8760, plan=plan, narrate=False)
    r = sim.run()
    s = r.summary
    led = compute_ledger(sim, r, terms, premiums)
    ids = plan.connected_ids()
    _, sq_cost, sq_co2 = _status_quo(site, ids)
    capex_yr = 0.0
    for ln in led.capex:
        rate, years = ln.financing if ln.financing else (cfg.finance.discount_rate.value, cfg.finance.horizon_years)
        capex_yr += ln.usd * crf(rate, years)
    grid = cfg.emissions.grid_t_per_mwh.value
    chiller_usd = s["dc_used_mwh"] / cfg.data_center.chiller_cop.value * cfg.prices.electricity_usd_per_mwh.value
    energy = s["electricity_cost_usd"] + s["backup_fuel_cost_usd"] - chiller_usd
    chiller_t = s["dc_used_mwh"] / cfg.data_center.chiller_cop.value * grid
    elec_t = (s["hp_electricity_mwh"] + s["pump_electricity_mwh"]) * grid
    backup_t = s["network_emissions_t"] - elec_t
    footprint = s["network_emissions_t"] - chiller_t
    npv = {p: float(round(v.npv_usd, 0)) for p, v in led.parties.items()}
    worst = min(npv, key=npv.get) if npv else None
    return Alternative(
        key=key, label=label, description=desc, buildings=len(ids), heat_mwh=round(s["heat_demand_mwh"], 0),
        network_share=round(s["network_share"], 4), backup_hours=s["hours_with_backup"],
        system_cost_usd_per_yr=round(energy + capex_yr, 0), co2_t_per_yr=round(footprint, 1),
        co2_avoided_t_per_yr=round(sq_co2 - footprint, 1), party_npv_usd=npv,
        everyone_ahead=all(v >= 0 for v in npv.values()), min_party=worst,
        carbon_waterfall=[
            {"step": "Today's heating", "t": round(sq_co2, 1)},
            {"step": "Fuel no longer burned", "t": round(-(sq_co2 - backup_t), 1)},
            {"step": "Heat pump + pump electricity", "t": round(elec_t, 1)},
            {"step": "Data center chillers avoided", "t": round(-chiller_t, 1)},
            {"step": "Net footprint", "t": round(footprint, 1), "total": True},
        ],
        notes=[f"status-quo heating cost for these buildings ${sq_cost:,.0f}/yr"])


def _standalone(site: SiteId, ids: list[str]) -> Alternative:
    """Each building installs its own air-source heat pump; no network, no data center heat."""
    cfg = load_site(site)
    by = {b.id: b for b in providers.get_buildings(site)}
    weather = providers.get_weather(site, YEAR, 8760)
    t_out = np.array(weather.t_out_c)
    fc = providers.get_demand_forecast(site, ids, YEAR, 8760)
    elec_price = cfg.prices.electricity_usd_per_mwh.value
    grid = cfg.emissions.grid_t_per_mwh.value
    rate, years = cfg.finance.discount_rate.value, cfg.finance.horizon_years
    heat_mwh = elec_mwh = backup_mwh = backup_cost = backup_t = capex = 0.0
    party_npv: dict[str, float] = {}
    af = (1 - (1 + rate) ** -years) / rate
    for f in fc:
        b = by[f.building_id]
        need = np.array(f.p50)
        th, tc = b.required_supply_temp_c + 273.15, t_out + 273.15
        cop = np.clip(ASHP_ETA * th / np.maximum(th - tc, 5.0), 1.0, 5.0)
        # below -15 C, or when the lift is too large, fall back to the existing system
        ok = (t_out > -15) & (b.required_supply_temp_c <= 80)
        hp_heat = np.where(ok, need, 0.0)
        e = (hp_heat / cop).sum() / 1000
        bk = (need - hp_heat).sum() / 1000
        eff = (cfg.backup_efficiency.get(b.heating_system) or cfg.backup_efficiency["unknown"]).value
        ft = cfg.emissions.fuels_t_per_mwh.get(b.heating_system)
        kw = b.annual_heat_mwh * 1000 / 8760 * DESIGN_PEAK_FACTOR
        c = kw * ASHP_USD_PER_KW
        heat_mwh += need.sum() / 1000
        elec_mwh += e
        backup_mwh += bk
        backup_cost += bk * b.current_heat_cost_usd_per_mwh
        backup_t += bk / eff * (ft.value if ft else 0.181)
        capex += c
        saving = b.annual_heat_mwh * b.current_heat_cost_usd_per_mwh - e * elec_price - bk * b.current_heat_cost_usd_per_mwh
        p = cfg.party_by_use_type[b.use_type]
        party_npv[p] = party_npv.get(p, 0.0) + saving * af - c
    _, sq_cost, sq_co2 = _status_quo(site, ids)
    footprint = elec_mwh * grid + backup_t
    npv = {p.id: float(round(party_npv.get(p.id, 0.0), 0)) for p in cfg.parties}
    for p in cfg.parties:              # network owners / data center get nothing in this world
        npv.setdefault(p.id, 0.0)
    worst = min(npv, key=npv.get)
    return Alternative(
        key="standalone_ashp", label="Own heat pumps, no network",
        description="Each of the same buildings installs an air-source heat pump; existing system covers deep cold.",
        buildings=len(ids), heat_mwh=round(heat_mwh, 0), network_share=0.0,
        backup_hours=int((t_out <= -15).sum()),
        system_cost_usd_per_yr=round(elec_mwh * elec_price + backup_cost + capex * crf(rate, years), 0),
        co2_t_per_yr=round(footprint, 1), co2_avoided_t_per_yr=round(sq_co2 - footprint, 1),
        party_npv_usd=npv, everyone_ahead=all(v >= 0 for v in npv.values()), min_party=worst,
        carbon_waterfall=[
            {"step": "Today's heating", "t": round(sq_co2, 1)},
            {"step": "Fuel no longer burned", "t": round(-(sq_co2 - backup_t), 1)},
            {"step": "Heat pump electricity", "t": round(elec_mwh * grid, 1)},
            {"step": "Data center chillers avoided", "t": 0.0},
            {"step": "Net footprint", "t": round(footprint, 1), "total": True},
        ],
        notes=[f"air-source COP = {ASHP_ETA} x Carnot vs outdoor air (assumption - verify)",
               f"installed ${ASHP_USD_PER_KW:,.0f}/kW (assumption - verify)", "no data center heat used"])


def _connect_all_plan(site: SiteId) -> Plan:
    """Every building with any positive-value option, ignoring firm capacity."""
    cfg = load_site(site)
    d = plan_details(site)
    items = []
    for it in d.plan.items:
        ev = d.evals[it.building_id]
        best = max(ev.options, key=lambda o: o.npv_value_usd)
        b = next(x for x in providers.get_buildings(site) if x.id == it.building_id)
        reach = b.street_distance_m <= (1200 if site == "chelsea" else 6000)
        if it.connect or reach:
            items.append(PlanItem(building_id=it.building_id, option=best.option, connect=True, npv_usd=best.npv_value_usd,
                                  phase=it.phase or 3, guaranteed=is_guaranteed(cfg, b),
                                  design_capacity_kw=round(b.annual_heat_mwh * 1000 / 8760 * DESIGN_PEAK_FACTOR, 1),
                                  reason_codes=["connect_all"]))
        else:
            items.append(it)
    return Plan(site=site, items=items, created_by="alternatives.connect_all")


@lru_cache(maxsize=4)
def compare(site: SiteId) -> dict:
    from api.live import deal_report      # cached deal terms + premiums for the recommended plan

    cfg = load_site(site)
    plan = plan_details(site).plan
    ids = plan.connected_ids()
    _, sq_cost, sq_co2 = _status_quo(site, ids)
    deal = deal_report(site)
    alts = [
        Alternative(key="status_quo", label="Keep today's heating", description="Every building keeps its boiler or steam.",
                    buildings=len(ids), heat_mwh=round(_status_quo(site, ids)[0], 0), network_share=0.0, backup_hours=8760,
                    system_cost_usd_per_yr=round(sq_cost, 0), co2_t_per_yr=round(sq_co2, 1), co2_avoided_t_per_yr=0.0,
                    party_npv_usd={p.id: 0.0 for p in cfg.parties}, everyone_ahead=True, min_party=None,
                    carbon_waterfall=[{"step": "Today's heating", "t": round(sq_co2, 1), "total": True}],
                    notes=["baseline: nobody gains or loses"]),
        _standalone(site, ids),
        _run_plan(site, "heatos", "HeatOS plan", "Recommended: NPV incl. carbon, sized to firm capacity, deal terms applied.",
                  plan, deal.terms, deal.premiums_usd, sq_co2),
    ]
    fin = build_plan(site, providers.get_buildings(site), use_carbon=False).plan
    alts.append(_run_plan(site, "financial_only", "Money-only plan", "Same engine with carbon valued at $0.", fin, deal.terms, None, sq_co2))
    alts.append(_run_plan(site, "connect_all", "Connect everyone in reach", "Every reachable building, ignoring capacity.",
                          _connect_all_plan(site), deal.terms, None, sq_co2))
    # Score every alternative over the SAME buildings: anyone an alternative doesn't serve stays on today's heating.
    universe = [b.id for b in providers.get_buildings(site)]
    _, u_cost, u_co2 = _status_quo(site, universe)
    served = {"status_quo": ids, "standalone_ashp": ids, "heatos": ids, "financial_only": fin.connected_ids(),
              "connect_all": _connect_all_plan(site).connected_ids()}
    for a in alts:
        rest = [i for i in universe if i not in set(served[a.key])]
        _, r_cost, r_co2 = _status_quo(site, rest)
        if a.key == "status_quo":
            a.system_cost_usd_per_yr, a.co2_t_per_yr = round(u_cost, 0), round(u_co2, 1)
        else:
            a.system_cost_usd_per_yr = round(a.system_cost_usd_per_yr + r_cost, 0)
            a.co2_t_per_yr = round(a.co2_t_per_yr + r_co2, 1)
        a.co2_avoided_t_per_yr = round(u_co2 - a.co2_t_per_yr, 1)
        a.notes.insert(0, f"covers all {len(universe)} candidate buildings; {len(universe) - len(rest)} served, the rest keep today's heating")
        if a.carbon_waterfall and len(a.carbon_waterfall) > 1:
            a.carbon_waterfall.insert(-1, {"step": "Buildings not served", "t": round(r_co2, 1)})
            a.carbon_waterfall[-1]["t"] = a.co2_t_per_yr
            a.carbon_waterfall[0]["t"] = round(a.carbon_waterfall[0]["t"], 1)
    heatos = next(a for a in alts if a.key == "heatos")
    carbon_price = cfg.policy["carbon_price_usd_per_t"].value if "carbon_price_usd_per_t" in cfg.policy else 0.0
    for a in alts:
        a.notes.append(f"social cost incl. carbon ${a.system_cost_usd_per_yr + a.co2_t_per_yr * carbon_price:,.0f}/yr "
                       f"(carbon at ${carbon_price:,.0f}/t)")
    social = {a.key: a.system_cost_usd_per_yr + a.co2_t_per_yr * carbon_price for a in alts}
    best_social = min(social, key=social.get)
    return {
        "site": site, "carbon_price_usd_per_t": carbon_price, "universe_buildings": len(universe),
        "alternatives": [asdict(a) for a in alts],
        "social_cost_usd_per_yr": social,
        "best_social": best_social,
        "verdict": {
            "heatos_everyone_ahead": heatos.everyone_ahead,
            "heatos_lowest_social_cost": best_social == "heatos",
            "summary": ("HeatOS plan: every party ahead" if heatos.everyone_ahead else
                        f"HeatOS plan: all parties ahead except {heatos.min_party}") +
                       f"; lowest cost incl. carbon: {next(a.label for a in alts if a.key == best_social)}.",
        },
    }
