"""Download the geography the 3D model is drawn from, once, into web/public/geo/.

    python -m scripts.fetch_geo

- Land: US Census Bureau cartographic boundary counties (1:500k).
- Water: Census TIGER area-water polygons (Hudson, East River, harbor; Cayuga
  Lake), drawn over the land.
- Skyline (Chelsea): tall buildings (>= 90 m) from OpenStreetMap, for context.

Everything is converted to local metres (east, north) from the site origin and
simplified. No map-tile service is used at runtime.
"""

from __future__ import annotations

import io
import json
import math
import sys
import zipfile
from pathlib import Path

import httpx
import shapefile  # pyshp

from engine.config import load_site

OUT = Path(__file__).resolve().parents[1] / "web" / "public" / "geo"
CACHE = Path(__file__).resolve().parents[1] / ".geo-cache"
COUNTIES = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip"
AREAWATER = "https://www2.census.gov/geo/tiger/TIGER2023/AREAWATER/tl_2023_{fips}_areawater.zip"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
EXTENT_M = {"chelsea": 14000, "lansing": 16000}
WATER_COUNTIES = {
    "chelsea": ["36061", "36047", "36081", "36085", "36005", "34017", "34003", "34013", "34039"],   # NYC boroughs + Hudson, Bergen, Essex, Union NJ
    "lansing": ["36109", "36011", "36099"],                                                       # Tompkins, Cayuga, Seneca
}


def download(url: str) -> bytes:
    CACHE.mkdir(exist_ok=True)
    f = CACHE / url.rsplit("/", 1)[-1]
    if not f.exists():
        print(f"  downloading {url}", flush=True)
        r = httpx.get(url, timeout=300, follow_redirects=True, headers={"User-Agent": "HeatOS hackathon"})
        r.raise_for_status()
        f.write_bytes(r.content)
    return f.read_bytes()


def reader(zbytes: bytes) -> shapefile.Reader:
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    base = next(n[:-4] for n in z.namelist() if n.endswith(".shp"))
    return shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), dbf=io.BytesIO(z.read(base + ".dbf")),
                            shx=io.BytesIO(z.read(base + ".shx")))


def local(site: str):
    cfg = load_site(site)
    lat0, lon0 = cfg.site.lat, cfg.site.lon
    kx, ky = 111_320 * math.cos(math.radians(lat0)), 111_320
    return lambda lon, lat: ((lon - lon0) * kx, (lat - lat0) * ky)


def simplify(pts: list[tuple[float, float]], min_step: float) -> list[list[float]]:
    out = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= min_step:
            out.append(p)
    return [[round(x, 1), round(y, 1)] for x, y in out]


def rings(shape) -> list[list[tuple[float, float]]]:
    parts = list(shape.parts) + [len(shape.points)]
    return [shape.points[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]


def polygons_near(r: shapefile.Reader, to_local, extent: float, min_step: float, keep=lambda rec: True):
    out = []
    for sr in r.iterShapeRecords():
        if not keep(sr.record):
            continue
        for ring in rings(sr.shape):
            pts = [to_local(lon, lat) for lon, lat in ring]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if max(xs) < -extent or min(xs) > extent or max(ys) < -extent or min(ys) > extent:
                continue
            simp = simplify(pts, min_step)
            if len(simp) >= 4:
                out.append(simp)
    return out


def skyline(site: str) -> list[dict]:
    cfg = load_site(site)
    q = (f'[out:json][timeout:120];(way["building"]["height"](around:7500,{cfg.site.lat},{cfg.site.lon});'
         f'way["building"]["building:levels"](around:7500,{cfg.site.lat},{cfg.site.lon}););out tags geom;')
    to_local = local(site)
    for url in OVERPASS:
        try:
            data = httpx.post(url, data={"data": q}, timeout=200, headers={"User-Agent": "HeatOS hackathon"}).json()
            break
        except Exception as e:
            print(f"  {url}: {e}", file=sys.stderr)
    else:
        return []
    out = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        try:
            h = float(str(t.get("height", "0")).split()[0].replace("m", "")) or float(t.get("building:levels", 0)) * 3.6
        except ValueError:
            continue
        if h < 90 or len(el.get("geometry", [])) < 4:
            continue
        pts = simplify([to_local(g["lon"], g["lat"]) for g in el["geometry"]], 2.0)
        if len(pts) >= 4:
            out.append({"h": round(h, 1), "p": pts, **({"n": t["name"]} if "name" in t else {})})
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    counties = reader(download(COUNTIES))
    for site in ("chelsea", "lansing"):
        to_local = local(site)
        ext = EXTENT_M[site]
        land = polygons_near(counties, to_local, ext, 25 if site == "chelsea" else 60)
        water = []
        for fips in WATER_COUNTIES[site]:
            r = reader(download(AREAWATER.format(fips=fips)))
            water += polygons_near(r, to_local, ext, 20 if site == "chelsea" else 40,
                                   keep=lambda rec: rec["AWATER"] > (3e4 if site == "chelsea" else 2e6))
        doc = {"site": site, "extent_m": ext, "land": land, "water": water,
               "skyline": skyline(site) if site == "chelsea" else [],
               "sources": ["US Census Bureau cartographic boundary files 2023 (1:500k)",
                           "US Census Bureau TIGER/Line 2023 area water", "© OpenStreetMap contributors (skyline)"]}
        path = OUT / f"{site}_geo.json"
        path.write_text(json.dumps(doc, separators=(",", ":")))
        print(f"{site}: {len(land)} land rings, {len(water)} water, {len(doc['skyline'])} towers, {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
