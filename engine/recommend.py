"""Recommendation engine: which buildings connect, with which existing
equipment, in what order.

For every building and every feasible option HeatOS computes a 20-year NPV
at the site discount rate (6%) from the whole-system view:

    annual savings = current heat cost avoided
                   - heat pump electricity
                   + data center chiller electricity avoided (heat taken from the data center / chiller COP)
    NPV (financial) = -capex - pipe + savings x annuity
    NPV (value)     = NPV (financial) + avoided tCO2 x carbon price x annuity

The option with the highest value NPV wins; a building connects only if that
NPV > 0 and the data center has the capacity. Buildings are added greedily,
like growing a tree from the data center: each step's pipe is the distance to
the nearest point already on the network, and the next building is the one
with the highest priority = NPV x (1 + equity) x boiler urgency (1.5 if the
boiler is over 20 years old). Phases 1-3 split the connected set by that order.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Sequence

import numpy as np

from engine.config import SiteConfig, load_site
from engine.contracts import Building, ConnectionOption, Plan, PlanItem, SiteId

DESIGN_PEAK_FACTOR = 2.5          # design capacity = average load x 2.5
BOILER_URGENT_YEARS = 20


def annuity(rate: float, years: int) -> float:
    return (1 - (1 + rate) ** -years) / rate


def _cop(eta: float, t_hot: float, t_cold: float, cap: float = 8.0) -> float:
    th, tc = t_hot + 273.15, t_cold + 273.15
    return float(np.clip(eta * th / max(th - tc, 1.0), 1.0, cap))


@dataclass
class OptionEval:
    option: ConnectionOption
    feasible: bool
    capex_usd: float
    pipe_m: float
    pipe_usd: float
    cop: float | None
    hp_electricity_mwh: float
    loop_draw_mwh: float
    annual_savings_usd: float
    annual_co2_avoided_t: float
    npv_financial_usd: float
    npv_value_usd: float
    note: str = ""


@dataclass
class BuildingEval:
    building_id: str
    options: list[OptionEval]
    best: OptionEval | None

    def to_dict(self) -> dict:
        return {"building_id": self.building_id, "options": [asdict(o) for o in self.options],
                "best": asdict(self.best) if self.best else None}


def candidate_options(cfg: SiteConfig, b: Building) -> list[ConnectionOption]:
    if cfg.site.id == "lansing":
        return ["booster" if b.use_type == "home" else "direct_use"]
    opts: list[ConnectionOption] = ["loop_hp"]
    if b.street_distance_m < cfg.loop.direct_link_max_m.value:
        opts.append("direct_link")
    if b.heating_system == "steam":
        opts.append("steam_hp")
    return opts


def evaluate_option(cfg: SiteConfig, b: Building, option: ConnectionOption, pipe_m: float,
                    use_carbon: bool = True) -> OptionEval:
    cap = {k: v.value for k, v in cfg.capex.items()}
    hp = cfg.heat_pumps
    rate, years = cfg.finance.discount_rate.value, cfg.finance.horizon_years
    af = annuity(rate, years)
    elec_price = cfg.prices.electricity_usd_per_mwh.value
    grid = cfg.emissions.grid_t_per_mwh.value
    chiller = cfg.data_center.chiller_cop.value
    loop_t = cfg.loop.supply_temp_c.value
    dc_t = (cfg.data_center.supply_temp_c_min.value + cfg.data_center.supply_temp_c_max.value) / 2
    margin = cfg.loop.direct_use_margin_c.value
    heat = b.annual_heat_mwh
    kw = heat * 1000 / 8760 * DESIGN_PEAK_FACTOR
    steam = b.heating_system == "steam"
    dhw = {"home": 0.20}.get(b.use_type, 0.2)

    station = 0.0
    if "transfer_station_anchor_usd" in cap:
        station = cap["transfer_station_anchor_usd"] * (b.floor_area_m2 / cap["transfer_station_ref_m2"]) ** cap["transfer_station_scale_exp"]
    conversion = b.floor_area_m2 * cap.get("hydronic_conversion_usd_per_m2", 0.0) if steam else 0.0
    note = ""

    if option == "direct_link":
        pipe_m = b.street_distance_m
        direct = b.required_supply_temp_c <= dc_t - margin
        cop = None if direct else _cop(hp.eta.value, b.required_supply_temp_c, dc_t)
        capex = cap["direct_link_usd"] + station + conversion + (0 if direct else kw * cap["heat_pump_usd_per_kw"])
        hp_share = 0.0 if direct else 1.0
        note = "dedicated pipe to the data center's warmer water"
    elif option == "loop_hp":
        cop = _cop(hp.eta.value, b.required_supply_temp_c, loop_t)
        capex = station + conversion + kw * cap["heat_pump_usd_per_kw"]
        hp_share = 1.0
        if steam:
            note = "includes converting steam radiators to hot water"
    elif option == "steam_hp":
        cop = _cop(hp.steam_hp_eta.value, hp.steam_hp_t_hot_c.value, loop_t)
        capex = station + kw * cap["steam_hp_usd_per_kw"]
        hp_share = 1.0
        note = "keeps existing steam radiators"
    elif option == "direct_use":
        direct = b.required_supply_temp_c <= loop_t - margin
        cop = None if direct else _cop(hp.eta.value, b.required_supply_temp_c, loop_t)
        capex = kw * cap.get("direct_use_station_usd_per_kw", 0.0) + (0 if direct else kw * cap["heat_pump_usd_per_kw"])
        hp_share = 0.0 if direct else 1.0
    elif option == "booster":
        cop = _cop(hp.eta.value, hp.booster_t_hot_c.value, loop_t)
        capex = kw * cap.get("direct_use_station_usd_per_kw", 0.0) + kw * dhw * cap.get("booster_usd_per_kw", 0.0)
        hp_share = dhw
        note = "warm loop heats the home directly; booster lifts hot tap water to 60 C"
    else:
        raise ValueError(option)

    elec = heat * hp_share / cop if cop else 0.0
    draw = heat - elec
    loss = cfg.loop.pipe_loss_per_km.value * pipe_m / 1000
    if cfg.loop.type == "ambient_two_way":
        loss *= max(loop_t - cfg.loop.ambient_loss_ref_c, 0) / cfg.loop.ambient_loss_span_c
    draw *= 1 + loss
    eff = (cfg.backup_efficiency.get(b.heating_system) or cfg.backup_efficiency["unknown"]).value
    fuel_t = cfg.emissions.fuels_t_per_mwh.get(b.heating_system)
    fuel_t = fuel_t.value if fuel_t else 0.181

    savings = heat * b.current_heat_cost_usd_per_mwh - elec * elec_price + draw / chiller * elec_price
    co2 = heat / eff * fuel_t - elec * grid + draw / chiller * grid
    pipe_usd = pipe_m * cap["pipe_usd_per_m"]
    npv_fin = -capex - pipe_usd + savings * af
    carbon_price = cfg.policy["carbon_price_usd_per_t"].value if "carbon_price_usd_per_t" in cfg.policy else 0.0
    npv_value = npv_fin + (co2 * carbon_price * af if use_carbon else 0.0)
    return OptionEval(option=option, feasible=True, capex_usd=round(capex, 2), pipe_m=round(pipe_m, 1),
                      pipe_usd=round(pipe_usd, 2), cop=round(cop, 3) if cop else None,
                      hp_electricity_mwh=round(elec, 1), loop_draw_mwh=round(draw, 1),
                      annual_savings_usd=round(savings, 2), annual_co2_avoided_t=round(co2, 2),
                      npv_financial_usd=round(npv_fin, 2), npv_value_usd=round(npv_value, 2), note=note)


def evaluate_building(cfg: SiteConfig, b: Building, pipe_m: float | None = None,
                      use_carbon: bool = True) -> BuildingEval:
    pipe_m = b.street_distance_m if pipe_m is None else pipe_m
    opts = [evaluate_option(cfg, b, o, pipe_m, use_carbon) for o in candidate_options(cfg, b)]
    best = max(opts, key=lambda o: o.npv_value_usd)
    return BuildingEval(b.id, opts, best if best.npv_value_usd > 0 else None)


def _metric(site: str, ax: float, ay: float, bx: float, by: float) -> float:
    if site == "chelsea":
        return abs(ax - bx) + abs(ay - by)
    return float(np.hypot(ax - bx, ay - by)) * 1.05


FIRM_SHARE = 0.85      # plan design-peak loop draw against 85% of typical recoverable heat; storage is buffer, not capacity


def supply_budget_kw(cfg: SiteConfig) -> float:
    """Firm capacity for sizing: typical recoverable heat (capacity x capture x 0.85 load) x FIRM_SHARE.
    Sized so an ordinary winter runs on network heat and storage covers cold snaps."""
    dc = cfg.data_center
    return dc.capacity_mw_th.value * 1000 * dc.capture_fraction.value * 0.85 * FIRM_SHARE


def is_guaranteed(cfg: SiteConfig, b: Building) -> bool:
    return b.use_type in cfg.guarantee_use_types or b.id in cfg.guarantee_building_ids


def priority(b: Building, npv: float) -> float:
    urgency = 1.5 if (b.boiler_age_years or 0) > BOILER_URGENT_YEARS else 1.0
    return npv * (1 + b.equity_score) * urgency


@dataclass
class PlanDetails:
    plan: Plan
    evals: dict[str, BuildingEval]
    order: list[str]
    budget_kw: float
    used_kw: float

    def to_dict(self) -> dict:
        return {"plan": self.plan.model_dump(), "evals": {k: v.to_dict() for k, v in self.evals.items()},
                "order": self.order, "budget_kw": round(self.budget_kw, 1), "used_kw": round(self.used_kw, 1)}


def build_plan(site: SiteId, buildings: Sequence[Building], use_carbon: bool | None = None) -> PlanDetails:
    cfg = load_site(site)
    if use_carbon is None:
        use_carbon = cfg.plan_objective == "value"
    budget = supply_budget_kw(cfg)
    nodes: list[tuple[float, float]] = []          # connected building positions (data center handled separately)
    remaining = {b.id: b for b in buildings}
    evals: dict[str, BuildingEval] = {}
    chosen: dict[str, tuple[OptionEval, float]] = {}
    order: list[str] = []
    capacity_blocked: set[str] = set()
    used = 0.0

    while remaining:
        best = None
        for b in remaining.values():
            pipe = b.street_distance_m
            for (x, y) in nodes:
                pipe = min(pipe, _metric(site, b.x_m, b.y_m, x, y) + 20)
            ev = evaluate_building(cfg, b, pipe, use_carbon)
            evals[b.id] = ev
            if ev.best is None:
                continue
            draw_kw = b.annual_heat_mwh * 1000 / 8760 * DESIGN_PEAK_FACTOR * ev.best.loop_draw_mwh / max(b.annual_heat_mwh, 1e-9)
            if used + draw_kw > budget:
                capacity_blocked.add(b.id)
                continue
            score = priority(b, ev.best.npv_value_usd) + (1e12 if is_guaranteed(cfg, b) else 0.0)
            if best is None or score > best[0]:
                best = (score, b, ev, draw_kw)
        if best is None:
            break
        _, b, ev, draw_kw = best
        used += draw_kw
        chosen[b.id] = (ev.best, priority(b, ev.best.npv_value_usd))
        order.append(b.id)
        nodes.append((b.x_m, b.y_m))
        capacity_blocked.discard(b.id)
        del remaining[b.id]

    n = len(order)
    by_priority = sorted(order, key=lambda bid: -chosen[bid][1])
    phase = {bid: 1 + min(2, i * 3 // max(n, 1)) for i, bid in enumerate(by_priority)}
    items = []
    for b in buildings:
        ev = evals[b.id]
        if b.id in chosen:
            opt = chosen[b.id][0]
            reasons = [f"best_option:{opt.option}"]
            if b.heating_system == "steam":
                reasons.append("on_steam")
            if opt.option == "direct_link":
                reasons.append("near_data_center")
            if b.equity_score >= 0.7:
                reasons.append("high_equity")
            if (b.boiler_age_years or 0) > BOILER_URGENT_YEARS:
                reasons.append("boiler_urgent")
            if opt.npv_financial_usd < 0 < opt.npv_value_usd:
                reasons.append("carbon_value_tips_npv")
            items.append(PlanItem(
                building_id=b.id, option=opt.option, connect=True, npv_usd=opt.npv_value_usd, phase=phase[b.id],
                guaranteed=is_guaranteed(cfg, b),
                design_capacity_kw=round(b.annual_heat_mwh * 1000 / 8760 * DESIGN_PEAK_FACTOR, 1),
                reason_codes=reasons))
        else:
            best_opt = max(ev.options, key=lambda o: o.npv_value_usd)
            reason = "capacity_limit" if b.id in capacity_blocked or (ev.best and b.id in remaining) else "npv_negative"
            items.append(PlanItem(building_id=b.id, option="not_connected", connect=False,
                                  npv_usd=best_opt.npv_value_usd, reason_codes=[reason, f"best_option:{best_opt.option}"]))
    plan = Plan(site=site, items=items, created_by="recommend.build_plan")
    return PlanDetails(plan=plan, evals=evals, order=order, budget_kw=budget, used_kw=used)


@lru_cache(maxsize=8)
def _cached_details(site: SiteId, fingerprint: tuple) -> PlanDetails:
    """Cached per building-inventory fingerprint, so swapping in real data rebuilds the plan."""
    from engine import providers
    return build_plan(site, providers.get_buildings(site))


def plan_details(site: SiteId) -> PlanDetails:
    from engine import providers
    key = tuple((b.id, b.annual_heat_mwh, b.street_distance_m) for b in providers.get_buildings(site))
    return _cached_details(site, key)


def recommend_plan(site: SiteId) -> Plan:
    return plan_details(site).plan


def quick_plan(site: SiteId, buildings: Sequence[Building]) -> Plan:
    """Simple capacity-aware heuristic (kept for tests and comparisons):
    guarantee buyers first, then by equity and distance, until the supply budget is used."""
    cfg = load_site(site)
    max_m = 650.0 if site == "chelsea" else 4500.0
    budget_kw = supply_budget_kw(cfg)
    loop_draw_share = 0.75 if cfg.loop.type == "ambient_two_way" else 1.0
    order = sorted(buildings, key=lambda b: (not is_guaranteed(cfg, b), -b.equity_score, b.street_distance_m))
    used_kw = 0.0
    chosen: dict[str, PlanItem] = {}
    for b in order:
        peak_kw = b.annual_heat_mwh * 1000 / 8760 * DESIGN_PEAK_FACTOR
        reason = ("too_far" if b.street_distance_m > max_m
                  else "capacity" if used_kw + peak_kw * loop_draw_share > budget_kw else None)
        if reason:
            chosen[b.id] = PlanItem(building_id=b.id, option="not_connected", connect=False,
                                    npv_usd=0.0, reason_codes=[reason])
            continue
        used_kw += peak_kw * loop_draw_share
        if site == "lansing":
            option = "booster" if b.use_type == "home" else "direct_use"
        elif b.street_distance_m < cfg.loop.direct_link_max_m.value:
            option = "direct_link"
        elif b.heating_system == "steam":
            option = "steam_hp"
        else:
            option = "loop_hp"
        chosen[b.id] = PlanItem(
            building_id=b.id, option=option, connect=True, npv_usd=0.0, phase=1,
            guaranteed=is_guaranteed(cfg, b), design_capacity_kw=round(peak_kw, 1),
            reason_codes=["heuristic_placeholder"])
    return Plan(site=site, items=[chosen[b.id] for b in buildings], created_by="recommend.quick_plan")
