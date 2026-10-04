"""
HeatOS allocation: WHO gets heat first, and why. (What makes the plan more than "connect the best
buildings".)

1. HEAT FIT SCORE   how well each building matches the data center's heat, on the brief's axes:
                    temperature, capacity, timing, seasonality (continuity decides the tier).
2. SERVICE TIERS    every customer is on one rung of a ladder, priced accordingly:
                      PROTECTED  public housing: never cut, discounted heat (equity)
                      FIRM       hospitals, senior living: never cut, pays a small guarantee premium
                      BASE       year-round hot-water users: heat all year (they use the summer heat)
                      FLEX       space-heating users: cheaper heat, but curtailed first if heat is short
3. WATERFALL        each hour, the data center's heat flows down the ladder. If supply is short
                    (polar vortex, data center output drops) the lowest tiers are cut first and
                    their old boilers take over. This is how 'who gets heat' is decided in practice.
"""
import json

import numpy as np

from demand import DAYS

TIERS = ["PROTECTED", "FIRM", "BASE", "FLEX"]            # priority order, first = served first
TIER_NOTE = {
    "PROTECTED": "Public housing and affordability-priority homes. Heat is never cut. Discounted price (grants + equity).",
    "FIRM": "Hospitals and senior living. Heat is never cut. Pays a small guarantee premium.",
    "BASE": "Year-round hot-water users. Heat all year, so summer heat is used, not wasted.",
    "FLEX": "Space-heating users. Cheapest heat; curtailed first when supply is short (boiler takes over).",
}


# ------------------------------------------------------------ tiers
def assign_tier(use_mix_json, public_housing, dhw_share, cfg):
    """Rule-based tier. Thresholds are in config (tier_rules)."""
    rules = cfg["tier_rules"]
    mix = json.loads(use_mix_json) if isinstance(use_mix_json, str) else {}
    tot = sum(mix.values()) or 1.0
    critical = sum(a for u, a in mix.items() if u in rules["continuity_critical_uses"]) / tot
    if public_housing:
        return "PROTECTED"
    if critical >= rules["critical_area_share"]:
        return "FIRM"
    if dhw_share >= rules["base_hot_water_share"]:
        return "BASE"
    return "FLEX"


# ------------------------------------------------------------ fit score
def fit_scores(econ, delivered, cop_bm, cap_kw, cfg):
    """0-1 score per axis and a weighted 0-100 total, for each candidate row.
      temperature  cheap to lift: average heat pump COP (3.5 -> 0, 5.5 -> 1)
      timing       flat all day: average / peak of the delivered heat (flat suits a flat heat source)
      seasonality  uses heat all year: summer (Jun-Aug) / winter (Dec-Feb) heat delivered
      capacity     small enough to host: heat pump size vs the data center's heat (big = risky)
    """
    n = len(econ)
    w = cfg["fit_weights"]
    month_kwh = delivered.sum(axis=2) * DAYS                              # [n,12] kWh
    cop_avg = (cop_bm * month_kwh).sum(axis=1) / np.maximum(month_kwh.sum(axis=1), 1e-9)
    temp = np.clip((cop_avg - 3.5) / 2.0, 0, 1)
    peak = np.maximum(delivered.max(axis=(1, 2)), 1e-9)
    timing = np.clip(delivered.mean(axis=(1, 2)) / peak, 0, 1)
    summer = month_kwh[:, [5, 6, 7]].sum(axis=1)
    winter = month_kwh[:, [11, 0, 1]].sum(axis=1)
    season = np.clip(summer / np.maximum(winter, 1e-9), 0, 1)
    share = econ["hp_kw"].values / (float(np.mean(cap_kw)) * cfg["fit_capacity_ref_share"])
    capacity = np.clip(1 - share, 0, 1)
    total = 100 * (w["temperature"] * temp + w["timing"] * timing + w["seasonality"] * season
                   + w["capacity"] * capacity) / sum(w.values())
    return {"temperature": temp, "timing": timing, "seasonality": season, "capacity": capacity,
            "score": total, "cop_avg": cop_avg}


# ------------------------------------------------------------ the waterfall
def scenario_defs(cfg):
    """name -> (demand multiplier per month, supply multiplier, label)"""
    pv = np.ones(12)
    pv[cfg["stress_polar_months"]] = cfg["stress_polar_demand_multiplier"]
    return {"normal": (np.ones(12), 1.0, "Normal year"),
            "polar_vortex": (pv, 1.0, "Polar vortex: winter demand +%d%%" % round(100 * (pv.max() - 1))),
            "dc_down": (np.ones(12), 1 - cfg["stress_dc_loss_fraction"],
                        "Data center output -%d%%" % round(100 * cfg["stress_dc_loss_fraction"]))}


def waterfall(S, tiers, demand, hp_kw, cop_bm, cfg, cap_kw):
    """Feed the data center's heat down the tier ladder, hour by hour (12 typical days).
    S = chosen row indices, tiers = tier name per row. Returns served/unserved kW by tier per scenario."""
    loss = cfg["pipe_heat_loss_fraction"]
    out = {}
    for name, (dmult, smult, label) in scenario_defs(cfg).items():
        want = {t: np.zeros((12, 24)) for t in TIERS}                       # DC heat each tier asks for
        for i in S:
            d = np.minimum(demand[i] * dmult[:, None], hp_kw[i])           # heat pump output, kW
            want[tiers[i]] += d * (1 - 1 / cop_bm[i][:, None]) / (1 - loss)
        supply = cap_kw * smult
        left, served = supply.copy(), {}
        for t in TIERS:                                                    # the waterfall
            served[t] = np.minimum(want[t], left)
            left = left - served[t]
        yr = lambda a: float((a.sum(axis=1) * DAYS).sum())
        asked = sum(yr(want[t]) for t in TIERS)
        out[name] = {
            "label": label,
            "supply_kw": np.rint(supply[:, :]).astype(int).tolist(),
            "served_kw": {t: np.rint(served[t]).astype(int).tolist() for t in TIERS},
            "unserved_kw": {t: np.rint(want[t] - served[t]).astype(int).tolist() for t in TIERS},
            "share_served": {t: (round(yr(served[t]) / yr(want[t]), 3) if yr(want[t]) > 0 else None) for t in TIERS},
            "heat_asked_gwh": round(asked / 1e6, 2),
            "heat_served_gwh": round(sum(yr(served[t]) for t in TIERS) / 1e6, 2),
            "heat_unused_gwh": round(max(0.0, yr(supply) - sum(yr(served[t]) for t in TIERS)) / 1e6, 2),
        }
    return out
