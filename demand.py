"""
Demand model: one heat-demand curve per building, 12 typical days x 24 hours.

THIS IS THE PLUG-IN POINT FOR THE FORECASTING TEAMMATES: replace build_demand() with a
function that returns the same array shape (n_buildings, 12, 24) in kW of heat.

How the curve is made (from the yearly LL84 fuel numbers):
  1. yearly useful heat = fuel burned x boiler efficiency x share that is heating.
  2. split it into space heating (follows the weather) and hot water (runs all year).
  3. spread each part over the 12 months, then over 24 hours of a typical day.
"""
import json

import numpy as np
import pandas as pd

DAYS = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
KBTU_PER_KWH = 3.412

FUELS = ["steam", "gas", "oil2", "oil4", "oil56", "propane", "elec_heat"]   # heating fuels we can displace


def _daily_shapes():
    """Fraction of a day's heat used in each hour (each shape sums to 1)."""
    h = np.arange(24)
    flat = np.ones(24)
    residential = 1 + 1.2 * np.exp(-((h - 7) / 2.0) ** 2) + 1.0 * np.exp(-((h - 20) / 2.5) ** 2)
    commercial = np.where((h >= 6) & (h <= 19), 1.6, 0.5)
    shapes = {"flat": flat, "residential": residential, "commercial": commercial.astype(float)}
    return {k: v / v.sum() for k, v in shapes.items()}


def eff_by_fuel(cfg):
    return {"steam": cfg["steam_hx_eff"], "gas": cfg["boiler_eff_gas"], "oil2": cfg["boiler_eff_oil"],
            "oil4": cfg["boiler_eff_oil"], "oil56": cfg["boiler_eff_oil"], "propane": cfg["boiler_eff_propane"],
            "elec_heat": cfg["elec_heat_efficiency"]}


def price_by_fuel(cfg):  # $/MMBtu of fuel
    return {f: cfg[f"{f}_price_usd_per_mmbtu"] for f in FUELS}


def ensure_fuel_columns(b):
    """Tables from one site may lack a fuel another site has (e.g. propane): add zero columns."""
    for f in FUELS:
        if f"{f}_kbtu" not in b:
            b[f"{f}_kbtu"] = 0.0
    return b


def pick_candidates(b, cfg):
    """Keep buildings that burn enough fuel and whose energy numbers look sane."""
    b = ensure_fuel_columns(b.copy())
    for f in FUELS:
        b[f"{f}_kbtu"] = b[f"{f}_kbtu"].fillna(0)
    b["heat_fuel_kbtu"] = b[[f"{f}_kbtu" for f in FUELS]].sum(axis=1)
    b["gfa"] = b["gfa_ll84"].where(b["gfa_ll84"] > 0, b["bldgarea"])
    b["fuel_eui"] = b["heat_fuel_kbtu"] / b["gfa"]

    b["exclude_reason"] = ""
    b.loc[b["is_datacenter"], "exclude_reason"] = "is the data center"
    b.loc[~b["has_energy"], "exclude_reason"] = b["exclude_reason"].where(
        b["exclude_reason"] != "", "no energy data")
    b.loc[b["has_energy"] & (b["heat_fuel_kbtu"] < cfg["min_heat_fuel_kbtu"]) & (b["exclude_reason"] == ""),
          "exclude_reason"] = "too little fuel use"
    b.loc[(b["fuel_eui"] > cfg["max_fuel_eui_kbtu_ft2"]) & (b["exclude_reason"] == ""),
          "exclude_reason"] = "suspect LL84 value (EUI too high)"
    b.loc[b["gfa"].isna() & (b["exclude_reason"] == ""), "exclude_reason"] = "no floor area"
    return b


def _blend(mix, profiles):
    """GFA-weighted blend of use profiles for one building. mix = {use type: ft2}."""
    if not mix:
        return dict(profiles["default"], weights={"default": 1.0})
    tot = sum(mix.values())
    w = {u: a / tot for u, a in mix.items()}
    dhw = sum(w[u] * profiles.get(u, profiles["default"])["dhw"] for u in w)
    return {"dhw": dhw, "weights": w}


def build_demand(c, cfg, real=None, monthly=None):
    """
    c: candidate DataFrame (see pick_candidates). Returns
      demand[n, 12, 24]  kW of heat in each hour of each month's typical day
      info DataFrame     yearly heat, hot-water share, month-by-month hot-water fraction, ...

    With real data (cfg["use_real_data"]):
      * months come from the building's own LL84 monthly fuel bills when it has 12 months,
        else from measured NYC heating degree-days;
      * the hot-water part = its summer baseload (July/August use) when measured, else the share
        found in NREL end-use profiles for that kind of building;
      * hours of the day come from NREL ComStock/ResStock hourly profiles for New York.
    Without it, falls back to my hand-made shapes in config.use_profiles.
    """
    use_real = bool(real) and cfg["use_real_data"]
    shapes = _daily_shapes()
    prof = cfg["use_profiles"]
    eff = eff_by_fuel(cfg)
    hdd = np.array(real["weather"]["monthly_hdd"] if use_real else cfg["monthly_hdd"], float)
    w_space = hdd / hdd.sum()
    w_dhw = DAYS * (1 + cfg["dhw_winter_factor"] * hdd / hdd.max())
    w_dhw = w_dhw / w_dhw.sum()
    # climate outlook: warmer winters shrink the SPACE heating part (hot water does not change)
    clim = np.ones(12)
    dF = cfg["climate_warming_f"]
    if use_real and dF > 0:
        tab = real["weather"]["monthly_hdd_by_warming_f"]
        lo, hi = int(np.floor(min(dF, 12))), int(np.ceil(min(dF, 12)))
        mix_ = 0.0 if hi == lo else (min(dF, 12) - lo) / (hi - lo)
        warm = np.array(tab[str(lo)]) * (1 - mix_) + np.array(tab[str(hi)]) * mix_
        clim = np.where(hdd > 5, warm / np.maximum(hdd, 1e-9), 1.0)
    meas = {}
    if use_real and monthly is not None:
        for bbl, g in monthly.groupby("bbl"):
            g = g.sort_values("month")
            meas[bbl] = (g["steam"].values * eff["steam"] + g["gas"].values * eff["gas"]
                         + g["oil"].values * eff["oil2"])          # useful heat per month (kBtu)

    out = np.zeros((len(c), 12, 24))
    rows = []
    for i, r in enumerate(c.itertuples()):
        # 1. yearly useful heat (kWh thermal)
        q = sum(getattr(r, f"{f}_kbtu") * eff[f] for f in FUELS) * cfg["heat_share_of_fuel"] / KBTU_PER_KWH
        mix = json.loads(r.use_mix_json) if isinstance(r.use_mix_json, str) else {}
        weights = ({u: a / sum(mix.values()) for u, a in mix.items()} if mix and sum(mix.values()) > 0
                   else {"default": 1.0})

        # 2. hourly shapes [12,24], blended over the building's use types
        if use_real:
            sh = real["shapes"]
            pick = lambda u: sh.get(u, sh["default"])
            heat_shape = sum(wt * np.array(pick(u)["heat_shape"]) for u, wt in weights.items())
            dhw_shape = sum(wt * np.array(pick(u)["dhw_shape"]) for u, wt in weights.items())
            dhw_model = sum(wt * pick(u)["dhw_share"] for u, wt in weights.items())
        else:
            p = lambda u: prof.get(u, prof["default"])
            heat_shape = sum(wt * np.tile(shapes[p(u)["heat_shape"]], (12, 1)) for u, wt in weights.items())
            dhw_shape = sum(wt * np.tile(shapes[p(u)["dhw_shape"]], (12, 1)) for u, wt in weights.items())
            dhw_model = sum(wt * p(u)["dhw"] for u, wt in weights.items())

        # 3. month split: measured bills if we have them, else weather
        if r.bbl in meas and meas[r.bbl].sum() > 0:
            y = meas[r.bbl]
            shape_m = y / y.sum()
            summer_daily = (y[6] / 31 + y[7] / 31) / 2                  # July, August use per day
            base = min(cfg["dhw_share_cap"], max(0.03, summer_daily * 365 / y.sum()))
            source = "measured monthly bills"
            total_m = q * shape_m
            dhw_m = np.minimum(q * base * DAYS / 365, total_m)
        else:
            dhw_m = q * dhw_model * w_dhw
            total_m = q * (1 - dhw_model) * w_space + dhw_m
            source = "modeled from weather"
        space_m = (total_m - dhw_m) * clim
        total_m = dhw_m + space_m

        out[i] = (space_m / DAYS)[:, None] * heat_shape + (dhw_m / DAYS)[:, None] * dhw_shape
        rows.append({"bbl": r.bbl, "heat_kwh_year": float(total_m.sum()), "climate_ratio": float(total_m.sum() / max(q, 1e-9)),
                     "dhw_share": float(dhw_m.sum() / max(total_m.sum(), 1e-9)),
                     "dhw_frac_m": (dhw_m / np.maximum(total_m, 1e-9)), "demand_source": source})
    info = pd.DataFrame(rows)
    return out, info


def yearly_kwh(arr):
    """Sum a [..., 12, 24] kW array into kWh/year (using days per month)."""
    return (arr.sum(axis=-1) * DAYS).sum(axis=-1)
