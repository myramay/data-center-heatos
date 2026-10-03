"""Sustainability impact: carbon, water, ERF/ERE, and the HDR regenerative scorecard.

Every figure is a range: central uses each parameter's configured value,
low/high sweep the grid emission factor across its configured range.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from engine.config import SiteConfig

LATENT_HEAT_MJ_PER_KG = 2.26
EVAPORATION_SHARE_OF_TOWER_WATER = 0.67     # remainder is blowdown (challenge spec)
GAL_TO_M3 = 0.003785
SANITY_T_PER_MW_YR = (790.0, 1030.0)        # challenge spec, at 60% utilization


@dataclass
class Range:
    central: float
    low: float
    high: float


def _rng(values: list[float], central: float) -> Range:
    return Range(round(central, 2), round(min(values), 2), round(max(values), 2))


def impact(sim, result) -> dict:
    cfg: SiteConfig = sim.cfg
    net, f, s = sim.net, result.flows, result.summary
    H = len(result.supply_kw)
    years = H / 8760
    grid = cfg.emissions.grid_t_per_mwh
    chiller_cop = cfg.data_center.chiller_cop.value

    delivered_mwh = f.delivered_kw.sum(0) / 1000                              # (B,)
    fuel_displaced_mwh = delivered_mwh / net.backup_eff
    displaced_t = float((fuel_displaced_mwh * net.fuel_t_per_mwh).sum())
    elec_mwh = s["hp_electricity_mwh"] + s["pump_electricity_mwh"]
    dc_used_mwh = s["dc_used_mwh"]
    chiller_mwh = dc_used_mwh / chiller_cop

    def net_t(g: float) -> float:
        return displaced_t - elec_mwh * g + chiller_mwh * g

    net_co2 = _rng([net_t(grid.low), net_t(grid.high)], net_t(grid.value))
    cap_mw = cfg.data_center.capacity_mw_th.value
    util = s["dc_utilization"]
    per_mw_yr = _rng([v / cap_mw / years for v in (net_co2.low, net_co2.high)], net_co2.central / cap_mw / years)
    band = tuple(b * util / 0.6 for b in SANITY_T_PER_MW_YR)

    by_fuel: dict[str, float] = {}
    for b, mwh in zip(net.buildings, fuel_displaced_mwh):
        by_fuel[b.heating_system] = by_fuel.get(b.heating_system, 0.0) + float(mwh)

    water = None
    if cfg.data_center.cooling_towers:
        evap_m3 = dc_used_mwh * 3600 / LATENT_HEAT_MJ_PER_KG / 1000           # kg -> m3
        tower_m3 = evap_m3 / EVAPORATION_SHARE_OF_TOWER_WATER
        indirect_m3 = elec_mwh * 1000 * cfg.emissions.water_gal_per_kwh_electricity.value * GAL_TO_M3
        water = {"evaporation_avoided_m3": round(evap_m3), "tower_water_avoided_m3": round(tower_m3),
                 "indirect_water_added_m3": round(indirect_m3), "net_water_saved_m3": round(tower_m3 - indirect_m3),
                 "net_water_saved_gal": round((tower_m3 - indirect_m3) / GAL_TO_M3)}

    it_mwh = cfg.data_center.it_load_mw.value * H
    reused = dc_used_mwh
    erf = reused / it_mwh
    ere = (it_mwh * cfg.data_center.pue.value - reused) / it_mwh

    metrics = {
        "net_reduction_share": net_co2.central / max(s["counterfactual_emissions_t"], 1e-9),
        "combustion_displaced_share": float(delivered_mwh.sum() * 1000 / max(sim.inp.demand_kw[:H].sum(), 1e-9)),
        "public_housing_heat_share": float(sum(m for b, m in zip(net.buildings, delivered_mwh) if b.use_type == "public_housing")
                                           / max(delivered_mwh.sum(), 1e-9)),
        "guarantees": int(net.guaranteed.sum()),
        "net_water_saved_m3": water["net_water_saved_m3"] if water else 0.0,
    }
    return {
        "hours": H,
        "heat_delivered_mwh": round(float(delivered_mwh.sum()), 1),
        "fuel_displaced_mwh": round(float(fuel_displaced_mwh.sum()), 1),
        "fuel_displaced_by_type_mwh": {k: round(v, 1) for k, v in sorted(by_fuel.items())},
        "co2_displaced_fuel_t": round(displaced_t, 1),
        "co2_added_electricity_t": asdict(_rng([elec_mwh * grid.low, elec_mwh * grid.high], elec_mwh * grid.value)),
        "co2_avoided_chillers_t": asdict(_rng([chiller_mwh * grid.low, chiller_mwh * grid.high], chiller_mwh * grid.value)),
        "net_co2_avoided_t": asdict(net_co2),
        "net_co2_t_per_mw_yr": asdict(per_mw_yr),
        "sanity_band_t_per_mw_yr": [round(band[0]), round(band[1])],
        "sanity_ok": band[0] * 0.75 <= per_mw_yr.central <= band[1] * 1.25,
        "water": water,
        "erf": round(erf, 4),
        "ere": round(ere, 4),
        "scorecard": hdr_scorecard(sim.site, metrics),
        "sources": {
            "gas CO2 0.181 t/MWh, propane 0.215, oil 0.25": "challenge spec",
            "grid factor range": grid.source,
            "chiller COP": cfg.data_center.chiller_cop.source,
            "latent heat 2.26 MJ/kg; evaporation = 67% of tower water; 2 gal/kWh": "challenge spec",
        },
    }


# ===================================================================== HDR regenerative scorecard

CATEGORIES = ["Community", "Human Health", "Air", "Carbon", "Water", "Nutrients", "Biodiversity"]

BASELINE = {
    "chelsea": {
        "Community": (-1, "poverty 83rd percentile, social vulnerability 79th (HDR site packet)"),
        "Human Health": (-2, "PM2.5 92nd pct, ozone 83rd pct; days over 90 F rising 19 -> 69 by 2050 (HDR site packet)"),
        "Air": (-2, "block-west PM2.5 92nd percentile, ozone 83rd (HDR site packet)"),
        "Carbon": (-1, "fossil boilers and district steam; grid 0.25-0.442 t/MWh"),
        "Water": (-1, "cooling-tower evaporation; 500-year floodplain one block away (HDR site packet)"),
        "Nutrients": (0, "no nutrient loading pathway identified"),
        "Biodiversity": (-1, "dense urban fabric; noise 56 dB vs 43.9 dB baseline (HDR site packet)"),
    },
    "lansing": {
        "Community": (0, "no disadvantaged communities nearby (HDR site packet)"),
        "Human Health": (0, "rural; no acute exposure flagged"),
        "Air": (1, "clean air (HDR site packet)"),
        "Carbon": (-1, "propane / oil heating; large new data center load"),
        "Water": (0, "lake-adjacent; liquid cooling, no towers"),
        "Nutrients": (-1, "Cayuga Lake phosphorus-impaired (HDR site packet)"),
        "Biodiversity": (-1, "former coal plant and coal yard (brownfield)"),
    },
}


def hdr_scorecard(site: str, m: dict) -> list[dict]:
    """Baseline vs with-HeatOS, -2 (degenerative) .. +2 (regenerative).
    Uplift rules are transparent thresholds on modelled metrics (assumption - verify)."""
    rows = []
    for cat in CATEGORIES:
        base, basis = BASELINE[site][cat]
        score, why = base, "no material change modelled"
        if cat == "Carbon":
            r = m["net_reduction_share"]
            mapped = 2 if r >= 0.75 else 1 if r >= 0.4 else 0 if r >= 0.1 else base
            score, why = max(base, mapped), (f"net CO2 avoided = {r:.0%} of connected buildings' existing heating "
                                             "emissions (incl. avoided data center cooling)")
        elif cat in ("Air", "Human Health"):
            c = m["combustion_displaced_share"]
            up = (2 if base < 0 else 1) if c >= 0.9 else 1 if c >= 0.5 else 0
            score, why = base + up, f"{c:.0%} of connected buildings' heat no longer burned on site"
        elif cat == "Community":
            if site == "lansing":
                up = 1 if m["guarantees"] else 0
                why = f"{m['guarantees']} guaranteed public facilities (schools / community center)"
            else:
                up = (1 if m["public_housing_heat_share"] >= 0.2 else 0) + (1 if m["guarantees"] else 0)
                why = (f"{m['public_housing_heat_share']:.0%} of network heat to public housing; "
                       f"{m['guarantees']} guaranteed buildings")
            score = base + up
        elif cat == "Water" and m["net_water_saved_m3"] > 0:
            score, why = base + 1, f"{m['net_water_saved_m3']:,.0f} m3 of cooling-tower water saved (net)"
        elif cat == "Nutrients" and site == "lansing":
            score, why = base + 1, "greenhouse + aquaculture nutrient recovery assumed (assumption - verify)"
        elif cat == "Biodiversity" and site == "lansing":
            score, why = base + 1, "pit storage on the remediated coal yard (assumption - verify)"
        rows.append({"category": cat, "baseline": base, "with_heatos": int(np.clip(score, -2, 2)),
                     "baseline_basis": basis, "change_basis": why})
    return rows
