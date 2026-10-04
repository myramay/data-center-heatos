"""
Real-data layer: turns the hackathon data pack (data/hackathon/data/...) into small cached
tables the model reads. Run once:   .venv/bin/python realdata.py
Everything here is MEASURED data or a simple summary of it; guesses stay in config.py.

  monthly_heat.csv   per building (BBL) x month: heating fuel burned in 2024 (LL84 monthly)
  realdata.json      weather (monthly degree days, temperatures), NREL hourly load shapes,
                     energy prices, equity (NY State disadvantaged communities), and the
                     data center's own electricity use (the heat-supply estimate)
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from data_load import CACHE

HERE = Path(__file__).parent
PACK = HERE / "data" / "hackathon" / "data"
DAYS = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
MONTH_NUM = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
                                              "Aug", "Sep", "Oct", "Nov", "Dec"])}


# ------------------------------------------------------------ LL84 monthly fuel use
def build_monthly_heat():
    """Heating fuel per building per month (kBtu). Uses 2024, else 2023, if all 12 months exist."""
    b = pd.read_csv(CACHE / "buildings.csv", usecols=["bbl", "prop_ids"]).dropna()
    owner = {pid: bbl for bbl, ids in zip(b["bbl"], b["prop_ids"]) for pid in ids.split("|")}
    m = pd.read_csv(PACK / "site1_nyc" / "ll84_monthly.csv", low_memory=False, dtype={"Property Id": str})
    m = m[m["Property Id"].isin(owner)].copy()
    m["bbl"] = m["Property Id"].map(owner)
    m["month"] = m["Month"].str.split("-").str[-1].map(MONTH_NUM)
    fuel_cols = {"steam": "District Steam Use  (kBtu)", "gas": "Natural Gas Use - Monthly (kBtu)",
                 "oil": None}
    num = lambda c: pd.to_numeric(m[c], errors="coerce").fillna(0)
    m["steam"] = num(fuel_cols["steam"])
    m["gas"] = num(fuel_cols["gas"])
    m["oil"] = sum(num(c) for c in m.columns if c.startswith("Fuel Oil"))
    m["heat_kbtu"] = m["steam"] + m["gas"] + m["oil"]
    out = None
    for year in (2024, 2023):
        y = m[(m["Calendar Year"] == year) & m["month"].notna()]
        t = y.groupby(["bbl", "month"])[["steam", "gas", "oil", "heat_kbtu"]].sum().reset_index()
        full = t.groupby("bbl")["month"].nunique()
        t = t[t["bbl"].isin(full[full == 12].index)]
        t["year"] = year
        out = t if out is None else pd.concat([out, t[~t["bbl"].isin(out["bbl"])]])
    out.to_csv(CACHE / "monthly_heat.csv", index=False)
    print(f"monthly heat: {out['bbl'].nunique()} buildings with 12 complete months")
    return out


# ------------------------------------------------------------ Weather
def build_weather(folder):
    """Monthly heating degree-days (base 65 F) and mean temperature, averaged over complete years."""
    rows = []
    for f in sorted((PACK / "weather" / folder).glob("*.csv")):
        w = pd.read_csv(f, usecols=["DATE", "TMP", "REPORT_TYPE"], low_memory=False)
        w = w[w["REPORT_TYPE"].str.strip() == "FM-15"]
        t = w["TMP"].str.split(",").str[0].astype(float) / 10          # tenths of deg C
        t = t.where(t.abs() < 90)                                      # 999.9 = missing
        w = pd.DataFrame({"t": t.values, "date": pd.to_datetime(w["DATE"]).values})
        w["day"] = w["date"].dt.floor("D")
        daily = w.groupby("day")["t"].mean().dropna()
        d = pd.DataFrame({"t": daily, "month": daily.index.month, "year": daily.index.year})
        d["hdd"] = np.maximum(0, 65 - (d["t"] * 9 / 5 + 32))
        rows.append(d)
    d = pd.concat(rows)
    d = d[d["year"].between(2015, 2024)]
    monthly_hdd = d.groupby(["year", "month"])["hdd"].sum().groupby("month").mean()
    # heating degree-days if every day were dF degrees F warmer (0..12), for the climate outlook
    by_shift = {}
    for dF in range(13):
        h = np.maximum(0, 65 - (d["t"] * 9 / 5 + 32 + dF))
        by_shift[str(dF)] = [round(float(v), 1) for v in
                             h.groupby([d["year"], d["month"]]).sum().groupby("month").mean()]
    return {"monthly_hdd": [round(float(v), 1) for v in monthly_hdd], "monthly_hdd_by_warming_f": by_shift,
            "monthly_mean_temp_c": [round(float(v), 2) for v in d.groupby("month")["t"].mean()],
            "coldest_daily_mean_c": round(float(d["t"].min()), 1)}


# ------------------------------------------------------------ NREL hourly load shapes
NREL = PACK / "nrel_load_profiles"
CS = NREL / "comstock_amy2018_release_3"
RS = NREL / "resstock_amy2018_release_1"
SHAPE_SOURCES = {   # use type -> list of NREL files (heating + hot water timeseries)
    "Multifamily Housing": [RS / "up00-ny-multi-family_with_5plus_units.csv"],
    "Residence Hall/Dormitory": [RS / "up00-ny-multi-family_with_5plus_units.csv"],
    "Senior Living Community": [RS / "up00-ny-multi-family_with_5plus_units.csv"],
    "Hotel": [CS / "up0-g3600610-largehotel.csv"],
    "Fitness Center/Health Club/Gym": [CS / "up0-g3600610-smallhotel.csv"],     # proxy: 24/7 hot water
    "Hospital (General Medical & Surgical)": [CS / "up0-g3600610-hospital.csv"],
    "Office": [CS / "up0-g3600610-largeoffice.csv", CS / "up0-g3600610-mediumoffice.csv"],
    "Medical Office": [CS / "up0-g3600610-outpatient.csv"],
    "K-12 School": [CS / "up0-g3600610-primaryschool.csv", CS / "up0-g3600610-secondaryschool.csv"],
    "College/University": [CS / "up0-g3600610-secondaryschool.csv"],
    "Retail Store": [CS / "up0-g3600610-retailstandalone.csv", CS / "up0-g3600610-retailstripmall.csv"],
    "Food Service": [CS / "up0-g3600610-fullservicerestaurant.csv"],
    "default": [CS / "up0-g3600610-mediumoffice.csv"],
}
HEAT_RE = re.compile(r"out\.(natural_gas|fuel_oil|propane|district_heating)\.heating\.")
DHW_RE = re.compile(r"out\.(natural_gas|fuel_oil|propane|district_heating)\.(water_systems|hot_water)\.")


def _profile(file):
    d = pd.read_csv(file)
    ts = pd.to_datetime(d["timestamp"])
    heat = d[[c for c in d.columns if HEAT_RE.match(c)]].sum(axis=1)
    dhw = d[[c for c in d.columns if DHW_RE.match(c)]].sum(axis=1)
    f = pd.DataFrame({"m": ts.dt.month, "h": ts.dt.hour, "heat": heat, "dhw": dhw})
    H = f.pivot_table(index="m", columns="h", values="heat", aggfunc="mean").values      # [12,24]
    W = f.pivot_table(index="m", columns="h", values="dhw", aggfunc="mean").values
    return H, W


def build_shapes(sources=None):
    """Per use type: average hourly shape of heating and hot water for each month, plus the
    share of heating-fuel energy that goes to hot water (instead of my guesses in config)."""
    out = {}
    for use, files in (sources or SHAPE_SOURCES).items():
        Hs, Ws = zip(*[_profile(f) for f in files])
        H, W = sum(Hs), sum(Ws)
        norm = lambda X: np.where(X.sum(axis=1, keepdims=True) > 0, X / np.maximum(X.sum(axis=1, keepdims=True), 1e-12), 1 / 24)
        tot_h, tot_w = (H.sum(axis=1) * DAYS).sum(), (W.sum(axis=1) * DAYS).sum()
        out[use] = {"heat_shape": norm(H).round(5).tolist(),          # [12][24], each month sums to 1
                    "dhw_shape": norm(W).round(5).tolist(),
                    "dhw_share": round(float(tot_w / max(tot_h + tot_w, 1e-9)), 3),
                    "source": [Path(f).name for f in files]}
        print(f"  shapes {use:42s} hot-water share {out[use]['dhw_share']:.2f}")
    return out


# ------------------------------------------------------------ Prices and equity
def build_prices():
    p = pd.read_csv(PACK / "prices" / "nys_energy_prices_usd_per_mmbtu.csv")
    year = int(p["Year"].max())
    out = {"year": year}
    for sector in ("Commercial", "Residential"):
        r = p[(p["Year"] == year) & (p["Sector"] == sector)].iloc[0]
        out[sector.lower()] = {"gas_usd_per_mmbtu": float(r["Natural Gas"]),
                               "distillate_usd_per_mmbtu": float(r["Distillate"]),
                               "residual_usd_per_mmbtu": None if pd.isna(r["Residual"]) else float(r["Residual"]),
                               "elec_usd_per_kwh": round(float(r["Electricity"]) / 293.07, 4)}
    return out


def build_equity():
    e = pd.read_csv(PACK / "equity" / "nys_dac_2023.csv",
                    usecols=["GEOID", "DAC_Designation", "Percentile_Rank_Combined_NYC", "Population_Count"],
                    dtype={"GEOID": str})
    e = e[e["GEOID"].str.startswith("36061")]
    return {g: {"dac": d == "Designated as DAC", "pct": None if pd.isna(p) else round(float(p), 3)}
            for g, d, p in zip(e["GEOID"], e["DAC_Designation"], e["Percentile_Rank_Combined_NYC"])}


# ------------------------------------------------------------ Data center supply
def build_supply():
    """111 8th Ave's own LL84 meter data. Grid electricity is almost all computers + cooling,
    and nearly every kWh a computer uses leaves the building as heat."""
    d = pd.read_csv(PACK / "site1_nyc" / "ll84_111_8th_ave.csv", low_memory=False)
    num = lambda c: pd.to_numeric(d[c], errors="coerce")
    return {"years": d["Calendar Year"].tolist(),
            "grid_kwh": num("Electricity Use - Grid Purchase (kWh)").tolist(),
            "steam_kbtu": num("District Steam Use (kBtu)").tolist(),
            "office_gfa_ft2": num("Largest Property Use Type - Gross Floor Area (ft²)").tolist(),
            "datacenter_gfa_ft2": num("Data Center - Gross Floor Area (ft²)").tolist()}


# ------------------------------------------------------------ Lansing (Tompkins County)
def lansing_shape_sources():
    """Same NREL end-use profiles, but for Tompkins County's weather and building mix."""
    cs = lambda name: CS / f"up0-g3601090-{name}.csv"
    rs = lambda name: RS / f"up00-ny-{name}.csv"
    return {
        "Single-Family Home": [rs("single-family_detached"), rs("single-family_attached")],
        "Multifamily Housing": [rs("multi-family_with_2_-_4_units"), rs("multi-family_with_5plus_units")],
        "Senior Living Community": [rs("multi-family_with_5plus_units")],
        "K-12 School": [cs("primaryschool"), cs("secondaryschool")],
        "Retail Store": [cs("retailstandalone"), cs("retailstripmall")],
        "Office": [cs("smalloffice"), cs("mediumoffice")],
        "Medical Office": [cs("outpatient")],
        "Municipal/Emergency": [cs("smalloffice")],
        "Worship Facility": [cs("smalloffice")],             # proxy: part-time occupancy
        "Warehouse/Industrial": [cs("warehouse")],
        "Food Service": [cs("fullservicerestaurant")],
        "Hotel": [cs("smallhotel")],
        "default": [cs("smalloffice")],
    }


def greenhouse_shape():
    """Greenhouses have no NREL profile. Placeholder: heat runs all day, a bit more at night, and a large
    'baseload' share because crops need warmth in every month (TODO: grower data)."""
    h = np.arange(24)
    night = 1 + 0.35 * (((h >= 18) | (h <= 6)).astype(float))
    night = (night / night.sum()).round(5).tolist()
    flat = [round(1 / 24, 5)] * 24
    return {"heat_shape": [night] * 12, "dhw_shape": [flat] * 12, "dhw_share": 0.55,
            "source": ["placeholder: crop-year heating, TODO grower data"]}


def build_lansing_real():
    """Everything the model reads for Lansing, saved to data/cache/lansing/realdata.json."""
    P = PACK / "prices"
    oil = pd.read_csv(P / "nys_heating_oil_prices_weekly.csv")["Central New York Average ($/gal)"].tail(8).mean()
    prop = pd.read_csv(P / "nys_propane_prices_weekly.csv")["Central New York Average ($/gal)"].tail(8).mean()
    state = build_prices()
    k = 1.25                                                           # 2021 state table -> today (see config)
    price_overrides = {
        "oil2_price_usd_per_mmbtu": round(float(oil) / 0.1385, 2),       # $/gal / MMBtu per gal
        "oil4_price_usd_per_mmbtu": round(float(oil) / 0.1385 * 0.85, 2),
        "oil56_price_usd_per_mmbtu": round(float(oil) / 0.1385 * 0.80, 2),
        "propane_price_usd_per_mmbtu": round(float(prop) / 0.0915, 2),
        "gas_price_usd_per_mmbtu": round(state["residential"]["gas_usd_per_mmbtu"] * k, 2),
        "elec_price_usd_per_kwh": round(state["commercial"]["elec_usd_per_kwh"] * k, 4),
        "elec_heat_price_usd_per_mmbtu": round(state["residential"]["elec_usd_per_kwh"] * k / 0.0034121, 2),
    }
    shapes = build_shapes(lansing_shape_sources())
    shapes["Greenhouse"] = greenhouse_shape()
    equity = {g: v for g, v in build_equity_county("36109").items()}
    data = {"weather": build_weather("ithaca_airport"), "shapes": shapes, "prices": state,
            "price_overrides": price_overrides, "equity": equity, "supply": None,
            "price_note": {"heating_oil_usd_per_gal": round(float(oil), 2), "propane_usd_per_gal": round(float(prop), 2),
                           "source": "NYS weekly price survey, Central New York, mean of last 8 weeks"}}
    out = CACHE / "lansing"
    out.mkdir(parents=True, exist_ok=True)
    (out / "realdata.json").write_text(json.dumps(data))
    print("Lansing prices:", price_overrides, data["price_note"])
    print("Ithaca HDD (F-days):", sum(data["weather"]["monthly_hdd"]))
    return data


def build_equity_county(prefix="36061"):
    e = pd.read_csv(PACK / "equity" / "nys_dac_2023.csv",
                    usecols=["GEOID", "DAC_Designation", "Percentile_Rank_Combined_NYC", "Percentile_Rank_Combined_ROS"],
                    dtype={"GEOID": str})
    e = e[e["GEOID"].str.startswith(prefix)]
    pct = e["Percentile_Rank_Combined_NYC"].fillna(e["Percentile_Rank_Combined_ROS"])
    return {g: {"dac": d == "Designated as DAC", "pct": None if pd.isna(p) else round(float(p), 3)}
            for g, d, p in zip(e["GEOID"], e["DAC_Designation"], pct)}


def build_all():
    build_monthly_heat()
    data = {"weather_nyc": build_weather("nyc_central_park"),
            "weather_ithaca": build_weather("ithaca_airport"),
            "shapes": build_shapes(), "prices": build_prices(), "equity": build_equity(),
            "supply": build_supply()}
    (CACHE / "realdata.json").write_text(json.dumps(data))
    print("weather NYC:", data["weather_nyc"])
    print("prices:", data["prices"])
    print("supply:", data["supply"])
    return data


_cache = {}


def load(site="chelsea"):
    """Cached tables for the model (build with `python realdata.py` first). Returns (real, monthly_bills)."""
    site = site if isinstance(site, str) else site.id
    if site not in _cache:
        if site == "chelsea":
            real = json.loads((CACHE / "realdata.json").read_text())
            real["weather"] = real["weather_nyc"]
            monthly = pd.read_csv(CACHE / "monthly_heat.csv")
        else:
            real = json.loads((CACHE / site / "realdata.json").read_text())
            monthly = pd.DataFrame(columns=["bbl", "month", "steam", "gas", "oil", "heat_kbtu"])
        _cache[site] = (real, monthly)
    return _cache[site]


if __name__ == "__main__":
    import sys
    if "lansing" in sys.argv[1:]:
        build_lansing_real()
    else:
        build_all()
