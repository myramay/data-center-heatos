"""The team's trained models (heat_models.py), plugged into the HeatOS contracts.

- TeamWeatherProvider: real NOAA hourly temperature (Central Park / Ithaca airport,
  2015-2024). Dates outside that span reuse the real year with the same position
  in the 10-year cycle, so a 2025 run plays 2015's weather, 2026 plays 2016's...
  Stress scenarios (polar vortex, heat wave, ...) are applied on top exactly as
  in the mock.
- TeamDemandProvider: hourly SHAPES from the gradient-boosted model trained on
  NREL ComStock/ResStock (outputs/models/site*_demand_shapes.npz), split into
  space heat and hot water, scaled to each building's annual heat. Space heat
  also moves with the actual hour's weather vs the model's typical year, using
  the slope the model itself implies, so a polar vortex still raises demand.
  Bands come from the model's own P10/P90 spread (site*_demand_hourly_typical_year.csv).
- TeamSupplyProvider: hourly recoverable-heat shape from the team's supply
  simulation (site1: 111 8th Ave calibrated on its LL84 electricity; site2: the
  proposed Lake Hawkeye phase 1). By default the SHAPE is used and scaled to the
  site config's capacity (HEATOS_SUPPLY_SCALE=config); HEATOS_SUPPLY_SCALE=team
  uses the team's magnitude (10.6 MW Chelsea / ~90 MW Lansing) instead.

ML_TEAM_INTEGRATION: selected in engine/providers.py (HEATOS_ML=team).
Regenerate the inputs with `.team-venv/bin/python combine_site1.py && .team-venv/bin/python heat_models.py`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from engine import mocks
from engine.config import load_site
from engine.contracts import Building, ModelCard, SiteId

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "outputs" / "models"
WEATHER_DIR = ROOT / "heat-reuse-data" / "data" / "weather"
CACHE = ROOT / "cache"
STATION = {"chelsea": "nyc_central_park", "lansing": "ithaca_airport"}
KEY = {"chelsea": "site1", "lansing": "site2"}
YEARS = list(range(2015, 2025))
# HeatOS use type -> NREL building type the team model was trained on
NREL_TYPE = {
    "chelsea": {"residential": "multi-family_with_5plus_units", "public_housing": "multi-family_with_5plus_units",
                "office": "largeoffice", "retail": "retailstandalone", "food": "fullservicerestaurant",
                "clinic": "outpatient", "school": "secondaryschool", "home": "single-family_attached",
                "greenhouse": "warehouse", "aquaculture": "warehouse"},
    "lansing": {"residential": "multi-family_with_2_-_4_units", "public_housing": "multi-family_with_2_-_4_units",
                "office": "smalloffice", "retail": "retailstandalone", "food": "fullservicerestaurant",
                "clinic": "outpatient", "school": "primaryschool", "home": "single-family_detached",
                "greenhouse": "warehouse", "aquaculture": "warehouse"},
}
# Lansing supply column used for the shape: proposed phase 1, AI inference (steadiest of the three compute types)
SITE2_SUPPLY = "phase1_ai_inference__source_heat_mw"


def available() -> bool:
    need = [MODELS / f"{k}_{f}" for k in ("site1", "site2")
            for f in ("demand_shapes.npz", "demand_hourly_typical_year.csv", "supply_hourly_typical_year.csv")]
    return all(p.exists() for p in need) and all((WEATHER_DIR / s / "2024.csv").exists() for s in STATION.values())


# ===================================================================== weather

@lru_cache(maxsize=2)
def _noaa(station: str) -> pd.Series:
    """Hourly dry-bulb (C), local standard time, 2015-2024 (same cleaning as heat_models.load_weather)."""
    cached = CACHE / f"noaa_{station}.npy"
    full = pd.date_range("2015-01-01", "2024-12-31 23:00", freq="h")
    if cached.exists():
        return pd.Series(np.load(cached), index=full)
    out = []
    for y in YEARS:
        w = pd.read_csv(WEATHER_DIR / station / f"{y}.csv", usecols=["DATE", "TMP"], low_memory=False)
        val = pd.to_numeric(w["TMP"].str.split(",").str[0], errors="coerce") / 10
        t = pd.Series(val.values, index=pd.to_datetime(w["DATE"]) - pd.Timedelta(hours=5))
        out.append(t[(t > -45) & (t < 50)].resample("h").mean())
    s = pd.concat(out)
    s = s[~s.index.duplicated()].reindex(full).interpolate(limit=12).ffill().bfill()
    CACHE.mkdir(exist_ok=True)
    np.save(cached, s.to_numpy(dtype=np.float32))
    return s


def _real_year_hours(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Map local times to the matching hour of a real 2015-2024 year (standard time, no leap day)."""
    std = idx.tz_localize(None) if idx.tz is None else (idx.tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=5))
    year = 2015 + (std.year - 2015) % 10
    doy = np.minimum(std.dayofyear.to_numpy() - 1, 364)          # Dec 31 of a leap year reuses Dec 30
    base = pd.to_datetime(pd.DataFrame({"year": year, "month": 1, "day": 1}))
    return pd.DatetimeIndex(base + pd.to_timedelta(doy, "D") + pd.to_timedelta(std.hour.to_numpy(), "h"))


class TeamWeatherProvider(mocks.MockWeatherProvider):
    """Real NOAA hourly temperatures; stress scenarios layered on top as in the mock."""

    def base_t(self, site: SiteId, idx: pd.DatetimeIndex) -> np.ndarray:
        s = _noaa(STATION[site])
        return s.reindex(_real_year_hours(idx)).to_numpy(dtype=float)

    def model_card(self) -> ModelCard:
        return ModelCard(
            name="NOAA hourly weather", version="ISD 2015-2024",
            method="measured hourly dry-bulb; years beyond 2024 replay the 2015-2024 cycle",
            datasets=["NOAA ISD global-hourly 72505394728 (Central Park)", "NOAA ISD 72515594761 (Ithaca Tompkins Regional)"],
            assumptions=["stress scenarios overlay the real series (polar vortex low, heat wave high, as in the mock)"],
            is_mock=False)


# ===================================================================== demand

@lru_cache(maxsize=2)
def _shapes(site: SiteId) -> dict[str, np.ndarray]:
    z = np.load(MODELS / f"{KEY[site]}_demand_shapes.npz")
    return {k: np.asarray(z[k], dtype=float) for k in z.files}


@lru_cache(maxsize=2)
def _typical(site: SiteId) -> pd.DataFrame:
    return pd.read_csv(MODELS / f"{KEY[site]}_demand_hourly_typical_year.csv")


@lru_cache(maxsize=2)
def _bands(site: SiteId) -> tuple[np.ndarray, np.ndarray]:
    """Hour-of-year P10/P50 and P90/P50 ratios from the model's own aggregate ranges (outermost ring)."""
    d = _typical(site)
    cols = sorted({c.rsplit("_", 1)[0] for c in d.columns if c.startswith("demand_mw_le")},
                  key=lambda c: int(c.split("le")[1].rstrip("m")))
    ring = cols[-1]
    p50 = np.maximum(d[f"{ring}_P50"].to_numpy(), 1e-6)
    return (np.clip(d[f"{ring}_P10"].to_numpy() / p50, 0.3, 1.0), np.clip(d[f"{ring}_P90"].to_numpy() / p50, 1.0, 2.5))


def _hoy(idx: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """(weather-year row 0..9, hour of year 0..8759) of the real year each local hour replays."""
    r = _real_year_hours(idx)
    return r.year.to_numpy() - 2015, ((r.dayofyear.to_numpy() - 1) * 24 + r.hour.to_numpy()) % 8760


class TeamDemandProvider(mocks.MockDemandProvider):
    """Hourly demand from the team's NREL-trained model, scaled to each building's annual heat.

    The model gives one normalised shape per building type and real weather year
    (2015-2024); a run uses the shape of the year its weather replays. During a
    stress scenario the space-heat part moves with (scenario - real) degree-hours,
    at the slope the trained shape itself implies.
    """

    BALANCE_C = 18.0

    def __init__(self, buildings, weather):
        super().__init__(buildings, weather)
        self._slope: dict[tuple[SiteId, str], float] = {}

    def _type(self, b: Building) -> str | None:
        want = NREL_TYPE[b.site].get(b.use_type)
        return want if want and f"{want}|space" in _shapes(b.site) else None

    def _space_slope(self, site: SiteId, btype: str) -> float:
        key = (site, btype)
        if key not in self._slope:
            dd = np.maximum(0.0, self.BALANCE_C - _noaa(STATION[site]).to_numpy())
            dd = dd[: 10 * 8760] if dd.size >= 87600 else np.resize(dd, 87600)
            sp = _shapes(site)[f"{btype}|space"].ravel()
            self._slope[key] = float(max(np.dot(dd, sp) / max(np.dot(dd, dd), 1e-9), 0.0))
        return self._slope[key]

    def _drivers(self, b: Building, idx: pd.DatetimeIndex, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        bt = self._type(b)
        if bt is None or b.use_type in ("greenhouse", "aquaculture"):
            return super()._drivers(b, idx, t)                  # no NREL type for this use: keep the physics mock
        yr, h = _hoy(idx)
        sh = _shapes(b.site)
        base = self.weather.base_t(b.site, idx) if hasattr(self.weather, "base_t") else t
        adj = self._space_slope(b.site, bt) * (np.maximum(0.0, self.BALANCE_C - t) - np.maximum(0.0, self.BALANCE_C - base))
        return np.maximum(sh[f"{bt}|space"][yr, h] + adj, 0.0), sh[f"{bt}|dhw"][yr, h]

    def get_demand_forecast(self, site, building_ids, start, hours, weather_scenario="typical"):
        out = super().get_demand_forecast(site, building_ids, start, hours, weather_scenario)
        lo, hi = _bands(site)
        _, h = _hoy(mocks.hour_index(site, start, hours))
        for fc in out:
            p50 = np.asarray(fc.p50)
            fc.p05 = np.round(p50 * lo[h], 2).tolist()
            fc.p95 = np.round(p50 * hi[h], 2).tolist()
        return out

    def model_card(self) -> ModelCard:
        return ModelCard(
            name="Team demand model (heat_models.py)", version="HistGBR quantile",
            method="gradient-boosted quantile regression on NREL ComStock/ResStock hourly heat (held-out hourly R2 0.94); "
                   "per-type shapes for each real weather year x each building's annual heat",
            datasets=["NREL ComStock AMY2018 r3 (NY County, Tompkins)", "NREL ResStock AMY2018 r1 (NY)",
                      "NOAA ISD 2015-2024", "NYC LL84 (annual heat, site 1)", "Tompkins parcels (site 2)"],
            assumptions=["use type -> NREL building type mapping in ml/team_ml.py",
                         "greenhouse / aquaculture keep the degree-day physics profile (no NREL type)",
                         "stress scenarios shift space heat by the shape's own degree-hour slope",
                         "p05/p95 = model's hourly P10/P90 spread for the whole service area"],
            is_mock=False)


# ===================================================================== supply

@lru_cache(maxsize=2)
def _supply(site: SiteId) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    d = pd.read_csv(MODELS / f"{KEY[site]}_supply_hourly_typical_year.csv")
    col = "source_heat_mw" if site == "chelsea" else SITE2_SUPPLY
    return tuple(d[f"{col}_p{p}"].to_numpy(dtype=float) * 1000 for p in (10, 50, 90))  # kW


def team_supply_mw(site: SiteId) -> float:
    return float(_supply(site)[1].mean() / 1000)


class TeamSupplyProvider(mocks.MockSupplyProvider):
    """Recoverable heat from the team's supply simulation."""

    def get_supply_forecast(self, site, start, hours, scenario="base"):
        dc = load_site(site).data_center
        idx = mocks.hour_index(site, start, hours)
        _, h = _hoy(idx)
        p10, p50, p90 = (a[h] for a in _supply(site))
        cap = dc.capacity_mw_th.value * 1000 * dc.capture_fraction.value
        if os.environ.get("HEATOS_SUPPLY_SCALE", "config") != "team":
            k = cap / max(_supply(site)[1].max(), 1e-6)            # team shape, config magnitude
            p10, p50, p90 = p10 * k, p50 * k, p90 * k
        else:
            cap = max(cap, _supply(site)[2].max())
        lf = np.clip(p50 / max(cap, 1e-6), 0, 1)
        tmin, tmax = dc.supply_temp_c_min.value, dc.supply_temp_c_max.value
        temp = tmin + (tmax - tmin) * np.clip((lf - 0.75) / 0.2, 0, 1)
        if scenario == "tenant_leaves":
            p10, p50, p90 = p10 * 0.7, p50 * 0.7, p90 * 0.7
        elif scenario == "server_outage":
            p10[:6] = p50[:6] = p90[:6] = 0.0
            temp[:6] = tmin
        elif scenario == "flex_off":
            f = 1 - dc.flexible_compute_share.value
            p10, p50, p90 = p10 * f, p50 * f, p90 * f
        p05 = np.minimum(p10, p50)
        p95 = np.maximum(p90, p50)
        from engine.contracts import SupplyForecast
        return SupplyForecast(hours=list(idx.to_pydatetime()), p05=np.round(p05, 2).tolist(), p50=np.round(p50, 2).tolist(),
                              p95=np.round(p95, 2).tolist(), supply_temp_c=np.round(temp, 2).tolist())

    def model_card(self) -> ModelCard:
        scale = os.environ.get("HEATOS_SUPPLY_SCALE", "config")
        return ModelCard(
            name="Team supply model (heat_models.py)", version="calibrated sim + Monte Carlo",
            method="site 1: LL84 monthly electricity regression (IT vs cooling) + Google-trace compute profile; "
                   "site 2: proposed phase 1 AI-inference simulation",
            datasets=["NYC LL84 monthly (111 8th Ave)", "Google powerdata_2019 traces", "NOAA ISD 2015-2024"],
            assumptions=[f"scale = {scale}: " + ("team shape scaled to the site config capacity" if scale != "team"
                                                  else "team magnitude used as-is"),
                         "p05/p95 = team P10/P90 across simulated runs"],
            is_mock=False)
