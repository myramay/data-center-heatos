"""
Fill in buildings that have no LL84 energy data (about 80% of lots, mostly small).

Idea: look at the buildings that DO have measured fuel use, find the typical fuel use per
square foot for each kind of building (using the PLUTO building class), and apply that to
the buildings with no data. These rows are marked estimated=True so the offer says so.
"""
import json

import numpy as np
import pandas as pd

from demand import FUELS, ensure_fuel_columns

# PLUTO building class first letter -> LL84 use type (used for hot-water profile and LL97 group)
USE_BY_CLASS = {
    "B": None, "C": "Multifamily Housing", "D": "Multifamily Housing", "R": "Multifamily Housing",
    "S": "Multifamily Housing", "N": "Multifamily Housing", "H": "Hotel", "O": "Office", "L": "Office",
    "K": "Retail Store", "W": "K-12 School", "I": "Office", "Q": "Fitness Center/Health Club/Gym",
}


def use_of(bldgclass):
    letter = str(bldgclass)[:1]
    return USE_BY_CLASS.get(letter, "default")


def fill_missing(b, cfg):
    """Return b with estimated fuel use added for lots that have none. Adds columns
    `estimated` (bool) and `has_energy` (measured or estimated)."""
    b = ensure_fuel_columns(b.copy())
    b["estimated"] = False
    b["has_energy"] = b["has_ll84"]
    if not cfg["estimate_missing_buildings"]:
        return b

    # --- typical fuel use per ft2 of each kind, from measured buildings with sane numbers
    m = b[b["has_ll84"]].copy()
    m["fuel"] = m[[f"{f}_kbtu" for f in FUELS]].fillna(0).sum(axis=1)
    m["gfa"] = m["gfa_ll84"].where(m["gfa_ll84"] > 0, m["bldgarea"])
    m["eui"] = m["fuel"] / m["gfa"]
    m = m[(m["eui"] > 5) & (m["eui"] < cfg["max_fuel_eui_kbtu_ft2"])]
    m["use"] = m["bldgclass"].map(use_of)
    q = cfg["estimated_eui_quantile"]
    overall = m["eui"].quantile(q)
    by_use = {u: (g["eui"].quantile(q) if len(g) >= cfg["estimated_min_samples"] else overall)
              for u, g in m.groupby("use")}

    # --- apply to lots with no energy data
    skip = set(cfg["no_heat_class_letters"])
    todo = b[(~b["has_ll84"]) & b["bldgarea"].notna() & (b["bldgarea"] > 0)
             & ~b["bldgclass"].fillna("?").str[:1].isin(skip) & ~b["is_datacenter"]].index
    fuel_col = f"{cfg['estimated_fuel']}_kbtu"
    for i in todo:
        r = b.loc[i]
        use = use_of(r["bldgclass"])
        eui = by_use.get(use, overall)
        b.at[i, "gfa_ll84"] = r["bldgarea"]
        b.at[i, fuel_col] = r["bldgarea"] * eui
        b.at[i, "site_kbtu"] = r["bldgarea"] * eui
        b.at[i, "ptype"] = use if use not in (None, "default") else "Other"
        b.at[i, "use_mix_json"] = json.dumps({use if use else "default": float(r["bldgarea"])})
        b.at[i, "estimated"] = True
        b.at[i, "has_energy"] = True
    return b
