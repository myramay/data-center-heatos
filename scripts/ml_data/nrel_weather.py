"""Rebuild the ML inputs of heat-reuse-data/data/ from public sources.

    .venv/bin/python -m scripts.ml_data.nrel_weather

Writes (relative to heat-reuse-data/data/):

- nrel_load_profiles/comstock_amy2018_release_3/up0-{g3600610,g3601090}-*.csv
    NREL ComStock AMY2018 release 3 (2025), by-county aggregates, baseline (upgrade 0).
- nrel_load_profiles/resstock_amy2018_release_1/up00-ny-*.csv
    NREL ResStock AMY2018 release 1 (2025), by-state aggregates for NY, baseline (upgrade 0).
- weather/nyc_central_park/{2015..2024}.csv, weather/ithaca_airport/{2015..2024}.csv
    NOAA NCEI Global Hourly (ISD) access CSVs, Central Park 725053-94728 and
    Ithaca Tompkins Regional Airport 725155-94761.
- site2_lansing/tompkins_parcels.geojson
    NYS ITS "NYS Tax Parcels Public" layer, COUNTY_NAME = 'Tompkins', all attributes, WGS84.
- cluster-data/powerdata_2019/cell*_*.csv.gz
    Google cluster-data PowerData2019 traces (57 power domains). The machine-to-PDU
    mapping file is skipped on purpose: heat_models.ComputeLoadModel globs *.csv.gz and
    expects every file to be a power trace.

Existing files are skipped, so the script can be re-run after an interruption.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "heat-reuse-data" / "data"
UA = {"User-Agent": "HeatOS hackathon data rebuild (public data; httpx)"}

OEDI = "https://oedi-data-lake.s3.amazonaws.com/"
EULP = "nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/"
COMSTOCK = EULP + "2025/comstock_amy2018_release_3/timeseries_aggregates/by_county/upgrade=0/county={C}/"
RESSTOCK = EULP + "2025/resstock_amy2018_release_1/timeseries_aggregates/by_state/upgrade=0/state=NY/"
COUNTIES = ["G3600610", "G3601090"]          # Manhattan (New York County), Tompkins County

ISD = "https://www.ncei.noaa.gov/data/global-hourly/access/{year}/{station}.csv"
STATIONS = {"nyc_central_park": "72505394728", "ithaca_airport": "72515594761"}
YEARS = range(2015, 2025)                    # heat_models.WEATHER_YEARS (includes the 2018 training year)

PARCELS = "https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Tax_Parcels_Public/MapServer/1/query"
PARCEL_WHERE = "COUNTY_NAME='Tompkins'"

GCS_LIST = "https://storage.googleapis.com/storage/v1/b/powerdata_2019/o"
GCS_FILE = "https://storage.googleapis.com/powerdata_2019/{name}"


def _client() -> httpx.Client:
    return httpx.Client(headers=UA, timeout=httpx.Timeout(120, connect=30), follow_redirects=True)


def _retry(fn, what: str, tries: int = 5):
    for i in range(tries):
        try:
            return fn()
        except (httpx.HTTPError, ValueError) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 404:
                raise
            if i == tries - 1:
                raise
            wait = 2 ** i
            print(f"    retry {what} in {wait}s ({e.__class__.__name__}: {e})", flush=True)
            time.sleep(wait)


def _download(c: httpx.Client, url: str, dest: Path) -> int:
    """Stream url to dest (via .part). Returns bytes written, 0 if it already existed."""
    if dest.exists() and dest.stat().st_size > 0:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")

    def go():
        with c.stream("GET", url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
        tmp.replace(dest)
        return dest.stat().st_size

    return _retry(go, url)


def _s3_list(c: httpx.Client, prefix: str) -> list[tuple[str, int]]:
    out, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        t = _retry(lambda: c.get(OEDI, params=params).raise_for_status().text, prefix)
        out += [(k, int(s)) for k, s in re.findall(r"<Key>([^<]*)</Key>.*?<Size>(\d+)</Size>", t, re.S)]
        m = re.search(r"<NextContinuationToken>([^<]*)</NextContinuationToken>", t)
        if not m:
            return out
        token = m.group(1)


# ---------------------------------------------------------------- NREL
def fetch_nrel(c: httpx.Client, out_dir: Path, notes: list[str]) -> None:
    jobs = []
    for county in COUNTIES:
        for key, size in _s3_list(c, COMSTOCK.format(C=county)):
            if key.endswith(".csv"):
                jobs.append((key, size, out_dir / "nrel_load_profiles" / "comstock_amy2018_release_3"))
    for key, size in _s3_list(c, RESSTOCK):
        if key.endswith(".csv"):
            jobs.append((key, size, out_dir / "nrel_load_profiles" / "resstock_amy2018_release_1"))
    got = 0
    for key, size, folder in jobs:
        name = key.rsplit("/", 1)[-1]          # already up0-g3600610-<type>.csv / up00-ny-<type>.csv
        n = _download(c, OEDI + key, folder / name)
        got += n
        print(f"  {'got ' if n else 'have'} {folder.name}/{name} ({size / 1e6:.1f} MB)", flush=True)
    notes.append(f"NREL: {len(jobs)} files ({sum(s for _, s, _ in jobs) / 1e6:.0f} MB total, "
                 f"{got / 1e6:.0f} MB downloaded now) from {OEDI}{EULP}2025/...")


# ---------------------------------------------------------------- weather
def fetch_weather(c: httpx.Client, out_dir: Path, notes: list[str]) -> None:
    for folder, station in STATIONS.items():
        for y in YEARS:
            dest = out_dir / "weather" / folder / f"{y}.csv"
            try:
                n = _download(c, ISD.format(year=y, station=station), dest)
            except httpx.HTTPStatusError as e:
                notes.append(f"weather: {folder} {y} missing ({e.response.status_code}) at {e.request.url}")
                continue
            print(f"  {'got ' if n else 'have'} weather/{folder}/{y}.csv ({dest.stat().st_size / 1e6:.1f} MB)", flush=True)
    notes.append("weather: NOAA ISD global-hourly CSVs, stations "
                 + ", ".join(f"{k}={v}" for k, v in STATIONS.items()) + f", {YEARS.start}-{YEARS.stop - 1}")


# ---------------------------------------------------------------- parcels
def fetch_parcels(c: httpx.Client, out_dir: Path, notes: list[str]) -> None:
    dest = out_dir / "site2_lansing" / "tompkins_parcels.geojson"
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have {dest.relative_to(out_dir)}")
        return
    ids = _retry(lambda: c.get(PARCELS, params={"where": PARCEL_WHERE, "returnIdsOnly": "true", "f": "json"})
                 .raise_for_status().json()["objectIds"], "parcel ids")
    ids = sorted(ids)
    feats = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        data = {"objectIds": ",".join(map(str, chunk)), "outFields": "*", "outSR": "4326",
                "geometryPrecision": "7", "returnGeometry": "true", "f": "geojson"}
        page = _retry(lambda: c.post(PARCELS, data=data).raise_for_status().json(), f"parcels {i}")
        if "features" not in page:
            raise RuntimeError(f"parcel query failed: {str(page)[:300]}")
        feats += page["features"]
        print(f"  parcels {len(feats):,}/{len(ids):,}", end="\r", flush=True)
    print()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    tmp.replace(dest)
    notes.append(f"parcels: {len(feats):,} Tompkins County parcels (all attributes) from NYS ITS "
                 f"NYS_Tax_Parcels_Public layer 1 ({PARCELS}); current roll year, not necessarily the "
                 f"vintage the team downloaded")


# ---------------------------------------------------------------- Google power traces
def fetch_power(c: httpx.Client, out_dir: Path, notes: list[str]) -> None:
    items = _retry(lambda: c.get(GCS_LIST, params={"maxResults": 1000, "fields": "items(name,size)"})
                   .raise_for_status().json()["items"], "gcs list")
    names = [i["name"] for i in items if re.fullmatch(r"cell[a-z]_[a-z]+\d+\.csv\.gz", i["name"])]
    folder = out_dir / "cluster-data" / "powerdata_2019"
    for name in names:
        _download(c, GCS_FILE.format(name=name), folder / name)
    print(f"  power traces: {len(names)} files in {folder.relative_to(out_dir)}")
    notes.append(f"google power: {len(names)} PDU traces from gs://powerdata_2019 "
                 f"(machine_to_pdu_mapping.csv.gz skipped: not a trace, would break the *.csv.gz glob)")


def fetch(out_dir: Path) -> list[str]:
    out_dir = Path(out_dir)
    notes: list[str] = []
    with _client() as c:
        for label, fn in [("NREL load profiles", fetch_nrel), ("weather", fetch_weather),
                          ("Tompkins parcels", fetch_parcels), ("Google power traces", fetch_power)]:
            print(f"== {label}", flush=True)
            try:
                fn(c, out_dir, notes)
            except Exception as e:   # keep going; report what failed
                notes.append(f"{label}: FAILED ({e.__class__.__name__}: {e})")
                print(f"  FAILED: {e}", flush=True)
    return notes


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA
    for n in fetch(out):
        print("-", n)
