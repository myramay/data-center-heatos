"""Recommendation engine.

Build step 6 replaces quick_plan with the full option-by-option NPV + carbon
evaluation over existing, commercially available delivery options.
"""

from __future__ import annotations

from typing import Sequence

from engine.config import load_site
from engine.contracts import Building, Plan, PlanItem, SiteId


def quick_plan(site: SiteId, buildings: Sequence[Building]) -> Plan:
    """Placeholder until the full evaluation lands (build step 6).

    Greedy and capacity-aware: guarantee buyers first, then by equity and
    distance, adding buildings until their estimated peak loop draw uses up
    the data center's typical output plus half the storage discharge rate.
    """
    cfg = load_site(site)
    max_m = 650.0 if site == "chelsea" else 4500.0
    dc = cfg.data_center
    budget_kw = (dc.capacity_mw_th.value * 1000 * dc.capture_fraction.value * 0.85
                 + 0.5 * sum(s.max_discharge_mw.value for s in cfg.storage) * 1000)
    loop_draw_share = 0.75 if cfg.loop.type == "ambient_two_way" else 1.0   # ~1 - 1/COP

    def is_guaranteed(b: Building) -> bool:
        return b.use_type in cfg.guarantee_use_types or b.id in cfg.guarantee_building_ids

    order = sorted(buildings, key=lambda b: (not is_guaranteed(b), -b.equity_score, b.street_distance_m))
    used_kw = 0.0
    chosen: dict[str, PlanItem] = {}
    for b in order:
        peak_kw = b.annual_heat_mwh * 1000 / 8760 * 2.5
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
            guaranteed=is_guaranteed(b), design_capacity_kw=round(peak_kw, 1),
            reason_codes=["heuristic_placeholder"])
    return Plan(site=site, items=[chosen[b.id] for b in buildings], created_by="recommend.quick_plan")
