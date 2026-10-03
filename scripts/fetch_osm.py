"""Fetch real building footprints around each site from OpenStreetMap (Overpass API).

    python -m scripts.fetch_osm            # writes web/public/geo/<site>_osm.json

Output: footprints in local metres (east, north) from the site origin in the
site YAML, with a height from OSM `height` / `building:levels` (or a default).
Data (c) OpenStreetMap contributors, ODbL. Used for the 3D context only; the
analysis still runs on the building provider.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import httpx

from engine.config import load_site

OUT = Path(__file__).resolve().parents[1] / "web" / "public" / "geo"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
RADIUS_M = {"chelsea": 1300, "lansing": 6000}
DEFAULT_H = {"chelsea": 14.0, "lansing": 6.0}


def _height(tags: dict, default: float) -> float:
    for key in ("height", "building:height"):
        v = tags.get(key)
        if v:
            try:
                return max(3.0, float(str(v).split()[0].replace("m", "")))
            except ValueError:
                pass
    lv = tags.get("building:levels")
    if lv:
        try:
            return max(3.0, float(str(lv).split(";")[0]) * 3.3 + 1.0)
        except ValueError:
            pass
    return default


def fetch(site: str) -> dict:
    cfg = load_site(site)
    lat0, lon0 = cfg.site.lat, cfg.site.lon
    q = f'[out:json][timeout:120];(way["building"](around:{RADIUS_M[site]},{lat0},{lon0}););out tags geom;'
    data = None
    for url in OVERPASS:
        try:
            r = httpx.post(url, data={"data": q}, timeout=180, headers={"User-Agent": "HeatOS hackathon (local)"})
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:  # try the mirror
            print(f"  {url} failed: {e}", file=sys.stderr)
            time.sleep(2)
    if data is None:
        raise RuntimeError("Overpass unavailable")
    k_lat = 111_320.0
    k_lon = 111_320.0 * math.cos(math.radians(lat0))
    out = []
    for el in data.get("elements", []):
        geom = el.get("geometry") or []
        if len(geom) < 4:
            continue
        pts = [[round((g["lon"] - lon0) * k_lon, 1), round((g["lat"] - lat0) * k_lat, 1)] for g in geom]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        tags = el.get("tags", {})
        out.append({"id": el["id"], "h": round(_height(tags, DEFAULT_H[site]), 1), "p": pts,
                    **({"n": tags["name"]} if "name" in tags else {})})
    return {"site": site, "origin": [lat0, lon0], "radius_m": RADIUS_M[site],
            "attribution": "© OpenStreetMap contributors (ODbL)", "buildings": out}


def main(sites: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for site in sites:
        d = fetch(site)
        path = OUT / f"{site}_osm.json"
        path.write_text(json.dumps(d, separators=(",", ":")))
        print(f"{site}: {len(d['buildings'])} buildings, {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1:] or ["chelsea", "lansing"])
