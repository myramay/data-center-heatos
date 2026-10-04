"""Rebuild the New York State parts of heat-reuse-data/data/ from public sources.

Produces (relative to out_dir):
  equity/nys_dac_2023.csv                        data.ny.gov 2e6c-s6fp (Final DAC 2023, WKT the_geom)
  prices/nys_energy_prices_usd_per_mmbtu.csv     EIA SEDS bulk file, New York prices ($/MMBtu)
  prices/nys_heating_oil_prices_weekly.csv       data.ny.gov rc94-5y2u (NYSERDA weekly heating oil)
  prices/nys_propane_prices_weekly.csv           data.ny.gov keug-6vc5 (NYSERDA weekly propane)
  grid/nyiso_fuel_mix/2024MM_rtfuelmix.zip       NYISO MIS real-time fuel mix, monthly zips for 2024

Run:  .venv/bin/python -m scripts.ml_data.nystate
"""
from __future__ import annotations

import csv
import io
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

UA = {"User-Agent": "heatos-data-rebuild/1.0 (hackathon research; python-httpx)"}
TIMEOUT = httpx.Timeout(60.0, read=300.0)

DAC_ID = "2e6c-s6fp"
OIL_ID = "rc94-5y2u"
PROPANE_ID = "keug-6vc5"
SOCRATA_CSV = "https://data.ny.gov/api/views/{id}/rows.csv?accessType=DOWNLOAD"
SEDS_URL = "https://www.eia.gov/state/seds/CDF/Complete_SEDS.csv"
NYISO_URL = "http://mis.nyiso.com/public/csv/rtfuelmix/{y}{m:02d}01rtfuelmix_csv.zip"

# SEDS price MSNs ($/MMBtu, nominal) -> (Sector, column)
SEDS_MSN = {
    "NGCCD": ("Commercial", "Natural Gas"), "NGRCD": ("Residential", "Natural Gas"),
    "NGICD": ("Industrial", "Natural Gas"),
    "DFCCD": ("Commercial", "Distillate"), "DFRCD": ("Residential", "Distillate"),
    "DFICD": ("Industrial", "Distillate"),
    "RFCCD": ("Commercial", "Residual"), "RFICD": ("Industrial", "Residual"),
    "ESCCD": ("Commercial", "Electricity"), "ESRCD": ("Residential", "Electricity"),
    "ESICD": ("Industrial", "Electricity"),
}
FIRST_YEAR = 2000


def _get(client: httpx.Client, url: str, tries: int = 4) -> bytes:
    for i in range(tries):
        try:
            r = client.get(url)
            r.raise_for_status()
            if not r.content:
                raise httpx.HTTPError(f"empty body from {url}")
            return r.content
        except httpx.HTTPError as e:
            if i == tries - 1:
                raise
            print(f"  retry {i + 1} {url}: {e}", file=sys.stderr)
            time.sleep(2 ** i * 2)
    raise RuntimeError("unreachable")


def _socrata(client: httpx.Client, ds: str, dest: Path, notes: list[str], sort_date: bool = False) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        notes.append(f"skip existing {dest.name}")
        return
    url = SOCRATA_CSV.format(id=ds)
    raw = _get(client, url)
    if sort_date:
        # team code takes .tail(8) as "latest 8 weeks" -> guarantee ascending date order
        df = pd.read_csv(io.BytesIO(raw))
        df["_d"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.sort_values("_d").drop(columns="_d")
        df.to_csv(dest, index=False)
    elif ds == DAC_ID:
        # The public export writes 0 for the out-of-region percentile (NYC rank on Rest-of-State
        # tracts and vice versa). realdata.build_equity_county() does NYC.fillna(ROS), which only
        # works if those are blank -> blank them so ROS tracts (e.g. Tompkins) get their real rank.
        df = pd.read_csv(io.BytesIO(raw), dtype={"GEOID": str}, low_memory=False)
        df.loc[df["NYC_Region"] != "NYC", "Percentile_Rank_Combined_NYC"] = pd.NA
        df.loc[df["NYC_Region"] == "NYC", "Percentile_Rank_Combined_ROS"] = pd.NA
        df.to_csv(dest, index=False)
    else:
        dest.write_bytes(raw)
    notes.append(f"{dest.name}: {url}")


def _seds_prices(client: httpx.Client, dest: Path, notes: list[str]) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        notes.append(f"skip existing {dest.name}")
        return
    rows = []
    for i in range(4):
        try:
            rows = []
            with client.stream("GET", SEDS_URL) as r:
                r.raise_for_status()
                lines = r.iter_lines()
                next(lines)  # header
                status = None
                for line in lines:
                    if '"NY"' not in line:
                        continue
                    rec = next(csv.reader([line]))
                    st, msn, state, year, val = rec
                    if state == "NY" and msn in SEDS_MSN:
                        status = st
                        rows.append((msn, int(year), val))
            break
        except httpx.HTTPError as e:
            if i == 3:
                raise
            print(f"  retry SEDS: {e}", file=sys.stderr)
            time.sleep(2 ** i * 2)
    recs = {}
    for msn, year, val in rows:
        if year < FIRST_YEAR:
            continue
        sector, col = SEDS_MSN[msn]
        v = pd.to_numeric(val, errors="coerce")
        recs.setdefault((year, sector), {})[col] = None if pd.isna(v) or v == 0 else float(v)
    df = pd.DataFrame([{"Year": y, "Sector": s, **c} for (y, s), c in recs.items()])
    df = df[["Year", "Sector", "Natural Gas", "Distillate", "Residual", "Electricity"]]
    df = df.sort_values(["Year", "Sector"]).reset_index(drop=True)
    df.to_csv(dest, index=False)
    notes.append(f"{dest.name}: EIA SEDS {SEDS_URL} (release {status}), NY, MSNs {','.join(SEDS_MSN)}, "
                 f"years {df.Year.min()}-{df.Year.max()}, nominal $/MMBtu")


def _nyiso(client: httpx.Client, d: Path, notes: list[str], year: int = 2024) -> None:
    got = 0
    for m in range(1, 13):
        # site1_analysis.py globs '2024*_rtfuelmix.zip' -> NYISO's own name (20240101rtfuelmix_csv.zip) wouldn't match
        dest = d / f"{year}{m:02d}_rtfuelmix.zip"
        if dest.exists() and dest.stat().st_size > 0:
            continue
        dest.write_bytes(_get(client, NYISO_URL.format(y=year, m=m)))
        got += 1
    notes.append(f"nyiso_fuel_mix: {got} downloaded from {NYISO_URL.replace('{y}{m:02d}', str(year) + 'MM')}"
                 f" saved as {year}MM_rtfuelmix.zip (renamed to match the team glob)")


def fetch(out_dir: Path) -> list[str]:
    out_dir = Path(out_dir)
    for sub in ("equity", "prices", "grid/nyiso_fuel_mix"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    with httpx.Client(headers=UA, timeout=TIMEOUT, follow_redirects=True) as c:
        steps = [
            ("dac", lambda: _socrata(c, DAC_ID, out_dir / "equity" / "nys_dac_2023.csv", notes)),
            ("seds", lambda: _seds_prices(c, out_dir / "prices" / "nys_energy_prices_usd_per_mmbtu.csv", notes)),
            ("oil", lambda: _socrata(c, OIL_ID, out_dir / "prices" / "nys_heating_oil_prices_weekly.csv", notes, True)),
            ("propane", lambda: _socrata(c, PROPANE_ID, out_dir / "prices" / "nys_propane_prices_weekly.csv", notes, True)),
            ("nyiso", lambda: _nyiso(c, out_dir / "grid" / "nyiso_fuel_mix", notes)),
        ]
        for name, fn in steps:
            try:
                fn()
            except Exception as e:  # keep going, report
                notes.append(f"FAILED {name}: {e!r}")
    return notes


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out = root / "heat-reuse-data" / "data"
    for n in fetch(out):
        print(n)


if __name__ == "__main__":
    main()
