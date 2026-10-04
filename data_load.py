"""
HeatOS step 1: load real NYC data for buildings within 1 km of 111 8th Ave.

Inputs (data/raw/, downloaded from NYC Open Data / NYC Planning):
  pluto.csv       MapPLUTO: year built, building class, floor area, lot centroid
  footprints.csv  Building Footprints: roof height + polygon shape (per BIN)
  ll84.csv        Local Law 84 benchmarking: energy use by fuel (incl. steam)

Outputs (data/cache/, small, so later steps run offline):
  buildings.csv      one row per BBL: PLUTO + LL84 columns joined on BBL
  footprints.geojson one polygon per building (BIN) with height, linked to BBL
  streets.graphml    OSM street network around the data center (see load_streets)

Run:  .venv/bin/python data_load.py
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import mapping

HERE = Path(__file__).parent
RAW = HERE / "data" / "raw"
CACHE = HERE / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

# 111 8th Ave = Manhattan block 739 lot 1 (PLUTO centroid).
DC_BBL = 1007390001
DC_LATLON = (40.74136, -74.003208)
RADIUS_M = 1000

# Lat/lon -> metres (UTM zone 18N) so "1 km" is a real distance.
_to_m = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True)
DC_XY = _to_m.transform(DC_LATLON[1], DC_LATLON[0])


def to_bbl(x):
    """Turn '1007390001', 1007390001.0 or '1007390001.0' into an int; else NaN."""
    try:
        return int(float(str(x).split(",")[0].split(";")[0].strip()))
    except (ValueError, TypeError):
        return np.nan


def num(series):
    """LL84 stores numbers as text, with 'Not Available' for blanks."""
    return pd.to_numeric(series.astype(str).str.replace(",", ""), errors="coerce")


# ---------------------------------------------------------------- MapPLUTO
def load_pluto():
    cols = ["bbl", "address", "borough", "bldgclass", "landuse", "ownertype",
            "yearbuilt", "bldgarea", "numfloors", "unitsres", "latitude", "longitude",
            "ownername", "bct2020"]
    p = pd.read_csv(RAW / "pluto.csv", usecols=cols, low_memory=False)
    p = p.dropna(subset=["latitude", "longitude"])
    p["bbl"] = p["bbl"].map(to_bbl).astype("int64")
    # Census tract GEOID (11 digits) so we can look up NY State disadvantaged-community status.
    # Manhattan = county 061. bct2020 looks like '1007000' (borough digit + 6-digit tract).
    p["tract_geoid"] = p["bct2020"].map(lambda v: "36061" + str(int(v))[1:].zfill(6) if pd.notna(v) else None)
    p["x_m"], p["y_m"] = _to_m.transform(p["longitude"].values, p["latitude"].values)
    p["dist_m"] = np.hypot(p["x_m"] - DC_XY[0], p["y_m"] - DC_XY[1])  # straight line, only for the cut
    return p[p["dist_m"] <= RADIUS_M].copy()


# ---------------------------------------------------------------- LL84
def load_ll84():
    """Latest year per BBL, energy by fuel in kBtu, summed over buildings on the lot."""
    raw = pd.read_csv(RAW / "ll84.csv", low_memory=False)
    raw["bbl"] = raw["NYC Borough, Block and Lot (BBL)"].map(to_bbl)
    raw = raw.dropna(subset=["bbl"])
    raw["bbl"] = raw["bbl"].astype("int64")

    fuels = {  # output name -> LL84 column (all in kBtu except electricity)
        "steam_kbtu": "District Steam Use (kBtu)",
        "gas_kbtu": "Natural Gas Use (kBtu)",
        "oil2_kbtu": "Fuel Oil #2 Use (kBtu)",
        "oil4_kbtu": "Fuel Oil #4 Use (kBtu)",
        "oil56_kbtu": "Fuel Oil #5 & 6 Use (kBtu)",
        "elec_kwh": "Electricity Use - Grid Purchase (kWh)",
        "site_kbtu": "Site Energy Use (kBtu)",
        "ghg_tco2e": "Total (Location-Based) GHG Emissions (Metric Tons CO2e)",
        "gfa_ll84": "Largest Property Use Type - Gross Floor Area (ft²)",
    }
    for out, col in fuels.items():
        raw[out] = num(raw[col])
    raw["calendar_year"] = raw["Calendar Year"]
    raw["ptype"] = raw["Primary Property Type - Portfolio Manager-Calculated"]

    # Floor area of every use type, so step 2 can spot laundromats/gyms/hotels/etc.
    gfa_cols = [c for c in raw.columns if c.endswith(" - Gross Floor Area (ft²)")
                and "Largest" not in c and "Data Center" not in c]
    use_gfa = {re.sub(r" ?- Gross Floor Area.*", "", c).strip(): num(raw[c]) for c in gfa_cols}
    use_gfa = pd.DataFrame(use_gfa).fillna(0)
    raw["hot_water_use_gfa"] = use_gfa[[c for c in use_gfa.columns if any(
        k in c for k in ["Hotel", "Hospital", "Fitness", "Residence Hall", "Senior Living",
                         "Multifamily", "Barracks", "Laundry", "Pool", "Recreation"])]].sum(axis=1)
    raw["use_mix_json"] = [json.dumps({k: float(v) for k, v in row.items() if v > 0})
                           for row in use_gfa.to_dict("records")]

    # Keep only the newest year that each BBL reported.
    raw = raw[raw["calendar_year"] == raw.groupby("bbl")["calendar_year"].transform("max")]
    # Biggest row per lot supplies the label; energy is summed over rows (multi-building lots).
    raw = raw.sort_values("gfa_ll84", ascending=False)
    label = raw.groupby("bbl").first()[["ptype", "calendar_year", "use_mix_json",
                                        "Year Built", "Address 1"]]
    # Keep the LL84 Property IDs of every row on the lot (the monthly file is keyed by these).
    label["prop_ids"] = raw.groupby("bbl")["Property ID"].apply(
        lambda s: "|".join(str(int(float(x))) for x in s.dropna().unique()))
    sums = raw.groupby("bbl")[list(fuels) + ["hot_water_use_gfa"]].sum(min_count=1)
    n = raw.groupby("bbl").size().rename("ll84_rows")
    return label.join(sums).join(n).reset_index()


# ---------------------------------------------------------------- Footprints
def load_footprints(bbls):
    """Polygons + heights for the buildings on our lots. File is large, so read in chunks."""
    keep = []
    use = ["the_geom", "BIN", "BASE_BBL", "Height Roof", "Construction Year",
           "Feature Code", "Ground Elevation", "LAST_STATUS_TYPE"]
    for chunk in pd.read_csv(RAW / "footprints.csv", usecols=use, chunksize=100_000,
                             dtype=str, low_memory=False):
        chunk["bbl"] = chunk["BASE_BBL"].map(to_bbl)
        chunk = chunk[chunk["bbl"].isin(bbls)]
        if len(chunk):
            keep.append(chunk)
    f = pd.concat(keep, ignore_index=True)
    f["bbl"] = f["bbl"].astype("int64")
    f["height_ft"] = num(f["Height Roof"])
    f["bin"] = f["BIN"]
    f["geometry"] = f["the_geom"].map(wkt.loads)
    # Drop demolished buildings if the file flags them.
    f = f[~f["LAST_STATUS_TYPE"].fillna("").str.contains("Demolition", case=False)]
    return f[["bin", "bbl", "height_ft", "geometry"]]


# ---------------------------------------------------------------- Streets
def load_streets():
    """Walkable+drivable street network around the data center. Downloaded once, then cached."""
    import osmnx as ox
    path = CACHE / "streets.graphml"
    if path.exists():
        return ox.load_graphml(path)
    # Query a bit wider than 1 km so routes to buildings at the edge don't get cut off.
    g = ox.graph_from_point(DC_LATLON, dist=RADIUS_M + 500, network_type="drive",
                            simplify=True)
    ox.save_graphml(g, path)
    return g


# ---------------------------------------------------------------- Main
def main():
    pluto = load_pluto()
    print(f"PLUTO lots within {RADIUS_M} m: {len(pluto)}")

    ll84 = load_ll84()
    b = pluto.merge(ll84, on="bbl", how="left")
    b["has_ll84"] = b["site_kbtu"].notna()
    print(f"  with LL84 energy data: {b['has_ll84'].sum()} "
          f"({b['has_ll84'].mean():.0%} of lots)")

    fp = load_footprints(set(b["bbl"]))
    print(f"Footprints on those lots: {len(fp)} buildings on {fp['bbl'].nunique()} lots")
    # Height per lot = tallest building; used when a lot has several footprints.
    b = b.merge(fp.groupby("bbl")["height_ft"].max().rename("height_ft"), on="bbl", how="left")
    b["is_datacenter"] = b["bbl"] == DC_BBL

    # Keep only footprints of lots we kept.
    feats = [{"type": "Feature",
              "properties": {"bin": r.bin, "bbl": int(r.bbl),
                             "height_ft": None if pd.isna(r.height_ft) else float(r.height_ft)},
              "geometry": mapping(r.geometry)} for r in fp.itertuples()]
    (CACHE / "footprints.geojson").write_text(json.dumps({"type": "FeatureCollection",
                                                          "features": feats}))
    b.to_csv(CACHE / "buildings.csv", index=False)

    g = load_streets()
    print(f"Street graph: {len(g.nodes)} nodes, {len(g.edges)} edges")

    # ---- quality report: what a judge or teammate would ask first
    e = b[b["has_ll84"]]
    heat_fuels = e[["steam_kbtu", "gas_kbtu", "oil2_kbtu", "oil4_kbtu", "oil56_kbtu"]].fillna(0)
    e = e.assign(heat_fuel_kbtu=heat_fuels.sum(axis=1))
    print("\nLL84 buildings with fuel use > 0:", int((e["heat_fuel_kbtu"] > 0).sum()))
    print("Total fuel (steam+gas+oil) in area: "
          f"{e['heat_fuel_kbtu'].sum() / 1e6:,.0f} GBtu/yr")
    print("Has footprint height:", f"{b['height_ft'].notna().mean():.0%} of lots")
    print("Year built missing/0 :", int((b['yearbuilt'].fillna(0) < 1800).sum()))
    print("\nTop 10 by fuel use:")
    print(e.nlargest(10, "heat_fuel_kbtu")[["address", "ptype", "yearbuilt",
          "bldgarea", "heat_fuel_kbtu", "dist_m"]].to_string(index=False))
    print("\nProperty types (LL84):")
    print(e["ptype"].value_counts().head(12).to_string())


if __name__ == "__main__":
    main()
