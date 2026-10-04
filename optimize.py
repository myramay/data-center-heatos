"""
HeatOS step 2: choose which buildings get the data center's heat, and in which phase.

Pipeline (run:  .venv/bin/python optimize.py):
  1. data        cached real buildings (data_load.py)
  2. demand      hourly curve per building, 12 typical days (demand.py)
  3. economics   per building: yearly revenue, power cost, capital, LL97 fines avoided
  4. network     one shared pipe tree along streets (network.py)
  5. MILP        pick the set of buildings with the best yearly value that the data center's
                 heat can serve in EVERY hour of the typical days (summer included)
  6. baseline    "biggest buildings first" for comparison
  7. phasing     split the chosen set into Phase 1/2/3
  8. export      out/plan.json (format described in SCHEMA.md)
"""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import LinearConstraint, Bounds, milp
from scipy.sparse import coo_matrix
from shapely import set_precision
from shapely.geometry import mapping, shape

import config
from data_load import _to_m
from sites import get_site
from demand import (DAYS, FUELS, KBTU_PER_KWH, build_demand, eff_by_fuel, pick_candidates,
                    price_by_fuel, yearly_kwh)
import realdata
_SITE = None      # the site being run (set at the top of run_plan; keeps function signatures short)
from allocate import TIER_NOTE, TIERS, assign_tier, fit_scores, waterfall
from ledger import party_ledger
from system import system_block, user_mix, why_suitable
from reliability import building_reliability, cooling_firewall, match_scorecard, pipe_serves
from estimate import fill_missing
from network import PipeTree

HERE = Path(__file__).parent
OUT = HERE / "out"
SUMMER = [5, 6, 7]          # Jun, Jul, Aug (0-based months)


def crf(rate, years):
    """Capital recovery factor: yearly payment per $ of capex."""
    return rate / (1 - (1 + rate) ** -years)


# ================================================================ real-data helpers
def apply_real_defaults(cfg, overrides, real):
    """Swap my placeholder prices for the state price table (scaled to today), unless the caller
    passed an explicit override (Monte Carlo draws win over data)."""
    ov = overrides or {}
    if "price_overrides" in real:                       # a site that brings its own current prices (Lansing)
        table = dict(real["price_overrides"])
    else:                                               # Chelsea: state price table scaled to today
        p, k = real["prices"][cfg["price_sector"]], cfg["price_inflation_since_data_year"]
        resid = p["residual_usd_per_mmbtu"] or p["distillate_usd_per_mmbtu"]
        table = {"gas_price_usd_per_mmbtu": p["gas_usd_per_mmbtu"] * k,
                 "oil2_price_usd_per_mmbtu": p["distillate_usd_per_mmbtu"] * k,
                 "oil4_price_usd_per_mmbtu": resid * k, "oil56_price_usd_per_mmbtu": resid * k,
                 "elec_price_usd_per_kwh": p["elec_usd_per_kwh"] * k}
    for key, v in table.items():
        if key not in ov:
            cfg[key] = round(v, 4)
    if "monthly_hdd" not in ov:
        cfg["monthly_hdd"] = real["weather"]["monthly_hdd"]
    if "elec_price_usd_per_kwh" in table and "elec_heat_price_usd_per_mmbtu" not in table and "elec_heat_price_usd_per_mmbtu" not in ov:
        cfg["elec_heat_price_usd_per_mmbtu"] = round(cfg["elec_price_usd_per_kwh"] / 0.0034121, 2)


def dc_supply(real, cfg):
    """How much heat the data center can give in each hour of each typical day -> [12,24] kW.
    Derived from 111 8th Ave's own LL84 electricity use unless config sets dc_heat_mw_th."""
    info = None
    if cfg["dc_heat_mw_th"] is None and cfg.get("dc_facility_mw"):
        it_mw = cfg["dc_facility_mw"] / cfg["dc_pue"]                      # facility power -> computer power
        cfg["dc_heat_mw_th"] = it_mw * cfg["dc_liquid_capture_fraction"] * cfg["dc_heat_scale"]
        info = {"method": "Planned facility power / PUE x share of server heat captured by the liquid-cooling loop",
                "facility_mw": cfg["dc_facility_mw"], "pue": cfg["dc_pue"], "it_mw": round(it_mw, 1),
                "capture_fraction": cfg["dc_liquid_capture_fraction"], "source_temp_c": cfg["dc_source_temp_c"]}
    elif cfg["dc_heat_mw_th"] is None:
        s = real["supply"]
        grid_gwh = float(np.mean(s["grid_kwh"])) / 1e6
        office_gwh = float(np.mean(s["office_gfa_ft2"])) * cfg["dc_nondc_kwh_per_ft2_office"] / 1e6
        dc_mw_elec = (grid_gwh - office_gwh) * 1000 / 8760
        cfg["dc_heat_mw_th"] = dc_mw_elec * cfg["dc_heat_capture_fraction"] * cfg["dc_heat_scale"]
        info = {"method": "LL84 grid electricity of 111 8th Ave minus office floors, x capture fraction",
                "ll84_years": s["years"], "grid_electricity_gwh_avg": round(grid_gwh, 1),
                "office_part_gwh": round(office_gwh, 1), "datacenter_electricity_mw": round(dc_mw_elec, 2),
                "capture_fraction": cfg["dc_heat_capture_fraction"],
                "building_steam_gbtu_avg": round(float(np.mean(s["steam_kbtu"])) / 1e6, 1)}
    cfg["_supply_info"] = info
    h = np.arange(24)
    swing = 1 + cfg["dc_load_swing"] * np.cos(2 * np.pi * (h - 15) / 24)       # peak mid-afternoon
    # cooling-first rule: never export more than max_export_fraction of the heat (and never more than its uptime allows),
    # so the data center's own coolers always carry a base load. The optimizer cannot plan past this limit.
    usable = min(cfg["dc_availability"], cfg["max_export_fraction"])
    return np.tile(cfg["dc_heat_mw_th"] * 1000 * usable * swing, (12, 1))


def cop_matrix(real, cfg, dhw_frac, low_temp=None, base_low=None):
    """Heat pump COP and the share of heat taken DIRECTLY, for each building and month.
    Hot water needs ~60 C all year; radiators need less when it is mild (reset curve); low-temperature
    systems (greenhouses, radiant floors, new buildings) need less still. If the source water is already
    hot enough (source - approach >= what the building needs), no heat pump is used: direct heat exchange."""
    n = len(dhw_frac)
    if cfg["cop_model"] == "fixed" or not real:
        return np.full(dhw_frac.shape, cfg["heat_pump_cop"], float), np.zeros(dhw_frac.shape)
    t_out = np.array(real["weather"]["monthly_mean_temp_c"])
    t_sup = np.interp(t_out, [cfg["space_reset_cold_outdoor_c"], cfg["space_reset_mild_outdoor_c"]],
                      [cfg["space_supply_temp_cold_c"], cfg["space_supply_temp_mild_c"]])        # [12]
    lt = np.zeros(n, bool) if low_temp is None else np.asarray(low_temp, bool)
    t_sup = np.where(lt[:, None], np.minimum(t_sup[None, :], cfg["low_temp_space_c"]), t_sup[None, :])   # [n,12]
    src, eta, cmax, appr = cfg["dc_source_temp_c"], cfg["carnot_efficiency"], cfg["cop_max"], cfg["direct_use_approach_k"]
    direct_space = t_sup <= src - appr
    cop_space = np.where(direct_space, cfg["cop_direct"],
                         np.minimum(cmax, eta * (t_sup + 273.15) / np.maximum(t_sup - src, 2.0)))
    # the year-round "baseload" is 60 C hot water for most customers, but a low-temperature process (greenhouse, fish tanks)
    bl = np.zeros(n, bool) if base_low is None else np.asarray(base_low, bool)
    t_d = np.where(bl, cfg["low_temp_space_c"], cfg["dhw_supply_temp_c"]).astype(float)           # [n]
    direct_dhw = t_d <= src - appr
    cop_dhw = np.where(direct_dhw, cfg["cop_direct"], np.minimum(cmax, eta * (t_d + 273.15) / np.maximum(t_d - src, 2.0)))[:, None]
    cop = 1 / (dhw_frac / cop_dhw + (1 - dhw_frac) / cop_space)
    direct = (1 - dhw_frac) * direct_space + dhw_frac * direct_dhw[:, None]
    return cop, direct


# ================================================================ 3. economics
def building_economics(c, demand, tree, cfg, cop_bm, dhw_share, direct_bm):
    """Everything we need to know about each candidate building, as arrays/columns."""
    eff, price, coef = eff_by_fuel(cfg), price_by_fuel(cfg), cfg["co2_t_per_kbtu"]
    loss = cfg["pipe_heat_loss_fraction"]
    n = len(c)

    # --- heat pump delivers up to a fraction of the building's peak; boiler covers the rest
    peak = demand.max(axis=(1, 2))
    hp_kw = c["sizing"].values * peak
    delivered = np.minimum(demand, hp_kw[:, None, None])          # kW, [n,12,24]
    # heat taken from the DC = delivered x (1 - 1/COP); COP differs by building and month
    dc_draw = delivered * (1 - 1 / cop_bm[:, :, None]) / (1 - loss)      # kW
    deliv_kwh = yearly_kwh(delivered)
    hp_elec_kwh = (delivered.sum(axis=2) / cop_bm * DAYS).sum(axis=1)

    # --- what the building burns today per kWh of heat the pump will replace
    fuel = np.column_stack([c[f"{f}_kbtu"].values for f in FUELS])           # kBtu
    share = fuel / fuel.sum(axis=1, keepdims=True)
    kbtu_per_kwh = share * KBTU_PER_KWH / np.array([eff[f] for f in FUELS])  # fuel kBtu per kWh heat
    cost_now_per_kwh = (kbtu_per_kwh * np.array([price[f] for f in FUELS]) / 1000).sum(axis=1)
    co2_now_per_kwh = (kbtu_per_kwh * np.array([coef[f] for f in FUELS])).sum(axis=1)
    fuel_avoided_usd = deliv_kwh * cost_now_per_kwh

    # --- air quality co-benefit: pollution no longer made inside / next to the building (power plants not counted)
    fuel_avoided_kbtu = kbtu_per_kwh * deliv_kwh[:, None]                       # [n,5] kBtu of each fuel not burned
    nox_avoided_lb = (fuel_avoided_kbtu / 1000 * np.array([cfg["nox_lb_per_mmbtu"][f] for f in FUELS])).sum(axis=1)
    pm25_avoided_lb = (fuel_avoided_kbtu / 1000 * np.array([cfg["pm25_lb_per_mmbtu"][f] for f in FUELS])).sum(axis=1)

    # --- LL97: emissions before/after vs the building's limit
    elec_now = c["elec_kwh"].fillna(0).values
    em_before = (fuel * np.array([coef[f] for f in FUELS])).sum(axis=1) + elec_now * cfg["grid_co2_t_per_kwh"]
    co2_avoided = deliv_kwh * co2_now_per_kwh - hp_elec_kwh * cfg["grid_co2_t_per_kwh"]
    em_after = em_before - co2_avoided
    limit = np.array([ll97_limit(r, cfg) for r in c.itertuples()])
    fine = cfg["ll97_fine_usd_per_tco2e"]
    fines_before = fine * np.maximum(0, em_before - limit)
    fines_after = fine * np.maximum(0, em_after - limit)
    fines_avoided = fines_before - fines_after

    # --- money. Building pays (1 - discount) x what it avoids today (fuel + LL97 fines).
    benefit = fuel_avoided_usd + fines_avoided
    public_ = c["equity_flag"].fillna(False).astype(bool).values       # public housing / affordability-priority
    tiers = [assign_tier(r.use_mix_json, public_[k], dhw_share[k], cfg) for k, r in enumerate(c.itertuples())]
    tier_adj = np.array([cfg["tier_price_adjust"][t] for t in tiers])
    revenue = (1 - cfg["customer_discount"] + tier_adj) * benefit
    elec_cost = hp_elec_kwh * cfg["elec_price_usd_per_kwh"]

    # share of each building's heat taken directly (no heat pump): its heat pump cost shrinks to a heat exchanger
    month_kwh = delivered.sum(axis=2) * DAYS
    direct_share = (direct_bm * month_kwh).sum(axis=1) / np.maximum(month_kwh.sum(axis=1), 1e-9)
    hp_capex = hp_kw * cfg["hp_usd_per_kw_th"] * (1 - direct_share)
    is_home = c["cluster_homes"].fillna(False).astype(bool).values if "cluster_homes" in c else np.zeros(n, bool)
    n_units = c["n_units"].fillna(1).values if "n_units" in c else np.ones(n)
    tie_in = np.where(is_home, cfg["home_connection_usd"] * n_units, cfg["tie_in_fixed_usd"]) + cfg["tie_in_usd_per_kw_th"] * hp_kw
    service_m = c["service_m"].fillna(0).values if "service_m" in c else np.zeros(n)
    street_lat_m = np.array(tree.lateral_m) * cfg["lateral_routing_factor"]
    lateral_m = street_lat_m + service_m                                     # for reporting
    lateral_capex = street_lat_m * cfg["pipe_lateral_usd_per_m"] + service_m * cfg["service_pipe_usd_per_m"]
    equip_capex = hp_capex + tie_in
    bcapex = equip_capex + lateral_capex               # capital that belongs to this building alone

    om = cfg["hp_om_fraction"] * equip_capex + cfg["pipe_om_fraction"] * lateral_capex
    net_cash = revenue - elec_cost - om                # yearly cash before shared pipe and capital
    crf_eq = crf(cfg["discount_rate"], cfg["equipment_life_years"])
    crf_pipe = crf(cfg["discount_rate"], cfg["pipe_life_years"])
    ann_cap = equip_capex * crf_eq + lateral_capex * crf_pipe
    standalone_value = net_cash - ann_cap              # yearly value before shared pipe

    # --- phasing inputs
    yb = c["yearbuilt"].where(c["yearbuilt"] >= 1800, cfg["default_year_built"]).fillna(cfg["default_year_built"])
    life = cfg["boiler_life_years"]
    boiler_left = life - ((cfg["base_year"] - yb) % life)      # years until next boiler replacement
    owner = c["ownername"].fillna("").str.upper()
    public = owner.str.contains("HOUSING AUTHORITY|NYCHA").values

    return pd.DataFrame({
        "bbl": c["bbl"].values, "hp_kw": hp_kw, "peak_kw": peak, "deliv_kwh": deliv_kwh,
        "dc_draw_kwh": yearly_kwh(dc_draw), "hp_elec_kwh": hp_elec_kwh,
        "cost_now_per_kwh": cost_now_per_kwh, "fuel_avoided_usd": fuel_avoided_usd,
        "co2_avoided_t": co2_avoided,
        "co2_avoided_local_t": co2_avoided - hp_elec_kwh * (cfg["grid_co2_local_t_per_kwh"] - cfg["grid_co2_t_per_kwh"]),
        "nox_avoided_lb": nox_avoided_lb, "pm25_avoided_lb": pm25_avoided_lb, "em_before_t": em_before, "em_after_t": em_after,
        "ll97_limit_t": limit, "fines_before": fines_before, "fines_after": fines_after,
        "fines_avoided": fines_avoided, "revenue": revenue, "elec_cost": elec_cost, "om": om,
        "equip_capex": equip_capex, "hp_capex": hp_capex, "tie_in_capex": tie_in,
        "lateral_m": lateral_m, "lateral_capex": lateral_capex, "bcapex": bcapex,
        "net_cash": net_cash, "ann_cap": ann_cap, "standalone_value": standalone_value,
        "boiler_left_years": boiler_left.values, "public_housing": public,
        "tier": tiers, "tier_adjust": tier_adj, "direct_share": direct_share,
        **{f"avoid_kbtu_{f}": fuel_avoided_kbtu[:, k] for k, f in enumerate(FUELS)},
    }), delivered, dc_draw


def ll97_limit(r, cfg):
    """Yearly CO2 allowance (tCO2e) = sum over use types of floor area x limit for its LL97 group."""
    lim, grp = cfg["ll97_limit_t_per_ft2_by_group"], cfg["ll97_group_by_use"]
    mix = json.loads(r.use_mix_json) if isinstance(r.use_mix_json, str) else {}
    tot = sum(mix.values())
    if tot <= 0:
        return r.gfa * lim["default"]
    scale = r.gfa / tot
    return sum(a * scale * lim.get(grp.get(u, "default"), lim["default"]) for u, a in mix.items())


# ================================================================ evaluate any set of buildings
def evaluate(S, econ, delivered, dc_draw, tree, cfg, cap_kw):
    """Totals for a set S of building indices (positions in econ)."""
    S = sorted(S)
    edges = sorted({e for i in S for e in tree.path_edges(tree.attach[i])})
    pipe_m = sum(tree.edge_len[e] for e in edges)
    pipe_capex = pipe_m * cfg["pipe_main_usd_per_m"]
    crf_pipe = crf(cfg["discount_rate"], cfg["pipe_life_years"])
    pipe_ann = pipe_capex * (crf_pipe + cfg["pipe_om_fraction"])
    e = econ.iloc[S]
    draw = dc_draw[S].sum(axis=0) if S else np.zeros((12, 24))
    mdraw = (draw.sum(axis=1) * DAYS)                        # kWh per month taken from DC
    avail_month = cap_kw.sum(axis=1) * DAYS
    capex = e["bcapex"].sum() + pipe_capex
    # what the street pipes cost in full, and how much of that someone else (utility rates, grants) carries
    full = cfg.get("pipe_main_usd_per_m_full", cfg["pipe_main_usd_per_m"])
    net_full = pipe_m * full * (crf_pipe + cfg["pipe_om_fraction"])
    paid_by_others = net_full - pipe_ann
    net_cash = e["net_cash"].sum() - pipe_capex * cfg["pipe_om_fraction"]
    return {
        "n_buildings": len(S), "pipe_m": pipe_m, "lateral_m": e["lateral_m"].sum(),
        "capex_usd": capex, "annual_net_cash_usd": net_cash,
        "annual_value_usd": e["standalone_value"].sum() - pipe_ann,
        "network_cost_annual_usd": net_full, "network_cost_paid_by_others_usd": paid_by_others,
        "annual_value_after_network_usd": e["standalone_value"].sum() - pipe_ann - paid_by_others,
        "capex_total_usd": capex + pipe_m * (full - cfg["pipe_main_usd_per_m"]),
        "simple_payback_years": capex / net_cash if net_cash > 0 else None,
        "heat_delivered_gwh": e["deliv_kwh"].sum() / 1e6,
        "dc_heat_used_gwh": e["dc_draw_kwh"].sum() / 1e6,
        "dc_utilization": mdraw.sum() / avail_month.sum(),
        "dc_utilization_summer": mdraw[SUMMER].sum() / avail_month[SUMMER].sum(),
        "dc_utilization_winter": mdraw[[0, 1, 11]].sum() / avail_month[[0, 1, 11]].sum(),
        "heat_density_mwh_per_m": (e["deliv_kwh"].sum() / 1000) / max(pipe_m + e["lateral_m"].sum(), 1.0),
        "co2_avoided_t": e["co2_avoided_t"].sum(), "co2_avoided_local_grid_t": e["co2_avoided_local_t"].sum(),
        "nox_avoided_t": e["nox_avoided_lb"].sum() / 2000, "pm25_avoided_kg": e["pm25_avoided_lb"].sum() * 0.4536,
        "fines_avoided_usd": e["fines_avoided"].sum(),
        "peak_dc_draw_mw": draw.max() / 1000, "monthly_dc_draw_mwh": (mdraw / 1000).tolist(),
    }


# ================================================================ 5. MILP
def solve_milp(econ, dc_draw, tree, cfg, cap_kw, bbl, max_share=None, insurance=None):
    """
    Decide x[b] (building connected?) and y[e] (street segment built?) to maximise
        sum_b x_b * (yearly value of building b)  -  sum_e y_e * (yearly cost of segment e)
    subject to
        heat taken from the DC in every hour of every typical day <= its capacity
        x_b <= y(segment where b attaches)        (can't connect without the pipe)
        y_e <= y(segment upstream of e)           (can't build a branch without its trunk)
    """
    # A building whose own value is negative can never help (shared pipe only adds cost).
    P = [i for i in range(len(econ)) if econ["standalone_value"].iloc[i] > 0]
    if not P:
        return [], {"status": "no profitable building"}
    edges = sorted({e for i in P for e in tree.path_edges(tree.attach[i])})
    eidx = {e: len(P) + k for k, e in enumerate(edges)}
    nv = len(P) + len(edges)
    crf_pipe = crf(cfg["discount_rate"], cfg["pipe_life_years"])
    # what a street segment costs in the objective: the project's share, plus the weighted share paid by others
    s_, w_ = cfg["pipe_subsidy_fraction"], cfg["public_cost_weight"]
    full_m = cfg.get("pipe_main_usd_per_m_full", cfg["pipe_main_usd_per_m"])
    obj_per_m = full_m * ((1 - s_) + w_ * s_)
    seg_cost = np.array([tree.edge_len[e] * obj_per_m * (crf_pipe + cfg["pipe_om_fraction"]) for e in edges])
    obj = np.concatenate([-econ["standalone_value"].values[P], seg_cost])   # minimise

    rows, cols, vals, ub = [], [], [], []
    r = 0
    cap_flat = np.asarray(cap_kw).reshape(-1)          # 288 values: month x hour
    # capacity rows: one per (month, hour)
    draw = dc_draw[P].reshape(len(P), -1)             # [P, 288]
    for t in range(draw.shape[1]):
        nz = np.nonzero(draw[:, t])[0]
        rows += [r] * len(nz); cols += nz.tolist(); vals += draw[nz, t].tolist()
        ub.append(float(cap_flat[t])); r += 1
    # x_b <= y_attach
    for k, i in enumerate(P):
        a = tree.attach[i]
        if a == tree.root:
            continue
        rows += [r, r]; cols += [k, eidx[a]]; vals += [1, -1]; ub.append(0); r += 1
    # y_e <= y_parent
    for e in edges:
        p = tree.pred[e]
        if p == tree.root:
            continue
        rows += [r, r]; cols += [eidx[e], eidx[p]]; vals += [1, -1]; ub.append(0); r += 1

    # a building gets at most one heat pump size
    by_bbl = {}
    for k, i in enumerate(P):
        by_bbl.setdefault(bbl[i], []).append(k)
    for ks in by_bbl.values():
        if len(ks) > 1:
            rows += [r] * len(ks); cols += ks; vals += [1] * len(ks); ub.append(1); r += 1
    # insurance rule: losing any ONE customer k must keep at least a fraction f of the plan's value
    #   sum_{j != k} v_j x_j - P  >=  f * (sum_j v_j x_j - P) + margin,   P = sum_e c_e y_e
    if (cfg["insurance_rule"] if insurance is None else insurance):
        v = econ["standalone_value"].values[P]
        f, margin = cfg["insurance_min_fraction"], cfg["insurance_min_value_usd"]
        ycols = list(range(len(P), nv))
        for k in range(len(P)):
            js = [j for j in range(len(P)) if j != k]
            rows += [r] * (len(P) + len(ycols)); cols += js + [k] + ycols
            vals += [-(1 - f) * v[j] for j in js] + [f * v[k]] + ((1 - f) * seg_cost).tolist()
            ub.append(-margin); r += 1
    # no customer may take more than max_customer_share of the heat delivered:
    #   d_b x_b - share * sum_j d_j x_j <= 0
    d = econ["deliv_kwh"].values[P]
    share = cfg["max_customer_share"] if max_share is None else max_share
    for k in range(len(P)):
        rows += [r] * len(P); cols += list(range(len(P)))
        vals += [(d[j] if j == k else 0) - share * d[j] for j in range(len(P))]
        ub.append(0); r += 1

    A = coo_matrix((vals, (rows, cols)), shape=(r, nv)).tocsr()
    integrality = np.concatenate([np.ones(len(P)), np.zeros(len(edges))])   # y can stay continuous
    t0 = time.time()
    res = milp(obj, constraints=LinearConstraint(A, -np.inf, np.array(ub)), integrality=integrality,
               bounds=Bounds(0, 1),
               options={"time_limit": cfg["solver_time_limit_s"], "mip_rel_gap": 1e-4})
    if res.x is None:
        return [], {"status": f"solver failed: {res.message}"}
    chosen = [P[k] for k in range(len(P)) if res.x[k] > 0.5]
    return chosen, {"status": res.message, "seconds": round(time.time() - t0, 1),
                    "candidates_in_milp": len(P), "mip_gap": getattr(res, "mip_gap", None)}


# ================================================================ 6. baseline
def baseline_biggest_first(econ, dc_draw, tree, cap_kw, eligible):
    """Naive plan: connect buildings in order of yearly heat use while the data center can supply them."""
    order = sorted(eligible, key=lambda i: -econ["deliv_kwh"].iloc[i])
    used = np.zeros((12, 24))
    chosen = []
    for i in order:
        if (used + dc_draw[i] <= cap_kw).all():
            used += dc_draw[i]
            chosen.append(i)
    return chosen


# ================================================================ 7. phasing
def assign_phases(S, econ, tree, cfg):
    """
    Phase 1: fastest payback first (public housing counts as if payback were 'equity weight' x faster)
    Phase 2: boilers closest to replacement (same equity weight), payback as tie-break
    Phase 3: everything else
    Payback uses INCREMENTAL capital: pipes already built by earlier picks are free for later ones.
    Each of phases 1-2 stops when it has spent its share of the total capital.
    """
    cost = lambda e: tree.edge_len[e] * cfg["pipe_main_usd_per_m"]
    eq = lambda i: max(cfg["equity_weight_public_housing"] if econ["public_housing"].iloc[i] else 1.0,
                       cfg["equity_weight_dac"] if econ["dac"].iloc[i] else 1.0)
    built, remaining, phase_of, new_edges_of = set(), set(S), {}, {}
    total = econ["bcapex"].iloc[list(S)].sum() + sum(
        cost(e) for e in {e for i in S for e in tree.path_edges(tree.attach[i])})
    targets = [s * total for s in cfg["phase_capex_share"]]
    order = {}
    for ph in (1, 2, 3):
        spent = 0.0
        while remaining and (ph == 3 or spent < targets[ph - 1]):
            best = None
            for i in remaining:
                new = [e for e in tree.path_edges(tree.attach[i]) if e not in built]
                inc = econ["bcapex"].iloc[i] + sum(cost(e) for e in new)
                payback = inc / max(econ["net_cash"].iloc[i], 1.0)
                key = (econ["boiler_left_years"].iloc[i] / eq(i), payback) if ph == 2 else (payback / eq(i), 0)
                if best is None or key < best[0]:
                    best = (key, i, inc, new)
            _, i, inc, new = best
            remaining.discard(i); built.update(new)
            phase_of[i] = ph; new_edges_of[i] = new; spent += inc
            order[i] = len(order)
    return phase_of, new_edges_of, order


def attribute_pipe_cost(S, econ, tree, cfg):
    """Share each street segment's cost among the buildings downstream of it, by heat delivered."""
    cost = {}
    per_edge_q = {}
    for i in S:
        for e in tree.path_edges(tree.attach[i]):
            per_edge_q[e] = per_edge_q.get(e, 0) + econ["deliv_kwh"].iloc[i]
    share = {i: 0.0 for i in S}
    for i in S:
        for e in tree.path_edges(tree.attach[i]):
            share[i] += tree.edge_len[e] * cfg["pipe_main_usd_per_m"] * econ["deliv_kwh"].iloc[i] / per_edge_q[e]
    return share


# ================================================================ 8. export
def export_json(c, econ, delivered, dc_draw, tree, S, phase_of, new_edges_of, order, cfg, cap_kw,
                optimized, baseline, solver, path, uncapped=None, stress=None, robustness=None, scenarios=None, wf=None, climate=None, extras=None):
    pipe_share = attribute_pipe_cost(S, econ, tree, cfg)
    chosen_bbl = {int(c["bbl"].iloc[i]) for i in S}
    bbl_phase = {int(c["bbl"].iloc[i]): ph for i, ph in phase_of.items()}
    loss = cfg["pipe_heat_loss_fraction"]
    fp = json.loads(_SITE.footprints.read_text())
    height_m = {}
    for f in fp["features"]:
        h = f["properties"]["height_ft"]
        height_m[f["properties"]["bbl"]] = max(height_m.get(f["properties"]["bbl"], 0),
                                               (h or 0) * 0.3048)

    # one row per building: the chosen size if chosen, else the largest size
    big = max(cfg["hp_sizing_options"])
    rows_out = [i for i in range(len(c)) if i in phase_of or
                (c["sizing"].iloc[i] == big and int(c["bbl"].iloc[i]) not in chosen_bbl)]
    buildings = []
    for i in rows_out:
        r, e = c.iloc[i], econ.iloc[i]
        chosen = i in phase_of
        b = {"bbl": int(r.bbl), "address": r.address, "lat": r.latitude, "lon": r.longitude,
             "property_type": r.ptype if isinstance(r.ptype, str) else None,
             "year_built": None if r.yearbuilt < 1800 else int(r.yearbuilt),
             "floor_area_ft2": float(r.gfa), "height_m": round(height_m.get(int(r.bbl), 0), 1),
             "public_housing": bool(e.public_housing), "chosen": chosen,
             "dac": bool(e.dac), "dac_percentile": e.dac_pct,
             "proposed": bool(c["proposed"].iloc[i]) if "proposed" in c else False,
             "data_quality": "estimated" if bool(r.estimated) else "measured",
             "selection_frequency": (robustness or {}).get("frequency", {}).get(str(int(r.bbl)))}
        if chosen:
            cap = e.bcapex + pipe_share[i]
            mmbtu = e.deliv_kwh * KBTU_PER_KWH / 1000
            price_mmbtu = e.revenue / mmbtu
            mm = (delivered[i].sum(axis=1) * DAYS) / 1000          # MWh heat per month
            b.update({
                "phase": phase_of[i], "join_year": cfg["phase_start_years"][phase_of[i] - 1],
                "join_order": order[i] + 1,
                "tier": e.tier, "tier_note": TIER_NOTE[e.tier], "tier_price_adjust": float(e.tier_adjust),
                "fit": {"score": round(float(e.fit_score), 1), "temperature": round(float(e.fit_temperature), 2),
                        "timing": round(float(e.fit_timing), 2), "seasonality": round(float(e.fit_seasonality), 2),
                        "capacity": round(float(e.fit_capacity), 2), "avg_cop": round(float(e.fit_cop_avg), 2)},
                "location": location_of(c, i),
                "reliability": {k: v for k, v in extras["rel"][i].items() if k != "unavailability"},
                "temperature_match": {"source_c": cfg["dc_source_temp_c"], "direct_use_share": round(float(e.direct_share), 3),
                                      "average_cop": round(float(e.fit_cop_avg), 2),
                                      "space_heat_needs_c": ("%d (low-temperature system)" % cfg["low_temp_space_c"]) if bool(c["low_temp"].iloc[i] if "low_temp" in c else False)
                                      else "%d to %d depending on weather" % (cfg["space_supply_temp_mild_c"], cfg["space_supply_temp_cold_c"]),
                                      "hot_water_needs_c": cfg["dhw_supply_temp_c"]},
                "nox_avoided_lb_year": round(float(e.nox_avoided_lb)), "pm25_avoided_lb_year": round(float(e.pm25_avoided_lb), 1),
                "boiler_years_left": round(float(e.boiler_left_years), 1),
                "equipment": {"heat_pump_kw_th": round(e.hp_kw), "heat_exchanger_kw_th": round(e.hp_kw),
                              "lateral_pipe_m": round(e.lateral_m), "hp_capex_usd": round(e.hp_capex),
                              "tie_in_capex_usd": round(e.tie_in_capex),
                              "lateral_capex_usd": round(e.lateral_capex),
                              "backup_boiler": "keep existing boiler as backup"},
                "heat_demand_mwh_year": round(econ["demand_kwh"].iloc[i] / 1000, 1),
                "heat_delivered_mwh_year": round(e.deliv_kwh / 1000, 1),
                "heat_delivered_mwh_month": [round(v, 2) for v in mm],
                "dc_heat_mwh_month": [round(v * (1 - 1 / cm) / (1 - loss), 2)
                                      for v, cm in zip(mm, econ["cop_m"].iloc[i])],
                "cop_month": [round(float(cm), 2) for cm in econ["cop_m"].iloc[i]],
                "hot_water_share": round(float(econ["dhw_share"].iloc[i]), 2),
                "demand_source": econ["demand_source"].iloc[i],
                "price_now_usd_per_mmbtu": round(e.cost_now_per_kwh / (KBTU_PER_KWH / 1000), 2),
                "price_now_all_in_usd_per_mmbtu": round((e.fuel_avoided_usd + e.fines_avoided) / mmbtu, 2),
                "price_heatos_usd_per_mmbtu": round(price_mmbtu, 2),
                "fuel_avoided_usd_year": round(e.fuel_avoided_usd),
                "ll97_fines_before_usd_year": round(e.fines_before),
                "ll97_fines_after_usd_year": round(e.fines_after),
                "ll97_fines_avoided_usd_year": round(e.fines_avoided),
                "heatos_charge_usd_year": round(e.revenue),
                "yearly_savings_usd": round(e.fuel_avoided_usd + e.fines_avoided - e.revenue),
                "capex_attributed_usd": round(cap),
                "payback_years": round(cap / e.net_cash, 1) if e.net_cash > 0 else None,
                "co2_avoided_t_year": round(e.co2_avoided_t, 1),
                "emissions_before_t": round(e.em_before_t, 1), "emissions_after_t": round(e.em_after_t, 1),
                "ll97_limit_t": round(e.ll97_limit_t, 1),
                "hp_electricity_mwh_year": round(e.hp_elec_kwh / 1000, 1),
            })
        if chosen:
            b["why_suitable"] = why_suitable(b)
        buildings.append(b)

    # ---- pipes: main segments (street tree), tagged with the phase that first builds them
    edge_phase, edge_peak = {}, {}
    for i, ph in phase_of.items():
        for e in new_edges_of[i]:
            edge_phase[e] = ph
        for e in tree.path_edges(tree.attach[i]):
            edge_peak[e] = edge_peak.get(e, 0) + econ["hp_kw"].iloc[i]
    pipes = []
    for e, ph in sorted(edge_phase.items(), key=lambda kv: tree.dist[kv[0]]):
        pipes.append({"kind": "main", "phase": ph, "length_m": round(tree.edge_len[e], 1),
                      "serves": extras["serves"][e]["serves"], "heat_share": round(extras["serves"][e]["heat_share"], 4),
                      "dn_mm": extras["edge_sizing"][e]["dn_mm"], "flow_kg_s": extras["edge_sizing"][e]["flow_kg_s"],
                      "capex_usd": round(tree.edge_len[e] * cfg["pipe_main_usd_per_m"]),
                      "peak_kw_th": round(edge_peak[e]),
                      "coords": [[round(x, 6), round(y, 6)] for x, y in tree.edge_coords(e)]})
    # connection from data center to the first intersection
    for i, ph in phase_of.items():
        a = tree.attach[i]
        lon, lat = tree.node_lonlat[a]
        pipes.append({"kind": "lateral", "phase": ph, "bbl": int(c["bbl"].iloc[i]),
                      "dn_mm": extras["lat_sizing"][i]["dn_mm"], "flow_kg_s": extras["lat_sizing"][i]["flow_kg_s"],
                      "length_m": round(econ["lateral_m"].iloc[i], 1),
                      "capex_usd": round(econ["lateral_capex"].iloc[i]),
                      "coords": [[round(lon, 6), round(lat, 6)],
                                 [round(float(c["longitude"].iloc[i]), 6), round(float(c["latitude"].iloc[i]), 6)]]})
    root_lon, root_lat = tree.node_lonlat[tree.root]
    pipes.insert(0, {"kind": "dc_link", "phase": 1, "length_m": round(tree.dc_to_root_m, 1),
                     "capex_usd": round(tree.dc_to_root_m * cfg["pipe_main_usd_per_m"]),
                     "peak_kw_th": round(max(edge_peak.values(), default=0)),
                     "dn_mm": extras["system"]["network"]["link_dn_mm"], "flow_kg_s": extras["system"]["network"]["peak_flow_kg_s"],
                     "coords": [[_SITE.dc_latlon[1], _SITE.dc_latlon[0]], [round(root_lon, 6), round(root_lat, 6)]]})

    # ---- phase summary (new things built in each phase)
    phases = []
    for ph in (1, 2, 3):
        mem = [i for i, p in phase_of.items() if p == ph]
        mains = [p for p in pipes if p["kind"] == "main" and p["phase"] == ph]
        e = econ.iloc[mem] if mem else econ.iloc[:0]
        phases.append({"phase": ph, "start_year": cfg["phase_start_years"][ph - 1],
                       "label": ["Fast payback", "Boilers near end of life", "Fill in the rest"][ph - 1],
                       "n_buildings": len(mem), "n_public_housing": int(e["public_housing"].sum()),
                       "main_pipe_m": round(sum(p["length_m"] for p in mains)
                                            + (tree.dc_to_root_m if ph == 1 else 0)),
                       "lateral_pipe_m": round(float(e["lateral_m"].sum())),
                       "capex_usd": round(float(e["bcapex"].sum()) + sum(p["capex_usd"] for p in mains)
                                          + (tree.dc_to_root_m * cfg["pipe_main_usd_per_m"] if ph == 1 else 0)),
                       "heat_delivered_gwh_year": round(float(e["deliv_kwh"].sum()) / 1e6, 2),
                       "co2_avoided_t_year": round(float(e["co2_avoided_t"].sum()), 0),
                       "fines_avoided_usd_year": round(float(e["fines_avoided"].sum()))})

    feats = []
    for f in fp["features"]:
        g = set_precision(shape(f["geometry"]).simplify(2e-6), 1e-6)
        bbl = f["properties"]["bbl"]
        ph = bbl_phase.get(bbl, 0)
        feats.append({"type": "Feature", "geometry": mapping(g), "properties": {
            "bbl": bbl, "height_m": round((f["properties"]["height_ft"] or 0) * 0.3048, 1),
            "phase": ph, "is_dc": bbl == _SITE.dc_id}})

    prod_factor = min(cfg["dc_availability"], cfg["max_export_fraction"])          # production = usable / this
    monthly_avail = [round(float(cap_kw[m].sum()) / prod_factor * int(d) / 1000, 1) for m, d in enumerate(DAYS)]   # what the data center makes
    plan = {
        "meta": {"schema_version": "0.1", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "note": "Illustrative: costs are placeholders, see assumptions[*].placeholder.",
                 "solver": solver},
        # the values the model actually used (data-derived ones replace the config defaults)
        "assumptions": {k: {**v, "value": cfg.get(k, v["value"]),
                            "overridden_by_data": cfg.get(k) != v["value"]}
                        for k, v in config.assumptions_for(_SITE.id).items()},
        "site": {"id": _SITE.id, "name": _SITE.name, "subtitle": _SITE.subtitle, "dc_name": _SITE.dc_name,
                 "dc_status": _SITE.dc_status, "network_owner": _SITE.network_owner, "camera": _SITE.camera,
                 "estimated_note": _SITE.estimated_note},
        "datacenter": {"name": _SITE.dc_name, "bbl": _SITE.dc_id, "lat": _SITE.dc_latlon[0], "lon": _SITE.dc_latlon[1],
                       "heat_mw_th": round(cfg["dc_heat_mw_th"], 2), "usable_mw_th": round(float(cap_kw.mean()) / 1000, 2),
                       "supply_estimate": cfg.get("_supply_info"),
                       "monthly_available_mwh": monthly_avail},
        "datacenter_benefits": {
            "heat_removed_gwh_year": round(optimized["dc_heat_used_gwh"], 1),
            "cooling_electricity_saved_gwh_year": round(optimized["dc_heat_used_gwh"] / cfg["chiller_cop"], 1),
            "average_freed_power_mw": round(optimized["dc_heat_used_gwh"] / cfg["chiller_cop"] * 1000 / 8760, 2),
            "peak_freed_power_mw": round(optimized["peak_dc_draw_mw"] / cfg["chiller_cop"], 2),
            "water_saved_million_gallons_year": round(optimized["dc_heat_used_gwh"] * 1e6 * cfg["evaporative_cooling_share"]
                                                       * cfg["cooling_water_l_per_kwh_heat"] / 3.785 / 1e6, 1),
            "note": "Electricity the chillers no longer need; can run servers instead (power is scarce in NYC)."},
        "phases": phases,
        "totals": optimized | {"monthly_dc_draw_mwh": [round(v, 1) for v in optimized["monthly_dc_draw_mwh"]]},
        "stress_tests": stress,
        "ownership_scenarios": scenarios,
        "service_tiers": [{"tier": t, "priority": k + 1, "note": TIER_NOTE[t],
                           "price_adjust": cfg["tier_price_adjust"][t],
                           "n_buildings": sum(1 for i in S if econ["tier"].iloc[i] == t)}
                          for k, t in enumerate(TIERS)],
        "waterfall": wf,
        "reliability": {"cooling_firewall": extras["firewall"],
                        "continuity": {"network_availability_pct": [m for m in extras["match"] if m["axis"] == "Continuity"][0]["numbers"]["availability_pct"],
                                       "storage_hours": cfg["storage_hours"], "backup_start_minutes": cfg["backup_start_minutes"],
                                       "assumptions": {k: cfg[k] for k in ["dc_trips_per_year", "dc_trip_hours", "pipe_failures_per_km_year",
                                                                           "pipe_repair_hours", "hp_failures_per_year", "hp_repair_hours"]}}},
        "match_scorecard": extras["match"],
        "system": {**extras["system"], "architecture_why": _SITE.architecture_why, "capture_options": _SITE.capture_options, "users": user_mix([b for b in buildings if b["chosen"]]),
                   "evidence": evidence_list(_SITE.id)},
        "ledger": ledger_with_odds(extras["ledger"], robustness),
        "next_in_line": extras["next"],
        "site_context": json.loads((HERE / "data" / "site_context.json").read_text()),
        "climate_outlook": climate,
        "regenerative_scorecard": (scorecard if _SITE.id == "chelsea" else scorecard_lansing)(
            cfg, optimized, [b for b in buildings if b["chosen"]], climate),
        "robustness": {k: v for k, v in (robustness or {}).items() if k != "runs"} or None,
        "comparison": {"heatos_plan": strip(optimized), "no_insurance_rule": strip(uncapped),
                       "biggest_buildings_first": strip(baseline)},
        "buildings": buildings,
        "pipes": pipes,
        "footprints": {"type": "FeatureCollection", "features": feats},
    }
    Path(path).parent.mkdir(exist_ok=True)
    # JSON has no NaN/Infinity: turn them into null so every browser and teammate can read the file
    Path(path).write_text(json.dumps(clean(plan), default=float, separators=(",", ":"), allow_nan=False))
    return plan


def location_of(c, i):
    """Where a building sits relative to the data center: compass direction and metres."""
    dcx, dcy = _to_m.transform(_SITE.dc_latlon[1], _SITE.dc_latlon[0])
    x, y = _to_m.transform(float(c["longitude"].iloc[i]), float(c["latitude"].iloc[i]))
    dx, dy = x - dcx, y - dcy
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return {"direction": names[int((bearing + 22.5) // 45) % 8], "distance_m": round(math.hypot(dx, dy)),
            "west_of_dc": bool(dx < -40)}


def scorecard(cfg, t, chosen, climate):
    """One card per regenerative-design category: what we know about the place + what the plan does about it."""
    n = len(chosen)
    west = sum(1 for b in chosen if b["location"]["west_of_dc"])
    heat = sum(b["heat_delivered_mwh_year"] for b in chosen) or 1
    equity = [b for b in chosen if b["public_housing"] or b["dac"]]
    eq_share = sum(b["heat_delivered_mwh_year"] for b in equity) / heat
    water_mg = t["dc_heat_used_gwh"] * 1e6 * cfg["evaporative_cooling_share"] * cfg["cooling_water_l_per_kwh_heat"] / 3.785 / 1e6
    out = [
        {"category": "Air quality and health", "headline": "%.1f t NOx and %.0f kg PM2.5 avoided per year" % (t["nox_avoided_t"], t["pm25_avoided_kg"]),
         "site_says": "The block just west of the data center is at the 92nd percentile for PM2.5 and 83rd for ozone (suspect combustion and diesel generators). Air is getting worse: 5% of days worse than 'Good' over 10 years, 10% over 5 years.",
         "plan_does": "Switches %d buildings off on-site boilers; %d of them lie west of the data center. Counts only burning in or beside the buildings; the heat pumps' electricity is made elsewhere and is not subtracted here." % (n, west)},
        {"category": "Carbon", "headline": "%s t CO2 avoided per year (LL97 2030 grid); %s t on today's local grid" % (format(round(t["co2_avoided_t"]), ","), format(round(t["co2_avoided_local_grid_t"]), ",")),
         "site_says": "The two nearest power plants emit 654 lb CO2 per MWh; one sits beside a disadvantaged community.",
         "plan_does": "Heat pumps move 4 to 5 units of heat per unit of electricity, so the plan still cuts carbon on today's dirtier local grid, and cuts more as the grid cleans up."},
        {"category": "Water", "headline": "%.1f million gallons of cooling water saved per year, if the towers are evaporative" % water_mg,
         "site_says": "Water stress is low-medium, groundwater table decline is medium, and runoff reaches the Hudson River (79th percentile direct discharge, impaired waterway, IR rating 5).",
         "plan_does": "Heat we carry away is heat the data center does not reject through cooling towers, so less water is evaporated and less tower blowdown goes to the river. Needs the operator to confirm tower use."},
        {"category": "Climate adaptation", "headline": "Heating demand %s%% by 2050; year-round share of delivered heat %.0f%%" % (
            ("%+.0f" % climate[2]["heating_demand_vs_today_pct"]) if climate and len(climate) > 2 else "n/a", 100 * (climate[2]["hot_water_share_of_delivered"] if climate and len(climate) > 2 else 0)),
         "site_says": "+3 F by 2050 and up to 69 days above 90 F (19 today); +12 F by 2080; +5 inches of rain in 5 years. The 500-year floodplain is one block away.",
         "plan_does": "Warmer winters shrink space heating but not hot water, so the plan favors year-round hot-water customers, and the loop can also cool buildings in hotter summers (not valued yet). Heat pumps and controls go above grade, out of the flood zone."},
        {"category": "Equity and community", "headline": "%d public-housing or disadvantaged-community building%s get%s %.0f%% of the heat" % (len(equity), "" if len(equity) == 1 else "s", "s" if len(equity) == 1 else "", 100 * eq_share),
         "site_says": "West of the site, poverty is at the 83rd percentile and minority population at the 79th; the area's social vulnerability is at the 79th percentile.",
         "plan_does": "Public housing is a protected tier: its heat is never cut and it gets extra discount. Equity-flagged buildings rank earlier in the build order."},
        {"category": "Noise and place", "headline": "Site is already at 56 dB (park baseline 43.9 dB)",
         "site_says": "Sustained noise is 12 dB above the ecological baseline.",
         "plan_does": "Design rule: heat pumps go indoors (basements or enclosures) with a noise limit, and each building's boiler runs less. No new outdoor machinery on the street."},
    ]
    return out


def scorecard_lansing(cfg, t, chosen, climate):
    """Lansing's regenerative-design cards: what we know about the place + what the plan does."""
    proposed = [b for b in chosen if b.get("proposed")]
    groups = sum(1 for b in chosen if "Neighborhood" in (b["property_type"] or ""))
    c2050 = climate[2] if climate and len(climate) > 2 else None
    return [
        {"category": "Carbon and heating fuel", "headline": "%s t CO2 avoided per year by displacing gas, propane and oil" % format(round(t["co2_avoided_t"]), ","),
         "site_says": "The grid here is largely zero-carbon, the data center is all-electric with no stack emissions, and many homes still burn propane and heating oil.",
         "plan_does": "Moves heating from fossil fuel to captured server heat. Because the electricity is clean, each unit of heat moved saves a lot of carbon."},
        {"category": "Air quality and health", "headline": "%.1f t NOx and %.0f kg PM2.5 avoided per year" % (t["nox_avoided_t"], t["pm25_avoided_kg"]),
         "site_says": "Air is already very clean (ozone 20th percentile, PM2.5 4th percentile, no days worse than 'Good').",
         "plan_does": "The air-quality gain is small because the starting point is already good, so the case for Lansing rests on cost, carbon and local ownership more than on air."},
        {"category": "Water and the lake", "headline": "Closed loop, almost no extra water, nothing new to Cayuga Lake",
         "site_says": "Cayuga Lake is impaired with phosphorus and storm runoff reaches it. The data center is designed as a closed loop with dry cooling and uses water only on about 10 to 20 peak days a year.",
         "plan_does": "Heat export carries warm water to customers and brings it back cooler in sealed pipes: no discharge to the lake and no new intake. Water saved is small because little is used."},
        {"category": "Climate adaptation", "headline": ("Heating demand %+.0f%% by 2050; year-round share of delivered heat %.0f%%" % (c2050["heating_demand_vs_today_pct"], 100 * c2050["hot_water_share_of_delivered"])) if c2050 else "n/a",
         "site_says": "About 3 F warmer by 2050 and 3 more inches of rain within 10 years. Flood maps for the site need a closer look.",
         "plan_does": "Greenhouse and fish-farm customers need heat in every month, so they hold their value as winters warm. Pumps and controls stay above grade until the flood review is done."},
        {"category": "Equity and affordability", "headline": (("%d neighborhood group%s and " % (groups, "" if groups == 1 else "s")) if groups else "") + "%d new local business%s supplied; heat priced %.0f%% below today" % (
            len(proposed), "" if len(proposed) == 1 else "es", 100 * cfg["customer_discount"]),
         "site_says": "Poverty and minority populations are low locally (28th and 24th percentiles), but homes on propane and oil carry the highest heating costs.",
         "plan_does": "Homes are not connected yet because the pipe per home costs too much. Priority goes to low-income and manufactured-home neighborhoods, and the 'next in line' list shows the grant each group would need to join."},
        {"category": "Local economy and place", "headline": "New year-round farm jobs next to the plant; no change to the data center's footprint",
         "site_says": "The site is a former coal plant on an industrial parcel away from homes, and the developer plans an annual fund for schools, parks and community initiatives.",
         "plan_does": "Cheap heat is what makes a greenhouse and a fish farm possible here, and local ownership keeps the heat income in the community."},
    ]


def next_in_line(S, c, econ, tree, cfg, top=14):
    """For customers NOT in the plan: what would it take? Yearly gap = cost of the extra street pipe they need (at full cost)
    minus the yearly value they bring. A one-off grant that closes the gap is gap / (annuity factor + O&M)."""
    built = {e for i in S for e in tree.path_edges(tree.attach[i])}
    crf_pipe = crf(cfg["discount_rate"], cfg["pipe_life_years"])
    full = cfg.get("pipe_main_usd_per_m_full", cfg["pipe_main_usd_per_m"])
    chosen_bbl = {int(c["bbl"].iloc[i]) for i in S}
    big = max(cfg["hp_sizing_options"])
    rows = []
    for i in range(len(c)):
        bbl = int(c["bbl"].iloc[i])
        if bbl in chosen_bbl or c["sizing"].iloc[i] != big:
            continue
        new_m = sum(tree.edge_len[e] for e in tree.path_edges(tree.attach[i]) if e not in built)
        pipe_ann = new_m * full * (crf_pipe + cfg["pipe_om_fraction"])
        gap = pipe_ann - float(econ["standalone_value"].iloc[i])
        units = int(c["n_units"].iloc[i]) if "n_units" in c else 1
        rows.append({"bbl": bbl, "address": c["address"].iloc[i], "type": c["ptype"].iloc[i], "units": units,
                     "heat_mwh_year": round(float(econ["deliv_kwh"].iloc[i]) / 1000, 1), "new_pipe_m": round(new_m),
                     "yearly_gap_usd": round(gap), "grant_needed_usd": round(max(gap, 0) / (crf_pipe + cfg["pipe_om_fraction"])),
                     "grant_per_unit_usd": round(max(gap, 0) / (crf_pipe + cfg["pipe_om_fraction"]) / units),
                     "heat_density_mwh_per_m": round(float(econ["deliv_kwh"].iloc[i]) / 1000 / max(new_m, 1.0), 2),
                     "meets_density_rule": bool(float(econ["deliv_kwh"].iloc[i]) / 1000 / max(new_m, 1.0) >= cfg["linear_heat_density_threshold_mwh_per_m"]),
                     "viable_at_full_cost": bool(gap <= 0)})
    rows.sort(key=lambda r: (r["yearly_gap_usd"] / max(r["units"], 1)))
    return rows[:top]


def ledger_with_odds(rows, robustness):
    """Attach each party's chance of coming out ahead (from the Monte Carlo) and an overall verdict."""
    win = (robustness or {}).get("party_win_pct", {})
    for r in rows:
        r["wins_in_pct_of_scenarios"] = win.get(r["id"])
    low = [r["party"] for r in rows if r["wins_in_pct_of_scenarios"] is not None and r["wins_in_pct_of_scenarios"] < 80]
    return {"parties": rows, "threshold_pct": 80, "parties_below_threshold": low,
            "balanced": bool(win) and not low}


def evidence_list(site_id):
    """The data each claim rests on, so the proposal can show its evidence."""
    common = ["Hourly end-use load profiles by building type (NREL ComStock / ResStock)", "NOAA hourly weather 2015-2024",
              "NY State disadvantaged-community tracts (2023)", "NYS weekly heating-oil and propane prices; state energy price tables",
              "Regenerative-design site conditions: noise, water, flood, climate, air, health, community"]
    if site_id == "chelsea":
        return ["Local Law 84 energy benchmarking: yearly fuel by type and 12 monthly bills per building (697 buildings near the site)",
                "111 8th Avenue's own LL84 electricity use (2022-24) to size the heat source", "MapPLUTO tax lots (year built, class, area, owner)",
                "NYC Building Footprints (real heights)", "OpenStreetMap street network (real street distances)"] + common
    return ["County tax parcels (floor area, year built, heating fuel for each home)", "OpenStreetMap buildings and streets",
            "Developer's planning-board presentation: 150 MW Phase I, 300 MW Phase II, PUE 1.25, closed-loop liquid cooling, dry cooling with water only at peak"] + common


def clean(x):
    """Recursively replace NaN / infinity with None."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (float, np.floating)) and not np.isfinite(x):
        return None
    return x


def strip(d):
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d.items() if k != "monthly_dc_draw_mwh"}


# ================================================================ stress tests
def stress_tests(S, econ, delivered, dc_draw, tree, cfg, cap_kw, base, c):
    """What if our biggest customer leaves? What if the data center makes less heat?"""
    out = {}
    big = max(S, key=lambda i: econ["deliv_kwh"].iloc[i])
    rest = evaluate([i for i in S if i != big], econ, delivered, dc_draw, tree, cfg, cap_kw)
    out["lose_largest_customer"] = {
        "building": c["address"].iloc[big], "value_usd": round(rest["annual_value_usd"]),
        "change_pct": round(100 * (rest["annual_value_usd"] / base["annual_value_usd"] - 1), 1)}
    f = cfg["stress_dc_loss_fraction"]
    draw = dc_draw[sorted(S)].sum(axis=0)                       # [12,24] kW taken from the DC
    served = np.minimum(1, cap_kw * (1 - f) / np.maximum(draw, 1e-9))
    lost = 1 - (draw * served).sum(axis=1).dot(DAYS) / (draw.sum(axis=1).dot(DAYS))
    margin = (econ["revenue"] - econ["elec_cost"]).iloc[sorted(S)].sum()
    value = base["annual_value_usd"] - lost * margin
    out["dc_output_drops"] = {"fraction": f, "heat_lost_pct": round(100 * lost, 1),
                              "value_usd": round(value),
                              "change_pct": round(100 * (value / base["annual_value_usd"] - 1), 1)}
    return out


# ================================================================ who pays for the pipes?
def run_scenarios(shares=None, site="chelsea"):
    """Solve the plan again with someone else (a grant, or Con Ed's rate base) paying part of the street pipes."""
    names = get_site(site).scenario_labels
    shares = shares or tuple(sorted(names))
    out = []
    for s in shares:
        r = run_plan({"pipe_subsidy_fraction": s}, out=None, verbose=False, light=True, site=site)
        o = r["optimized"]
        out.append({"pipe_subsidy_fraction": s, "label": names.get(s, f"{int(s*100)}% pipe subsidy"),
                    "n_buildings": o["n_buildings"], "annual_value_usd": round(o["annual_value_usd"]),
                    "capex_project_usd": round(o["capex_usd"]), "capex_total_usd": round(o["capex_total_usd"]),
                    "pipe_m": round(o["pipe_m"]),
                    "network_cost_paid_by_others_usd_year": round(o["network_cost_paid_by_others_usd"]),
                    "annual_value_after_network_usd": round(o["annual_value_after_network_usd"]),
                    "heat_delivered_gwh": round(o["heat_delivered_gwh"], 1),
                    "co2_avoided_t": round(o["co2_avoided_t"]), "dc_utilization": round(o["dc_utilization"], 3),
                    "dc_utilization_summer": round(o["dc_utilization_summer"], 3), **{
                        k: (round(v, 3) if isinstance(v, float) else v) for k, v in r["extra"].items()}})
        print(f"  scenario {names.get(s)}: {o['n_buildings']} buildings, ${o['annual_value_usd']/1e6:.2f}M/yr")
    return out


# ================================================================ climate outlook
def run_climate(site="chelsea"):
    """Re-run the plan in warmer climates (+3 F by 2050, +12 F by 2080)."""
    cfg = config.get_config(site=site)
    rows, base = [], None
    for year, dF in cfg["climate_outlook"]:
        r = run_plan({"climate_warming_f": float(dF)}, out=None, verbose=False, light=True, site=site)
        o = r["optimized"]
        base = base or r["demand_total_kwh"]
        rows.append({"year": year, "warming_f": dF, "n_buildings": o["n_buildings"],
                     "annual_value_usd": round(o["annual_value_usd"]), "heat_delivered_gwh": round(o["heat_delivered_gwh"], 1),
                     "heating_demand_vs_today_pct": round(100 * (r["demand_total_kwh"] / base - 1), 1),
                     "hot_water_share_of_delivered": round(r["dhw_share_delivered"], 3),
                     "dc_utilization": round(o["dc_utilization"], 3), "dc_utilization_summer": round(o["dc_utilization_summer"], 3)})
        print(f"  climate {year} (+{dF} F): demand {rows[-1]['heating_demand_vs_today_pct']:+.0f}%, {o['n_buildings']} buildings, value ${o['annual_value_usd']/1e6:.2f}M")
    return rows


# ================================================================ main
def run_plan(overrides=None, out="default", verbose=True, light=False, robustness=None, scenarios=None, climate=None, site="chelsea"):
    """Run everything. Monte Carlo teammates: call run_plan({...overrides...}, out=None)."""
    global _SITE
    _SITE = get_site(site)
    if out == "default":
        out = _SITE.plan_path
    cfg = config.get_config(overrides, site=_SITE.id)
    real, monthly = realdata.load(_SITE) if cfg["use_real_data"] else (None, None)
    if real:
        apply_real_defaults(cfg, overrides, real)
    cap_kw = dc_supply(real, cfg)               # [12,24] kW of heat the data center can give
    cfg["pipe_main_usd_per_m_full"] = cfg["pipe_main_usd_per_m"]
    cfg["pipe_main_usd_per_m"] *= 1 - cfg["pipe_subsidy_fraction"]   # what the project itself pays
    b = pd.read_csv(_SITE.buildings_csv)
    if _SITE.id == "chelsea":
        b = fill_missing(b, cfg)              # estimate buildings with no LL84 data
    else:
        b["has_energy"] = True                # every Lansing building is modeled from property records
        b["estimated"] = b.get("estimated", True)
    c = pick_candidates(b, cfg)
    if verbose:
        print(c["exclude_reason"].replace("", "CANDIDATE").value_counts().to_string(), "\n")
    c = c[c["exclude_reason"] == ""]
    # one row per (building, heat pump size); the MILP picks at most one size per building
    c = pd.concat([c.assign(sizing=s) for s in cfg["hp_sizing_options"]]).reset_index(drop=True)

    demand, info = build_demand(c, cfg, real, monthly)
    cx, cy = _to_m.transform(c["longitude"].values, c["latitude"].values)
    tree = PipeTree(cx, cy, _SITE)
    if "proposed" in c and cfg.get("anchor_propane_share") is not None and c["proposed"].fillna(False).any():
        m = c["proposed"].fillna(False).astype(bool).values          # new customers: split their fuel between propane and gas
        tot = c.loc[m, [f"{f}_kbtu" for f in FUELS]].sum(axis=1).values
        for f in FUELS:
            c.loc[m, f"{f}_kbtu"] = 0.0
        c.loc[m, "propane_kbtu"] = tot * cfg["anchor_propane_share"]
        c.loc[m, "gas_kbtu"] = tot * (1 - cfg["anchor_propane_share"])
    if cfg["climate_warming_f"] > 0:       # a warmer climate also lowers what the building burns (and its LL97 fine)
        for f in FUELS:
            c[f"{f}_kbtu"] = c[f"{f}_kbtu"].values * info["climate_ratio"].values
    if "equity_flag" not in c:                # Chelsea: public housing = NYCHA-owned
        c["equity_flag"] = c["ownername"].fillna("").str.upper().str.contains("HOUSING AUTHORITY|NYCHA")
    low_temp = c["low_temp"].fillna(False).astype(bool).values if "low_temp" in c else (
        c["yearbuilt"].fillna(0).values >= cfg.get("low_temp_year_built", 3000))
    base_low = c["baseload_low_temp"].fillna(False).astype(bool).values if "baseload_low_temp" in c else None
    cop_bm, direct_bm = cop_matrix(real, cfg, np.vstack(info["dhw_frac_m"].values), low_temp, base_low)
    econ, delivered, dc_draw = building_economics(c, demand, tree, cfg, cop_bm, info["dhw_share"].values, direct_bm)
    econ["demand_kwh"] = info["heat_kwh_year"].values
    econ["dhw_share"] = info["dhw_share"].values
    econ["demand_source"] = info["demand_source"].values
    econ["cop_m"] = list(cop_bm)
    fit = fit_scores(econ, delivered, cop_bm, cap_kw, cfg)
    for k, v in fit.items():
        econ["fit_" + k] = v
    eq = (real or {}).get("equity", {})
    econ["dac"] = [bool(eq.get(str(t), {}).get("dac", False)) for t in c["tract_geoid"]]
    econ["dac_pct"] = [eq.get(str(t), {}).get("pct") for t in c["tract_geoid"]]
    bbl = c["bbl"].values
    chosen, solver = solve_milp(econ, dc_draw, tree, cfg, cap_kw, bbl)
    optimized = evaluate(chosen, econ, delivered, dc_draw, tree, cfg, cap_kw)
    if light:        # Monte Carlo and scenario runs only need the chosen set and its totals
        extra = {}
        if chosen:
            big = max(chosen, key=lambda i: econ["standalone_value"].iloc[i])
            rest = evaluate([i for i in chosen if i != big], econ, delivered, dc_draw, tree, cfg, cap_kw)
            extra = {"top_customer": c["address"].iloc[big],
                     "value_kept_if_top_customer_leaves": rest["annual_value_usd"] / max(optimized["annual_value_usd"], 1),
                     "n_public_housing": int(econ["public_housing"].iloc[chosen].sum()),
                     "n_dac": int(econ["dac"].iloc[chosen].sum())}
        dem = float(info["heat_kwh_year"].sum())
        dsh = (float((econ["dhw_share"].values[chosen] * econ["deliv_kwh"].values[chosen]).sum()
                     / max(econ["deliv_kwh"].values[chosen].sum(), 1e-9)) if chosen else 0.0)
        ledger = party_ledger(chosen, c, econ, cfg, optimized, _SITE.id) if chosen else []
        return dict(c=c, econ=econ, chosen=chosen, optimized=optimized, extra=extra,
                    demand_total_kwh=dem, dhw_share_delivered=dsh, ledger=ledger)
    # same problem without the one-customer cap, to show what the safety rule costs
    free, _ = solve_milp(econ, dc_draw, tree, cfg, cap_kw, bbl, max_share=1.0, insurance=False)
    uncapped = evaluate(free, econ, delivered, dc_draw, tree, cfg, cap_kw)
    stress = stress_tests(chosen, econ, delivered, dc_draw, tree, cfg, cap_kw, optimized, c)
    full = [i for i in range(len(c)) if c["sizing"].iloc[i] == max(cfg["hp_sizing_options"])]
    base = baseline_biggest_first(econ, dc_draw, tree, cap_kw, full)
    baseline = evaluate(base, econ, delivered, dc_draw, tree, cfg, cap_kw)
    phase_of, new_edges_of, order = assign_phases(chosen, econ, tree, cfg)
    # reliability, stakeholders, and what it would take for the customers who did not make the plan
    rel = {i: building_reliability(i, econ, tree, cfg) for i in chosen}
    draw_total = dc_draw[sorted(chosen)].sum(axis=0)
    extras = {"rel": rel, "serves": pipe_serves(chosen, c, econ, tree),
              "firewall": cooling_firewall(cfg, cap_kw, draw_total, _SITE.id),
              "match": match_scorecard(chosen, c, econ, cfg, cap_kw, draw_total, optimized, cop_bm, rel),
              "ledger": party_ledger(chosen, c, econ, cfg, optimized, _SITE.id),
              "next": next_in_line(chosen, c, econ, tree, cfg)}
    extras["system"], extras["edge_sizing"], extras["lat_sizing"] = system_block(
        chosen, c, econ, delivered, dc_draw, demand, tree, cfg, cap_kw, optimized, _SITE, None)
    # who gets heat first: waterfall at each build-out stage, for three scenarios
    tiers_ = econ["tier"].tolist()
    wf = {str(s): waterfall([i for i, p in phase_of.items() if p <= s], tiers_, demand,
                            econ["hp_kw"].values, cop_bm, cfg, cap_kw) for s in (1, 2, 3)}

    if verbose:
        print("MILP:", solver)
        show = lambda name, d: print(f"{name:26s} bldgs={d['n_buildings']:3d} pipe={d['pipe_m']/1000:5.2f}km "
                                    f"capex=${d['capex_usd']/1e6:6.1f}M value=${d['annual_value_usd']/1e6:6.2f}M/yr "
                                    f"heat={d['heat_delivered_gwh']:5.1f}GWh DCuse={d['dc_utilization']:.0%} "
                                    f"(summer {d['dc_utilization_summer']:.0%}) CO2-{d['co2_avoided_t']:,.0f}t")
        show("HeatOS plan", optimized)
        show("No insurance rule", uncapped)
        show("Biggest buildings first", baseline)

    if out:
        export_json(c, econ, delivered, dc_draw, tree, chosen, phase_of, new_edges_of, order, cfg,
                    cap_kw, optimized, baseline, solver, out, uncapped=uncapped,
                    stress=stress, robustness=robustness, scenarios=scenarios, wf=wf, climate=climate, extras=extras)
        if verbose:
            print(f"\nWrote {out} ({Path(out).stat().st_size / 1e6:.1f} MB)")
    return dict(c=c, econ=econ, chosen=chosen, phase_of=phase_of, optimized=optimized, baseline=baseline)


def deal_search(site, levels=(0.0, 0.25, 0.5, 0.75, 1.0), runs=30):
    """Try different shares of the street pipes paid by others, run a short Monte Carlo at each, and report
    every party's chance of winning. The recommended deal is the smallest share where all parties clear the bar."""
    from montecarlo import run_mc
    out = []
    for s in levels:
        # run_mc draws its own random inputs; pass the subsidy as a fixed override through the config seam
        summary = run_mc(n=runs, seed=7, verbose=False, site=site, fixed={"pipe_subsidy_fraction": s}, write=False)
        win = summary["party_win_pct"]
        out.append({"pipe_subsidy_fraction": s, "party_win_pct": win, "n_buildings_median": summary["n_buildings_p10_p50_p90"][1],
                    "value_usd_median": summary["value_usd_p10_p50_p90"][1],
                    "all_parties_clear_80": all(v >= 80 for v in win.values()) if win else False})
        print(f"  deal search: {int(100*s)}% of pipes paid by others -> win % {win}")
    return pick_recommended(out)


def pick_recommended(levels, threshold=80):
    """The recommended deal is the share of street pipes paid by others that makes the WEAKEST party as safe as possible
    (ties go to the share that connects more buildings, then to the larger share). It only 'clears the bar' if that weakest party still wins in at least `threshold`% of scenarios."""
    for r in levels:
        r["weakest_party_win_pct"] = min(r["party_win_pct"].values()) if r["party_win_pct"] else 0
    best = max(levels, key=lambda r: (r["weakest_party_win_pct"], r["n_buildings_median"], r["pipe_subsidy_fraction"]))
    return {"levels": levels, "recommended_share": best["pipe_subsidy_fraction"], "recommended_weakest_win_pct": best["weakest_party_win_pct"],
            "clears_bar": bool(best["weakest_party_win_pct"] >= threshold), "threshold_pct": threshold}


def build_site(site, mc_runs=None):
    """Everything for one site: Monte Carlo, ownership scenarios, climate outlook, then the plan file."""
    from montecarlo import run_mc
    rob = run_mc(site=site, n=mc_runs)   # random scenarios first, so the plan can report robustness
    scen = run_scenarios(site=site)      # who pays for the street pipes?
    clim = run_climate(site=site)        # how does the plan hold up as winters warm?
    deal = deal_search(site)             # what share of the pipes must others pay so every party wins?
    return run_plan(robustness=dict(rob, deal_search=deal), scenarios=scen, climate=clim, site=site)


def refresh_site(site):
    """Re-export a plan using the Monte Carlo, scenarios and climate results already saved in its JSON (seconds, not minutes).
    Use after changing wording or presentation; use build_site when prices, costs or the model change."""
    old = json.loads(get_site(site).plan_path.read_text())
    return run_plan(robustness=old["robustness"], scenarios=old["ownership_scenarios"], climate=old["climate_outlook"], site=site, verbose=False)


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for s in (args or ["chelsea"]):
        print(f"\n===== {s} =====")
        (refresh_site if "--refresh" in sys.argv else build_site)(s)