"""Rebuild heat-reuse-data/data/site1_nyc/ from public NYC Open Data (Socrata).

    .venv/bin/python -m scripts.ml_data.nyc [--force] [--out DIR]

Files written (column names match what heat_models.py, data_load.py, combine_site1.py,
site1_analysis.py and realdata.py read):

  ll84_annual_2022_present.csv  LL84 2022-present (5zyy-y8am), Manhattan rows, display headers
  ll84_monthly.csv              LL84 monthly (fvp3-gcb2), rows for Property Ids in the annual file
  ll84_111_8th_ave.csv          annual rows for Property ID 7536925 (111 8th Avenue, BBL 1007390001)
  pluto_manhattan.csv           PLUTO (64uk-42ks), borough = MN, lowercase field names, no geometry
  school_locations.csv          2019-2020 School Locations (wg9x-4ke6), original mixed-case headers
  nycha_development_data_book.csv  NYCHA Development Data Book (evjd-dqpz)

The SODA resource API returns machine field names, so each table is fetched as CSV page by page
and renamed to the dataset's display column names from /api/views/<id>.json. That gives the same
headers as the portal's "Export CSV" (rows.csv) while letting us filter server side.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

DOMAIN = "https://data.cityofnewyork.us"
UA = "heatos-data-repro/1.0 (stern hackathon; public NYC Open Data)"
PAGE = 50_000

LL84_ANNUAL = "5zyy-y8am"   # NYC Building Energy and Water Data Disclosure for LL84 (2022-Present)
LL84_MONTHLY = "fvp3-gcb2"  # Local Law 84 Monthly Data (Calendar Year)
PLUTO = "64uk-42ks"         # Primary Land Use Tax Lot Output (PLUTO)
SCHOOLS = "wg9x-4ke6"       # 2019 - 2020 School Locations
NYCHA = "evjd-dqpz"         # NYCHA Development Data Book

SITE_ID = 7536925           # LL84 Property ID of 111 8th Avenue

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "heat-reuse-data" / "data" / "site1_nyc"


# ------------------------------------------------------------------ HTTP helpers
def _client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, timeout=httpx.Timeout(300, connect=30),
                        follow_redirects=True)


def _get(client: httpx.Client, url: str, params: dict | None = None, tries: int = 5) -> httpx.Response:
    for i in range(tries):
        try:
            r = client.get(url, params=params)
            if r.status_code == 200:
                return r
            if r.status_code < 500 and r.status_code != 429:
                raise RuntimeError(f"{url} -> HTTP {r.status_code}: {r.text[:300]}")
            err = f"HTTP {r.status_code}"
        except (httpx.TransportError, httpx.TimeoutException) as e:
            err = repr(e)
        wait = 5 * 2 ** i
        print(f"  retry {i + 1}/{tries} in {wait}s ({err})", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def _columns(client: httpx.Client, ds: str) -> list[tuple[str, str]]:
    """[(fieldName, display name)] for the dataset's public columns, in portal order."""
    meta = _get(client, f"{DOMAIN}/api/views/{ds}.json").json()
    return [(c["fieldName"], c["name"]) for c in meta["columns"]
            if not c["fieldName"].startswith(":")]


def _soda_pages(client: httpx.Client, ds: str, fields: list[str], where: str | None = None):
    """Yield DataFrames (all strings) of a SODA query, PAGE rows at a time, stable order."""
    offset = 0
    while True:
        params = {"$select": ",".join(fields), "$order": ":id", "$limit": PAGE, "$offset": offset}
        if where:
            params["$where"] = where
        r = _get(client, f"{DOMAIN}/resource/{ds}.csv", params)
        df = pd.read_csv(io.StringIO(r.text), dtype=str, keep_default_na=False)
        if df.empty:
            return
        yield df
        offset += len(df)
        print(f"  {ds}: {offset:,} rows", file=sys.stderr)
        if len(df) < PAGE:
            return


def _fetch_table(client, ds, where=None, drop=(), display=True) -> pd.DataFrame:
    cols = [(f, n) for f, n in _columns(client, ds) if f not in drop]
    fields = [f for f, _ in cols]
    df = pd.concat(list(_soda_pages(client, ds, fields, where)), ignore_index=True)
    df = df.reindex(columns=fields)
    if display:
        df.columns = [n for _, n in cols]
    return df


def _write(df: pd.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, quoting=csv.QUOTE_MINIMAL)
    tmp.replace(path)


def _mb(path: Path) -> str:
    return f"{path.stat().st_size / 1e6:.1f} MB"


# ------------------------------------------------------------------ builders
def _annual(client, path: Path) -> str:
    # Manhattan, plus records with no Borough whose (first) BBL is in Manhattan (borough code 1)
    where = "borough = 'MANHATTAN' OR (borough IS NULL AND starts_with(nyc_borough_block_and_lot, '1'))"
    df = _fetch_table(client, LL84_ANNUAL, where=where)
    df = df.sort_values(["Calendar Year", "Property ID"], kind="stable")
    _write(df, path)
    return (f"ll84_annual_2022_present.csv: {len(df):,} rows x {df.shape[1]} cols ({_mb(path)}) from "
            f"{DOMAIN}/resource/{LL84_ANNUAL} (LL84 2022-Present) filtered to Borough=MANHATTAN "
            f"(+ blank-borough rows with a 1xxxxxxxxx BBL); headers renamed to portal display names. "
            f"Date columns come in SODA ISO format, not the rows.csv 'MM/DD/YYYY' format.")


def _monthly(client, path: Path, annual_path: Path) -> str:
    ids = set(pd.read_csv(annual_path, usecols=["Property ID"], dtype=str)["Property ID"].str.strip())
    cols = _columns(client, LL84_MONTHLY)
    fields = [f for f, _ in cols]
    parts, total = [], 0
    for page in _soda_pages(client, LL84_MONTHLY, fields):
        total += len(page)
        parts.append(page[page["property_id"].str.strip().isin(ids)])
    df = pd.concat(parts, ignore_index=True).reindex(columns=fields)
    df.columns = [n for _, n in cols]
    _write(df, path)
    return (f"ll84_monthly.csv: {len(df):,} of {total:,} rows ({_mb(path)}) from "
            f"{DOMAIN}/resource/{LL84_MONTHLY} (LL84 Monthly Data, calendar years 2019-present), "
            f"kept only Property Ids present in the Manhattan annual file; display headers kept "
            f"verbatim (incl. double spaces, and the trailing space in 'Fuel Oil #1 Use - Monthly (kBtu) ').")


def _site(path: Path, annual_path: Path) -> str:
    a = pd.read_csv(annual_path, dtype=str, keep_default_na=False)
    d = a[a["Property ID"].str.strip() == str(SITE_ID)].copy()
    addr = set(d["Address 1"])
    assert d["Address 1"].str.contains(r"111\s+(?:8th|Eighth)", case=False, regex=True).all(), addr
    d = d.drop_duplicates().sort_values("Calendar Year")   # site1_analysis uses .iloc[-1] = latest
    _write(d, path)
    return (f"ll84_111_8th_ave.csv: {len(d)} rows (years {', '.join(d['Calendar Year'])}), all "
            f"{d.shape[1]} LL84 annual columns, = annual rows with Property ID {SITE_ID} "
            f"(Address 1 {sorted(addr)}, BBL {sorted(set(d['NYC Borough, Block and Lot (BBL)']))}). "
            f"Original team file may also have held pre-2022 years from older LL84 releases; not added.")


def _pluto(client, path: Path) -> str:
    df = _fetch_table(client, PLUTO, where="borough = 'MN'", drop={"geom"}, display=False)
    df["bbl"] = pd.to_numeric(df["bbl"], errors="coerce").astype("Int64")
    _write(df, path)
    return (f"pluto_manhattan.csv: {len(df):,} rows x {df.shape[1]} cols ({_mb(path)}) from "
            f"{DOMAIN}/resource/{PLUTO}?borough=MN (current PLUTO release on the portal, may be newer "
            f"than the team's copy); lowercase field names, 'geom' dropped, bbl written as integer.")


def _schools(client, path: Path) -> str:
    df = _fetch_table(client, SCHOOLS)
    _write(df, path)
    return (f"school_locations.csv: {len(df):,} rows ({_mb(path)}) from {DOMAIN}/resource/{SCHOOLS} "
            f"(2019-2020 School Locations: the latest NYC DOE release that has 'location_name', "
            f"'Borough_block_lot', 'LATITUDE', 'LONGITUDE' headers). Team may have used a different year.")


def _nycha(client, path: Path) -> str:
    df = _fetch_table(client, NYCHA)
    _write(df, path)
    return (f"nycha_development_data_book.csv: {len(df):,} rows ({_mb(path)}) from "
            f"{DOMAIN}/resource/{NYCHA} (NYCHA Development Data Book), display headers "
            f"(incl. the portal's 'COMMUNITY DISTIRCT' typo the code relies on).")


# ------------------------------------------------------------------ entry point
def fetch(out_dir: Path, force: bool = False) -> list[str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = {name: out_dir / name for name in [
        "ll84_annual_2022_present.csv", "ll84_monthly.csv", "ll84_111_8th_ave.csv",
        "pluto_manhattan.csv", "school_locations.csv", "nycha_development_data_book.csv"]}
    notes: list[str] = []

    def step(name, fn, *args):
        if p[name].exists() and not force:
            notes.append(f"{name}: exists ({_mb(p[name])}), skipped (use --force to refetch)")
            return
        print(f"fetching {name} ...", file=sys.stderr)
        notes.append(fn(*args))

    with _client() as c:
        step("ll84_annual_2022_present.csv", _annual, c, p["ll84_annual_2022_present.csv"])
        step("ll84_monthly.csv", _monthly, c, p["ll84_monthly.csv"], p["ll84_annual_2022_present.csv"])
        step("ll84_111_8th_ave.csv", _site, p["ll84_111_8th_ave.csv"], p["ll84_annual_2022_present.csv"])
        step("pluto_manhattan.csv", _pluto, c, p["pluto_manhattan.csv"])
        step("school_locations.csv", _schools, c, p["school_locations.csv"])
        step("nycha_development_data_book.csv", _nycha, c, p["nycha_development_data_book.csv"])
    return notes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="re-download files that already exist")
    a = ap.parse_args()
    for n in fetch(a.out, force=a.force):
        print("-", n)


if __name__ == "__main__":
    main()
