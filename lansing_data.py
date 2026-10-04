"""
Lansing data builder: turns county parcels + OpenStreetMap buildings and streets into the same kind of
candidate table the Chelsea model uses. Run once (needs internet the first time; cached after):

    .venv/bin/python lansing_data.py

There is no benchmarking data in Lansing, so demand is MODELED from property records:
  parcel (floor area, year built, heating fuel)  x  typical heat per ft2 for that kind of building,
  scaled to Ithaca's weather. Homes are grouped into neighborhoods at the nearest street intersection, because
  that is how a street pipe actually serves them.
"""
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
from scipy.spatial import cKDTree
from shapely import wkt
from shapely.geometry import Point, Polygon, mapping, shape

import config
from data_load import _to_m
from sites import get_site

SITE = get_site("lansing")
PACK = Path(__file__).parent / "data" / "hackathon" / "data"
RADIUS_M = 8000
HOME_CLUSTER_BASE = 90_000_000            # ids for neighborhood customers
FUEL_OF = {"Gas": "gas", "Propane/LPG": "propane", "Oil": "oil2", "Electric": "elec_heat"}


# ---------------------------------------------------------------- OpenStreetMap
def streets():
    import osmnx as ox
    if not SITE.streets.exists():
        SITE.cache.mkdir(parents=True, exist_ok=True)
        g = ox.graph_from_point(SITE.dc_latlon, dist=RADIUS_M + 3000, network_type="drive", simplify=False,
                                truncate_by_edge=True)
        ox.save_graphml(g, SITE.streets)
    return ox.load_graphml(SITE.streets)


def osm_buildings():
    path = SITE.cache / "osm_buildings.geojson"
    if not path.exists():
        f = ox.features_from_point(SITE.dc_latlon, tags={"building": True}, dist=RADIUS_M)
        f = f[f.geometry.type.isin(["Polygon", "MultiPolygon"])].reset_index()
        keep = [c for c in ["building", "amenity", "name", "height", "building:levels", "shop", "tourism", "office",
                            "leisure", "craft", "landuse", "geometry"] if c in f.columns]
        f[keep].to_file(path, driver="GeoJSON")
    g = gpd.read_file(path)
    utm = g.to_crs(32618)
    g["area_m2"] = utm.geometry.area.values
    cen = utm.geometry.centroid.to_crs(4326)
    g["lon"], g["lat"] = cen.x.values, cen.y.values

    def num(v):
        try:
            return float(str(v).split()[0])
        except (ValueError, TypeError):
            return np.nan
    h = g["height"].map(num) if "height" in g else pd.Series(np.nan, index=g.index)
    lv = g["building:levels"].map(num) if "building:levels" in g else pd.Series(np.nan, index=g.index)
    g["levels"] = lv.fillna(1.0).clip(1, 6)
    g["height_m"] = h.fillna(lv * 3.2).fillna(6.5)
    return g


# ---------------------------------------------------------------- parcels
def parcels(hdd_sum):
    cfg = config.get_config(site="lansing")
    g = json.load(open(PACK / "site2_lansing" / "tompkins_parcels.geojson"))
    rows = []
    for f in g["features"]:
        p = f["properties"]
        p = {k: p.get(k) for k in ["OBJECTID", "PARCEL_ADDR", "LOC_STREET", "PROP_CLASS", "YR_BLT", "SQFT_LIVING", "FUEL_TYPE_DESC",
                                   "BLDG_STYLE_DESC", "PRIMARY_OWNER", "MUNI_NAME", "FULL_MARKET_VAL", "ACRES"]}
        p["geometry"] = shape(f["geometry"])
        rows.append(p)
    d = gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)
    utm = d.to_crs(32618)
    cen = utm.geometry.centroid
    dcx, dcy = _to_m.transform(SITE.dc_latlon[1], SITE.dc_latlon[0])
    d["x"], d["y"] = cen.x.values, cen.y.values
    d = d[np.hypot(d["x"] - dcx, d["y"] - dcy) <= RADIUS_M].copy()
    cl = gpd.GeoSeries(gpd.points_from_xy(d["x"], d["y"]), crs=32618).to_crs(4326)
    d["lon"], d["lat"] = cl.x.values, cl.y.values
    d["pc"] = pd.to_numeric(d["PROP_CLASS"], errors="coerce").fillna(0).astype(int)

    # census tract + disadvantaged-community flag (tract polygons come with the DAC file)
    dac = pd.read_csv(PACK / "equity" / "nys_dac_2023.csv", usecols=["the_geom", "GEOID", "DAC_Designation", "County"], dtype={"GEOID": str})
    dac = dac[dac["County"] == "Tompkins"]
    tr = gpd.GeoDataFrame(dac[["GEOID", "DAC_Designation"]], geometry=dac["the_geom"].map(wkt.loads), crs=4326)
    pts = gpd.GeoDataFrame(d[["OBJECTID"]], geometry=gpd.points_from_xy(d["lon"], d["lat"]), crs=4326)
    j = gpd.sjoin(pts, tr, how="left", predicate="within").drop_duplicates("OBJECTID").set_index("OBJECTID")
    d["tract_geoid"] = d["OBJECTID"].map(j["GEOID"])
    d["dac"] = d["OBJECTID"].map(j["DAC_Designation"]).eq("Designated as DAC")
    return d


def use_of_class(pc, has_greenhouse):
    """County property class -> the use types the demand model knows. None = not a heat customer."""
    if 200 <= pc < 300:
        return "Single-Family Home"
    if pc in (411,):
        return "Multifamily Housing"
    if pc in (414, 415, 417):
        return "Hotel"
    if 100 <= pc < 200:
        return "Greenhouse" if has_greenhouse else None
    if pc in (421, 422, 424, 425, 426, 429):
        return "Food Service"
    if pc in (464, 465, 470, 480):
        return "Office"
    if 400 <= pc < 500:
        return "Retail Store"
    if pc in (612, 613, 614, 615):
        return "K-12 School"
    if pc in (620, 621, 622, 630):
        return "Worship Facility"
    if pc in (631, 632, 633, 634):
        return "Medical Office"
    if pc in (651, 652, 662, 650, 660):
        return "Municipal/Emergency"
    if 700 <= pc < 800:
        return "Warehouse/Industrial"
    return None


# ---------------------------------------------------------------- build
def build():
    real = json.loads((SITE.cache / "realdata.json").read_text())
    hdd = sum(real["weather"]["monthly_hdd"])
    cfg = config.get_config(site="lansing")
    eui = cfg["parcel_eui_kbtu_per_ft2_per_1000hdd"]

    def era(y):
        for yr, f in cfg["era_factor"]:
            if (y or 0) < yr:
                return f
        return 1.0

    G = streets()
    bld = osm_buildings()
    par = parcels(hdd)
    print(f"parcels within {RADIUS_M/1000:.0f} km: {len(par)}   OSM buildings: {len(bld)}")

    # which parcel is each OSM building on?
    bp = gpd.GeoDataFrame(bld[["area_m2", "levels", "height_m", "building", "amenity"]],
                          geometry=gpd.points_from_xy(bld["lon"], bld["lat"]), crs=4326)
    pg = gpd.GeoDataFrame(par[["OBJECTID"]], geometry=par.geometry, crs=4326)
    jb = gpd.sjoin(bp, pg, how="left", predicate="within").drop_duplicates(subset=None)
    jb = jb[~jb.index.duplicated(keep="first")]
    bld["parcel"] = jb["OBJECTID"].reindex(bld.index).values

    owner_plant = par["PRIMARY_OWNER"].fillna("").str.contains("Cayuga Operating", case=False)
    plant_ids = set(par.loc[owner_plant, "OBJECTID"])
    par["greenhouse"] = par["OBJECTID"].isin(set(bld.loc[bld["building"].eq("greenhouse"), "parcel"].dropna().astype(int)))
    par["use"] = [use_of_class(pc, gh) for pc, gh in zip(par["pc"], par["greenhouse"])]
    par = par[~par["OBJECTID"].isin(plant_ids) & par["use"].notna()].copy()

    # floor area: homes from the tax roll; everything else from building footprints x floors
    ba = bld.dropna(subset=["parcel"]).assign(parcel=lambda x: x["parcel"].astype(int))
    ba["fl_ft2"] = ba["area_m2"] * ba["levels"] * 10.764
    area_by_parcel = ba.groupby("parcel")["fl_ft2"].sum()
    par["area_ft2"] = np.where(par["use"].eq("Single-Family Home"), par["SQFT_LIVING"].fillna(0),
                               par["OBJECTID"].map(area_by_parcel).fillna(0))
    par = par[par["area_ft2"] > 300].copy()
    par["year"] = par["YR_BLT"].fillna(0).where(lambda s: s > 1700, 0)

    # fuel burned today (kBtu): typical heat per ft2 x weather x age; fuel from the tax roll
    base = par["use"].map(lambda u: eui.get(u, eui["default"])) * hdd / 1000
    par["fuel_kbtu"] = base * par["area_ft2"] * par["year"].map(era)
    gh = par["use"].eq("Greenhouse")
    par.loc[gh, "fuel_kbtu"] = par.loc[gh, "area_ft2"] * cfg["greenhouse_fuel_kbtu_per_ft2_year"]
    par["fuel"] = par["FUEL_TYPE_DESC"].map(FUEL_OF)
    par.loc[par["use"].ne("Single-Family Home") & par["fuel"].isna(), "fuel"] = "gas"   # assume gas when not recorded
    par = par[par["fuel"].notna()].copy()

    # -------- homes -> neighborhoods at the nearest street intersection
    nodes = pd.DataFrame([(n, d["x"], d["y"]) for n, d in G.nodes(data=True)], columns=["node", "lon", "lat"])
    nx_, ny_ = _to_m.transform(nodes["lon"].values, nodes["lat"].values)
    tree = cKDTree(np.c_[nx_, ny_])
    homes = par[par["use"].eq("Single-Family Home")].copy()
    # group homes into ~200 m neighborhoods; each neighborhood connects at the street node nearest its center
    homes["cell"] = list(zip((homes["x"] // 200).astype(int), (homes["y"] // 200).astype(int)))
    cx_ = homes.groupby("cell")["x"].transform("mean"); cy_ = homes.groupby("cell")["y"].transform("mean")
    _, nidx = tree.query(np.c_[cx_, cy_])
    homes["node_i"] = nidx
    homes["dist"] = np.hypot(homes["x"] - nx_[nidx], homes["y"] - ny_[nidx])           # service line to the neighborhood's node
    homes = homes[homes["dist"] <= cfg["home_max_distance_to_street_m"]].copy()
    homes["low"] = homes["BLDG_STYLE_DESC"].fillna("").str.contains("Manufd|Mobile", case=False) | homes["dac"]
    rows, home_cluster_of = [], {}
    for k, (ni, h) in enumerate(homes.groupby("node_i")):
        cid = HOME_CLUSTER_BASE + int(ni)
        fuel = h.groupby("fuel")["fuel_kbtu"].sum()
        street = h["LOC_STREET"].fillna("").replace("", np.nan).dropna()
        label = (street.mode().iloc[0] if len(street) else "local road") + " area"
        w = h["area_ft2"] / h["area_ft2"].sum()
        row = {"bbl": cid, "address": f"{label} ({len(h)} homes)", "latitude": float(nodes.lat[ni]), "longitude": float(nodes.lon[ni]),
               "ptype": f"Neighborhood of {len(h)} homes", "use_mix_json": json.dumps({"Single-Family Home": float(h["area_ft2"].sum())}),
               "gfa_ll84": float(h["area_ft2"].sum()), "bldgarea": float(h["area_ft2"].sum()),
               "yearbuilt": float((h["year"].replace(0, np.nan) * w).sum() / w[h["year"] > 0].sum()) if (h["year"] > 0).any() else 1975.0,
               "ownername": "Households", "equity_flag": bool(h["low"].mean() >= 0.5), "cluster_homes": True,
               "n_units": int(len(h)), "service_m": float(h["dist"].sum()),
               "low_temp": bool(((h["year"] >= cfg["low_temp_year_built"]) * w).sum() >= 0.5),
               "tract_geoid": h["tract_geoid"].mode().iloc[0] if h["tract_geoid"].notna().any() else None}
        for f in ["gas", "propane", "oil2", "elec_heat"]:
            row[f"{f}_kbtu"] = float(fuel.get(f, 0.0))
        rows.append(row)
        for oid in h["OBJECTID"]:
            home_cluster_of[int(oid)] = cid

    # -------- everyone else: one customer per parcel
    others = par[par["use"].ne("Single-Family Home")]
    cand_of_parcel = dict(home_cluster_of)
    big = ba.sort_values("fl_ft2", ascending=False).drop_duplicates("parcel").set_index("parcel")
    for _, p in others.iterrows():
        oid = int(p["OBJECTID"])
        b = big.loc[oid] if oid in big.index else None
        row = {"bbl": oid, "address": p["PARCEL_ADDR"] or f"{p['use']} (parcel {oid})",
               "latitude": float(b["lat"]) if b is not None and "lat" in b else float(p["lat"]),
               "longitude": float(b["lon"]) if b is not None and "lon" in b else float(p["lon"]),
               "ptype": p["use"], "use_mix_json": json.dumps({p["use"]: float(p["area_ft2"])}),
               "gfa_ll84": float(p["area_ft2"]), "bldgarea": float(p["area_ft2"]), "yearbuilt": float(p["year"]),
               "ownername": p["PRIMARY_OWNER"], "equity_flag": False, "cluster_homes": False, "n_units": 1, "service_m": 0.0,
               "low_temp": bool(p["use"] == "Greenhouse" or p["year"] >= cfg["low_temp_year_built"]),
               "tract_geoid": p["tract_geoid"]}
        for f in ["gas", "propane", "oil2", "elec_heat"]:
            row[f"{f}_kbtu"] = float(p["fuel_kbtu"]) if p["fuel"] == f else 0.0
        rows.append(row)
        cand_of_parcel[oid] = oid

    # -------- proposed anchor customers at the data center fence line (placeholders from config)
    dcx0, dcy0 = _to_m.transform(SITE.dc_latlon[1], SITE.dc_latlon[0])
    from pyproj import Transformer as _T
    inv = _T.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    anchor_feats = []
    for k, a in enumerate(cfg["proposed_anchors"]):
        lon0, lat0 = inv.transform(dcx0 + a["dx_m"], dcy0 + a["dy_m"])
        area = float(a["area_ft2"])
        row = {"bbl": 80_000_000 + k, "address": a["name"], "latitude": lat0, "longitude": lon0, "ptype": a["use"] + " (proposed)",
               "use_mix_json": json.dumps({a["use"]: area}), "gfa_ll84": area, "bldgarea": area, "yearbuilt": 2030.0,
               "ownername": "To be developed", "equity_flag": False, "cluster_homes": False, "n_units": 1, "service_m": 0.0,
               "low_temp": True, "baseload_low_temp": True, "tract_geoid": None, "proposed": True}
        for f in ["gas", "propane", "oil2", "elec_heat"]:
            row[f"{f}_kbtu"] = area * cfg["greenhouse_fuel_kbtu_per_ft2_year"] if a["fuel"] == f else 0.0
        rows.append(row)
        w, h = math.sqrt(area * 0.0929 * 2), math.sqrt(area * 0.0929 / 2)          # 2:1 rectangle, metres
        ring = [inv.transform(dcx0 + a["dx_m"] + dx, dcy0 + a["dy_m"] + dy)
                for dx, dy in [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2), (-w / 2, -h / 2)]]
        anchor_feats.append({"type": "Feature", "properties": {"bbl": 80_000_000 + k, "bin": "proposed", "height_ft": 6 / 0.3048},
                             "geometry": mapping(Polygon(ring))})

    c = pd.DataFrame(rows)
    if "proposed" not in c:
        c["proposed"] = False
    c["proposed"] = c["proposed"].fillna(False)
    if "baseload_low_temp" not in c:
        c["baseload_low_temp"] = False
    c["baseload_low_temp"] = c["baseload_low_temp"].fillna(False)
    # columns the engine expects (LL84-only ones are empty here)
    for col in ["steam_kbtu", "oil4_kbtu", "oil56_kbtu", "elec_kwh", "ghg_tco2e", "hot_water_use_gfa"]:
        c[col] = 0.0
    c["site_kbtu"] = c[["gas_kbtu", "propane_kbtu", "oil2_kbtu", "elec_heat_kbtu"]].sum(axis=1)
    c["borough"] = "LAN"; c["bldgclass"] = ""; c["has_ll84"] = False; c["is_datacenter"] = False
    c["estimated"] = True; c["calendar_year"] = 2026; c["prop_ids"] = ""; c["height_ft"] = np.nan; c["dist_m"] = 0.0
    c["numfloors"] = 1; c["unitsres"] = c["n_units"]; c["landuse"] = 0; c["ownertype"] = ""

    # -------- footprints for the map; bbl = the customer the building belongs to (0 = not a customer)
    bld["bbl"] = [cand_of_parcel.get(int(p), 0) if pd.notna(p) else 0 for p in bld["parcel"]]
    hmax = bld[bld["bbl"] > 0].groupby("bbl")["height_m"].max()
    c["height_ft"] = c["bbl"].map(hmax).fillna(6.5) / 0.3048
    feats = [{"type": "Feature", "properties": {"bbl": int(r.bbl), "bin": "", "height_ft": float(r.height_m / 0.3048)},
              "geometry": mapping(r.geometry)} for r in bld.itertuples()] + anchor_feats
    # the proposed data center: three buildings in a row at the old plant site (layout not published: schematic)
    dcx, dcy = _to_m.transform(SITE.dc_latlon[1], SITE.dc_latlon[0])
    from pyproj import Transformer
    back = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    for k in (-1, 0, 1):
        x0, y0 = dcx + k * 260, dcy
        ring = [back.transform(x0 + dx, y0 + dy) for dx, dy in [(-100, -45), (100, -45), (100, 45), (-100, 45), (-100, -45)]]
        feats.append({"type": "Feature", "properties": {"bbl": SITE.dc_id, "bin": "dc", "height_ft": 20 / 0.3048},
                      "geometry": mapping(Polygon(ring))})
    SITE.cache.mkdir(parents=True, exist_ok=True)
    SITE.footprints.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    c.to_csv(SITE.buildings_csv, index=False)

    fuel_gbtu = c[["gas_kbtu", "propane_kbtu", "oil2_kbtu", "elec_heat_kbtu"]].sum() / 1e6
    print(f"\ncustomers: {len(c)}  (neighborhoods of homes: {int(c.cluster_homes.sum())}, other buildings: {int((~c.cluster_homes).sum())})")
    print("heating fuel burned today (GBtu/yr):", fuel_gbtu.round(0).to_dict(), " total", round(float(fuel_gbtu.sum())))
    print(c.groupby("ptype").size().sort_values(ascending=False).head(12).to_string())
    print("equity-priority neighborhoods:", int(c.equity_flag.sum()), "| footprints:", len(feats))


if __name__ == "__main__":
    build()
