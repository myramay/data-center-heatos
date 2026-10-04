"""Mock implementations of the ML team's providers.

Everything here is synthetic but physically plausible and fully
deterministic: the same (site, building, timestamp, scenario) always gives
the same number, whatever window it is requested in.

ML_TEAM_INTEGRATION: each class below is replaced by a real module in ml/.
Swap them in engine/providers.py only.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from engine.config import load_site
from engine.contracts import (
    Building, ConfidenceResult, DemandForecast, FutureInputs, FutureOutcome, HeatingSystem,
    JevOpinion, ModelCard, Plan, SiteId, SimState, SupplyForecast, SupplyScenario,
    UncertaintyDriver, UseType, WeatherScenario, WeatherSeries,
)

if TYPE_CHECKING:
    from engine.providers import BuildingProvider, FutureSimulator, WeatherProvider

BASE_EPOCH_HOUR = 394_464          # 2015-01-01T00:00Z in hours since 1970
NOISE_SPAN_HOURS = 30 * 8766       # noise tables cover 2015-2044
BLOCK_HOURS = 8766                 # per-building noise is generated in year-sized blocks
REFERENCE_YEAR_START = datetime(2025, 1, 1)

# ML_TEAM_INTEGRATION: replace with the GEV 1-in-50-year low from the
# Monte Carlo engine (polar vortex, Chelsea) and lake-effect equivalent.
POLAR_VORTEX_LOW_C = -18.0
LAKE_EFFECT_LOW_C = -22.0
HEAT_WAVE_HIGH_C = 34.0


def _seed(*parts: object) -> int:
    return zlib.crc32("|".join(map(str, parts)).encode())


def hour_index(site: SiteId, start: datetime, hours: int) -> pd.DatetimeIndex:
    """Hourly, tz-aware index in the site's local timezone."""
    tz = ZoneInfo(load_site(site).site.timezone)
    ts = pd.Timestamp(start)
    ts = ts.tz_localize(tz) if ts.tzinfo is None else ts.tz_convert(tz)
    return pd.date_range(ts.floor("h"), periods=hours, freq="h")


def _epoch_hours(idx: pd.DatetimeIndex) -> np.ndarray:
    h = idx.as_unit("s").asi8 // 3600 - BASE_EPOCH_HOUR
    if h.min() < 0 or h.max() >= NOISE_SPAN_HOURS:
        raise ValueError("mock providers cover 2015-2044 only")
    return h


def _block_noise(key: str, epoch_h: np.ndarray, sigma: float) -> np.ndarray:
    """White noise that is a pure function of (key, absolute hour)."""
    blocks = epoch_h // BLOCK_HOURS
    out = np.empty(len(epoch_h))
    for blk in np.unique(blocks):
        m = blocks == blk
        out[m] = _noise_block(key, int(blk), sigma)[epoch_h[m] - blk * BLOCK_HOURS]
    return out


@lru_cache(maxsize=512)
def _noise_block(key: str, block: int, sigma: float) -> np.ndarray:
    return np.random.default_rng(_seed(key, block)).normal(0.0, sigma, BLOCK_HOURS)


def _ramp(n: int, length: int, edge: int = 6) -> np.ndarray:
    """0..1 weight that is 1 for the first `length` hours, with soft edges."""
    h = np.arange(n, dtype=float)
    return np.clip(h / edge, 0, 1) * np.clip((length - h) / edge, 0, 1)


# ===================================================================== weather

@dataclass(frozen=True)
class Climate:
    mean_c: float
    amp_c: float
    coldest_doy: float
    diurnal_c: float
    noise_c: float


CLIMATES = {
    "nyc": Climate(mean_c=13.0, amp_c=11.5, coldest_doy=22, diurnal_c=4.0, noise_c=3.0),
    "ithaca": Climate(mean_c=8.0, amp_c=13.0, coldest_doy=25, diurnal_c=5.0, noise_c=3.5),
}


@lru_cache(maxsize=4)
def _weather_noise(climate: str) -> np.ndarray:
    """AR(1) synoptic noise (multi-day warm and cold spells)."""
    c = CLIMATES[climate]
    phi = 0.985
    eps = np.random.default_rng(_seed("weather", climate)).normal(size=NOISE_SPAN_HOURS + 2000)
    ar = lfilter([c.noise_c * math.sqrt(1 - phi**2)], [1, -phi], eps)
    return ar[2000:]


class MockWeatherProvider:
    """Seasonal sinusoid + diurnal cycle + AR(1) noise for NYC or Ithaca.

    ML_TEAM_INTEGRATION: replace with real TMY / NOAA hourly data.
    """

    def get_weather(self, site: SiteId, start: datetime, hours: int,
                    scenario: WeatherScenario = "typical") -> WeatherSeries:
        idx = hour_index(site, start, hours)
        c = CLIMATES[load_site(site).site.climate]
        diurnal = c.diurnal_c * np.cos(2 * np.pi * (idx.hour.to_numpy() - 15) / 24)
        t = self.base_t(site, idx)

        if scenario == "cold_year":
            t = t - 2.0
        elif scenario == "warm_year":
            t = t + 1.5
        elif scenario in ("polar_vortex", "lake_effect"):
            low = POLAR_VORTEX_LOW_C if scenario == "polar_vortex" else LAKE_EFFECT_LOW_C
            w = _ramp(hours, 72)
            t = (1 - w) * t + w * (low + 0.4 * diurnal)
        elif scenario == "heat_wave":
            w = _ramp(hours, 120)
            t = (1 - w) * t + w * np.maximum(t, HEAT_WAVE_HIGH_C + 0.8 * diurnal)

        return WeatherSeries(hours=list(idx.to_pydatetime()), t_out_c=np.round(t, 2).tolist(),
                             scenario=scenario)

    def base_t(self, site: SiteId, idx: pd.DatetimeIndex) -> np.ndarray:
        """Outdoor temperature before any stress scenario is applied."""
        c = CLIMATES[load_site(site).site.climate]
        doy = idx.dayofyear.to_numpy() + idx.hour.to_numpy() / 24
        diurnal = c.diurnal_c * np.cos(2 * np.pi * (idx.hour.to_numpy() - 15) / 24)
        return (c.mean_c - c.amp_c * np.cos(2 * np.pi * (doy - c.coldest_doy) / 365.25)
                + diurnal + _weather_noise(load_site(site).site.climate)[_epoch_hours(idx)])

    def model_card(self) -> ModelCard:
        return ModelCard(
            name="Mock weather", version="0.1", method="seasonal sinusoid + diurnal + AR(1) noise",
            datasets=["synthetic (NYC / Ithaca climate normals, approximate)"],
            assumptions=[f"polar vortex low {POLAR_VORTEX_LOW_C} C (placeholder for GEV 1-in-50)",
                         f"lake-effect low {LAKE_EFFECT_LOW_C} C (placeholder)"],
            is_mock=True)


# ===================================================================== buildings

# Chelsea local frame: x = metres crosstown (east +), y = metres uptown (north +),
# aligned with the Manhattan grid. Avenues sit 280 m apart (8th Ave at x=+140,
# 9th at -140, 10th at -420, 7th at +420); streets 80 m apart (W16th at y=+40,
# W(n) at y = 40 + 80*(n-16)). The data center fills x in [-140, 140], y in [-40, 40].
DC_HALF = {"chelsea": (140.0, 40.0), "lansing": (60.0, 60.0)}

# id, name, use, x, y, height_m, footprint_m2, heating, year, boiler_age, equity, notes
_CHELSEA: list[tuple] = [
    ("CH-01", "Fulton Houses Tower 1", "public_housing", -220, 120, 70, 1100, "gas_boiler", 1965, 28, 0.95, ""),
    ("CH-02", "Fulton Houses Tower 2", "public_housing", -320, 170, 70, 1100, "gas_boiler", 1965, 28, 0.95, ""),
    ("CH-03", "Fulton Houses Tower 3", "public_housing", -250, 250, 60, 1000, "oil", 1965, 31, 0.96, ""),
    ("CH-04", "Chelsea-Elliott Houses North", "public_housing", -300, 880, 65, 1200, "gas_boiler", 1947, 24, 0.93, ""),
    ("CH-05", "Chelsea-Elliott Houses South", "public_housing", -240, 790, 45, 1300, "gas_boiler", 1947, 22, 0.92, ""),
    ("CH-06", "Market hall and offices, 9th Ave & W15th", "office", -260, 0, 40, 9000, "steam", 1898, None, 0.25, "food hall at street level, offices above"),
    ("CH-07", "Loft offices, 8th Ave & W17th", "office", 220, 160, 55, 2400, "steam", 1910, None, 0.10, ""),
    ("CH-08", "Office tower, 8th Ave & W15th", "office", 200, 0, 70, 2000, "steam", 1931, None, 0.10, ""),
    ("CH-09", "Office, 7th Ave & W15th", "office", 440, -20, 60, 2200, "steam", 1926, None, 0.10, ""),
    ("CH-10", "Media offices, 9th Ave & W18th", "office", -60, 260, 45, 2600, "gas_boiler", 1960, 18, 0.10, ""),
    ("CH-11", "Retail row, 8th Ave & W14th", "retail", 160, -140, 15, 1200, "gas_boiler", 1920, 15, 0.20, ""),
    ("CH-12", "Restaurant cluster, 9th Ave & W14th", "food", -120, -150, 12, 900, "gas_boiler", 1900, 12, 0.30, ""),
    ("CH-13", "Bakery and commissary kitchen, W13th", "food", -260, -210, 15, 1400, "gas_boiler", 1925, 21, 0.30, ""),
    ("CH-14", "Community health clinic, 9th Ave & W17th", "clinic", -90, 140, 25, 1500, "gas_boiler", 1955, 24, 0.75, "guarantee buyer"),
    ("CH-15", "Residential co-op, 8th Ave & W19th", "residential", 180, 320, 50, 1500, "steam", 1928, None, 0.40, ""),
    ("CH-16", "Rental apartments, 9th Ave & W20th", "residential", -80, 400, 40, 1300, "gas_boiler", 1962, 26, 0.45, ""),
    ("CH-17", "Senior housing residence, 10th Ave & W17th", "residential", -400, 140, 35, 1400, "oil", 1975, 31, 0.85, "senior housing; guarantee buyer"),
    ("CH-18", "Hotel, 8th Ave & W13th", "residential", 200, -220, 45, 1600, "steam", 1929, None, 0.20, "hotel"),
    ("CH-19", "Public high school, 10th Ave & W18th", "school", -480, 280, 25, 4000, "oil", 1958, 34, 0.70, ""),
    ("CH-20", "Elementary school, 9th Ave & W21st", "school", -180, 480, 20, 2500, "gas_boiler", 1930, 27, 0.75, ""),
    ("CH-21", "Gallery-district offices, 10th Ave & W26th", "office", -500, 820, 40, 3000, "gas_boiler", 1925, 19, 0.10, ""),
    ("CH-22", "Galleries and studios, 10th Ave & W24th", "retail", -520, 680, 18, 2200, "electric", 1940, None, 0.15, ""),
    ("CH-23", "Office, 9th Ave & W14th", "office", -180, -140, 50, 2000, "steam", 1937, None, 0.10, ""),
    ("CH-24", "Condominium tower, 10th Ave & W15th", "residential", -440, -10, 80, 1100, "gas_boiler", 2012, 10, 0.15, ""),
    ("CH-25", "Office, 10th Ave & W13th", "office", -520, -220, 60, 2600, "gas_boiler", 2015, 9, 0.10, ""),
    ("CH-26", "Apartments, 8th Ave & W22nd", "residential", 200, 560, 45, 1400, "steam", 1925, None, 0.40, ""),
    ("CH-27", "Apartments, 7th Ave & W23rd", "residential", 420, 640, 55, 1500, "steam", 1931, None, 0.35, ""),
    ("CH-28", "Office, 7th Ave & W17th", "office", 400, 140, 65, 2400, "steam", 1915, None, 0.10, ""),
    ("CH-29", "Diner and restaurants, 8th Ave & W19th", "food", 150, 300, 12, 600, "gas_boiler", 1910, 16, 0.30, ""),
    ("CH-30", "Supermarket, 8th Ave & W21st", "food", 160, 480, 12, 3000, "gas_boiler", 1990, 20, 0.35, ""),
    ("CH-31", "Residential tower, 8th Ave & W24th", "residential", 180, 720, 75, 1300, "gas_boiler", 2004, 18, 0.30, ""),
    ("CH-32", "Office, 7th Ave & W16th", "office", 470, 60, 50, 2000, "steam", 1912, None, 0.10, ""),
    ("CH-33", "Walk-up apartments, W17th", "residential", 20, 140, 18, 900, "oil", 1900, 29, 0.55, ""),
    ("CH-34", "Walk-up apartments, W14th", "residential", -40, -120, 18, 900, "gas_boiler", 1905, 23, 0.50, ""),
    ("CH-35", "Retail, W14th St corridor", "retail", 300, -130, 20, 1800, "steam", 1925, None, 0.25, ""),
    ("CH-36", "Mixed-use, 10th Ave & W18th", "residential", -460, 220, 30, 1200, "oil", 1935, 26, 0.50, ""),
    ("CH-37", "Office, 10th Ave & W19th", "office", -600, 380, 55, 3200, "gas_boiler", 2009, 12, 0.10, ""),
    ("CH-38", "Restaurant row, 9th Ave & W16th", "food", -170, 60, 12, 700, "gas_boiler", 1915, 19, 0.30, ""),
    ("CH-39", "Apartments, 8th Ave & W12th", "residential", 150, -300, 30, 1100, "gas_boiler", 1925, 17, 0.35, ""),
    ("CH-40", "Hotel, 7th Ave & W27th", "residential", 420, 920, 70, 1700, "gas_boiler", 2011, 11, 0.20, "hotel"),
]

# Lansing local frame: x east, y north of the data center; Cayuga Lake lies west (x < 0).
# Home clusters: footprint/floor area are cluster totals (homes x ~150 / ~170 m2).
_LANSING: list[tuple] = [
    ("LA-01", "Greenhouse 1", "greenhouse", 180, -160, 7, 10000, "propane", 2027, None, 0.30, "proposed on-site greenhouse park"),
    ("LA-02", "Greenhouse 2", "greenhouse", 300, -160, 7, 10000, "propane", 2027, None, 0.30, "proposed on-site greenhouse park"),
    ("LA-03", "Greenhouse 3", "greenhouse", 420, -160, 7, 10000, "propane", 2027, None, 0.30, "proposed on-site greenhouse park"),
    ("LA-04", "Greenhouse 4", "greenhouse", 180, -300, 7, 10000, "propane", 2028, None, 0.30, "proposed on-site greenhouse park"),
    ("LA-05", "Greenhouse 5", "greenhouse", 300, -300, 7, 10000, "propane", 2028, None, 0.30, "proposed on-site greenhouse park"),
    ("LA-06", "Greenhouse 6", "greenhouse", 420, -300, 7, 10000, "propane", 2028, None, 0.30, "proposed on-site greenhouse park"),
    ("LA-07", "Aquaculture hall A", "aquaculture", 260, 180, 10, 4000, "propane", 2027, None, 0.30, "recirculating fish farm"),
    ("LA-08", "Aquaculture hall B", "aquaculture", 380, 180, 10, 4000, "propane", 2028, None, 0.30, "recirculating fish farm"),
    ("LA-09", "High school", "school", 3200, 1600, 12, 9000, "oil", 1972, 26, 0.55, "low-temperature hydronic retrofit assumed"),
    ("LA-10", "Middle school", "school", 3350, 1750, 10, 7000, "oil", 1968, 30, 0.55, "low-temperature hydronic retrofit assumed"),
    ("LA-11", "Elementary school", "school", 2900, 1250, 8, 5000, "propane", 1990, 22, 0.60, "low-temperature hydronic retrofit assumed"),
    ("LA-12", "Community center", "school", 2700, 1450, 8, 2500, "propane", 1985, 24, 0.60, "community center (public facility)"),
    ("LA-13", "Lake Shore homes N1 (24 homes)", "home", 250, 1100, 7, 3600, "propane", 1965, 21, 0.40, "cluster of 24 homes"),
    ("LA-14", "Lake Shore homes N2 (30 homes)", "home", 300, 1700, 7, 4500, "propane", 1972, 19, 0.40, "cluster of 30 homes"),
    ("LA-15", "Lake Shore homes N3 (18 homes)", "home", 280, 2400, 7, 2700, "oil", 1958, 27, 0.45, "cluster of 18 homes"),
    ("LA-16", "Lake Shore homes N4 (22 homes)", "home", 320, 3100, 7, 3300, "propane", 1980, 16, 0.40, "cluster of 22 homes"),
    ("LA-17", "Hamlet homes E1 (35 homes)", "home", 1200, 250, 7, 5250, "propane", 1975, 22, 0.50, "cluster of 35 homes"),
    ("LA-18", "Hamlet homes E2 (28 homes)", "home", 1800, 350, 7, 4200, "oil", 1962, 28, 0.55, "cluster of 28 homes"),
    ("LA-19", "Hamlet homes E3 (40 homes)", "home", 2400, 500, 7, 6000, "propane", 1988, 14, 0.45, "cluster of 40 homes"),
    ("LA-20", "Ridge homes (26 homes)", "home", 2800, 1000, 7, 3900, "electric", 1995, None, 0.40, "cluster of 26 homes"),
    ("LA-21", "Village homes V1 (32 homes)", "home", 3000, 1900, 7, 4800, "propane", 1970, 23, 0.45, "cluster of 32 homes"),
    ("LA-22", "Village homes V2 (20 homes)", "home", 3500, 1400, 7, 3000, "oil", 1955, 33, 0.50, "cluster of 20 homes"),
    ("LA-23", "Senior cottages (16 homes)", "home", 2600, 1600, 6, 2000, "propane", 1992, 20, 0.70, "senior living cottages"),
    ("LA-24", "Mobile home park (45 homes)", "home", 1500, 800, 5, 3600, "propane", 1978, 25, 0.85, "manufactured homes"),
    ("LA-25", "Farmhouses south (12 homes)", "home", 900, -1300, 7, 1800, "oil", 1940, 30, 0.40, "cluster of 12 homes"),
]

INTENSITY_KWH_M2 = {  # annual heat per floor area, assumption - verify
    "residential": 140, "public_housing": 160, "office": 95, "retail": 85, "food": 260,
    "clinic": 210, "school": 120, "greenhouse": 350, "aquaculture": 450, "home": 150,
}
DHW_SHARE = {
    "residential": 0.25, "public_housing": 0.28, "office": 0.08, "retail": 0.05, "food": 0.40,
    "clinic": 0.20, "school": 0.10, "greenhouse": 0.0, "aquaculture": 0.60, "home": 0.20,
}
OM_USD_PER_MWH = 8.0


def _required_temp(site: SiteId, use: UseType, heating: HeatingSystem, year: int | None) -> float:
    if site == "lansing":
        return {"greenhouse": 40.0, "aquaculture": 28.0, "school": 45.0, "home": 45.0}.get(use, 45.0)
    if heating == "steam":
        return 110.0
    if use in ("food", "clinic"):
        return 70.0
    y = year or 1960
    return 75.0 if y < 1980 else 60.0 if y < 2005 else 50.0


def _to_latlon(site: SiteId, x: float, y: float) -> tuple[float, float]:
    s = load_site(site).site
    r = math.radians(s.grid_rotation_deg)
    north = y * math.cos(r) - x * math.sin(r)
    east = y * math.sin(r) + x * math.cos(r)
    lat = s.lat + north / 111_320
    lon = s.lon + east / (111_320 * math.cos(math.radians(s.lat)))
    return round(lat, 6), round(lon, 6)


def _street_distance(site: SiteId, x: float, y: float, footprint: float) -> float:
    hx, hy = DC_HALF[site]
    half = math.sqrt(footprint) / 2
    if site == "chelsea":   # grid streets: Manhattan distance from the data center's block edge
        return round(max(0, abs(x) - hx - half) + max(0, abs(y) - hy - half) + 20, 1)
    return round((abs(x) + abs(y)) * 1.05 + 30, 1)   # rural roads, small detour factor


@lru_cache(maxsize=4)
def _mock_buildings(site: SiteId) -> tuple[Building, ...]:
    cfg = load_site(site)
    rows = _CHELSEA if site == "chelsea" else _LANSING
    out = []
    for bid, name, use, x, y, h, fp, heat, year, boiler, eq, notes in rows:
        rng = np.random.default_rng(_seed(site, bid))
        floors = max(1, round(h / 3.4))
        floor_area = fp * floors * (1.0 if use in ("greenhouse", "aquaculture") else 0.9)
        if use == "home":
            floor_area = fp / 150 * 170
        if use in ("greenhouse", "aquaculture"):
            floor_area = float(fp)
        annual = floor_area * INTENSITY_KWH_M2[use] * rng.uniform(0.9, 1.1) / 1000
        fuel = cfg.prices.fuels_usd_per_mwh[heat].value
        eff = cfg.backup_efficiency.get(heat, cfg.backup_efficiency["unknown"]).value
        cost = (fuel / eff + OM_USD_PER_MWH) * rng.uniform(0.94, 1.06)
        lat, lon = _to_latlon(site, x, y)
        out.append(Building(
            id=bid, name=name, site=site, lat=lat, lon=lon, x_m=float(x), y_m=float(y),
            street_distance_m=_street_distance(site, x, y, fp), height_m=float(h),
            footprint_m2=float(fp), floor_area_m2=round(floor_area, 1), use_type=use,
            year_built=year, heating_system=heat, annual_heat_mwh=round(annual, 1),
            current_heat_cost_usd_per_mwh=round(cost, 2),
            required_supply_temp_c=_required_temp(site, use, heat, year),
            boiler_age_years=boiler, equity_score=eq, is_estimated=True,
            notes=("MOCK. " + notes).strip(),
        ))
    return tuple(out)


class MockBuildingProvider:
    """Hand-placed synthetic buildings on each site's street grid.

    ML_TEAM_INTEGRATION: replace with PLUTO / LL84 / NYCHA / assessor data.
    """

    def get_buildings(self, site: SiteId) -> list[Building]:
        return [b.model_copy() for b in _mock_buildings(site)]

    def model_card(self) -> ModelCard:
        return ModelCard(
            name="Mock building inventory", version="0.1",
            method="hand-placed archetypes; heat = floor area x use-type intensity",
            datasets=["synthetic"],
            assumptions=[f"{k}: {v} kWh/m2/yr" for k, v in INTENSITY_KWH_M2.items()]
                        + ["current cost = fuel price / boiler efficiency + $8/MWh O&M"],
            is_mock=True)


# ===================================================================== demand

def _gauss(h: np.ndarray, mu: float, sd: float) -> np.ndarray:
    return np.exp(-(((h - mu) / sd) ** 2))


def _occupancy(use: UseType, idx: pd.DatetimeIndex) -> np.ndarray:
    h = idx.hour.to_numpy()
    weekday = idx.dayofweek.to_numpy() < 5
    month = idx.month.to_numpy()
    if use in ("office", "retail"):
        return np.where(weekday & (h >= 7) & (h < 19), 1.0, 0.65)
    if use == "school":
        return np.where(weekday & (h >= 7) & (h < 16), 1.0, 0.5) * np.where(np.isin(month, [7, 8]), 0.3, 1.0)
    if use == "greenhouse":
        return np.where((h >= 7) & (h < 18), 0.5, 1.4)   # solar gain by day
    if use == "aquaculture":
        return np.ones(len(idx))
    return np.where((h >= 23) | (h < 6), 0.9, 1.0)


def _dhw_shape(use: UseType, idx: pd.DatetimeIndex) -> np.ndarray:
    h = idx.hour.to_numpy().astype(float)
    weekday = idx.dayofweek.to_numpy() < 5
    if use in ("office", "retail"):
        return (0.2 + _gauss(h, 12, 4)) * np.where(weekday, 1.0, 0.4)
    if use == "school":
        return (0.1 + _gauss(h, 11, 3)) * np.where(weekday, 1.0, 0.1)
    if use == "food":
        return 0.3 + _gauss(h, 12.5, 1.8) + 1.2 * _gauss(h, 19, 2)
    if use in ("aquaculture", "greenhouse"):
        return np.ones(len(idx))
    return 0.4 + 1.6 * _gauss(h, 7.5, 1.5) + 1.3 * _gauss(h, 19.5, 2.0)


class MockDemandProvider:
    """Degree-day space heat + daily hot-water profile, calibrated so a typical
    year sums to each building's annual_heat_mwh. p05/p95 = p50 x (1 -/+ 0.15).

    ML_TEAM_INTEGRATION: replace with the trained demand model.
    """

    BAND = 0.15

    def __init__(self, buildings: "BuildingProvider", weather: "WeatherProvider"):
        self.buildings = buildings
        self.weather = weather
        self._norms: dict[tuple[str, str], tuple[float, float]] = {}

    def _drivers(self, b: Building, idx: pd.DatetimeIndex, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        balance = 20.0 if b.use_type == "greenhouse" else 18.0
        space = np.maximum(0.0, balance - t) * _occupancy(b.use_type, idx)
        clim = CLIMATES[load_site(b.site).site.climate]
        doy = idx.dayofyear.to_numpy()
        seasonal = 1 + 0.15 * np.cos(2 * np.pi * (doy - clim.coldest_doy) / 365.25)
        return space, _dhw_shape(b.use_type, idx) * seasonal

    def _norm(self, b: Building) -> tuple[float, float]:
        key = (b.site, b.id)
        if key not in self._norms:
            ref_idx = hour_index(b.site, REFERENCE_YEAR_START, 8760)
            ref_t = np.asarray(self.weather.get_weather(b.site, ref_idx[0], 8760).t_out_c)
            space, dhw = self._drivers(b, ref_idx, ref_t)
            self._norms[key] = (space.sum(), dhw.sum())
        return self._norms[key]

    def profile_kw(self, b: Building, idx: pd.DatetimeIndex, t: np.ndarray) -> np.ndarray:
        space, dhw = self._drivers(b, idx, t)
        space_norm, dhw_norm = self._norm(b)
        annual_kwh = b.annual_heat_mwh * 1000
        share = DHW_SHARE[b.use_type]
        kw = annual_kwh * (1 - share) * space / space_norm + annual_kwh * share * dhw / dhw_norm
        noise = 1 + np.clip(_block_noise(f"demand|{b.site}|{b.id}", _epoch_hours(idx), 0.04), -0.15, 0.15)
        return np.maximum(kw * noise, 0.0)

    def get_demand_forecast(self, site: SiteId, building_ids: Sequence[str], start: datetime,
                            hours: int, weather_scenario: WeatherScenario = "typical") -> list[DemandForecast]:
        idx = hour_index(site, start, hours)
        t = np.asarray(self.weather.get_weather(site, idx[0], hours, weather_scenario).t_out_c)
        by_id = {b.id: b for b in self.buildings.get_buildings(site)}
        unknown = set(building_ids) - by_id.keys()
        if unknown:
            raise KeyError(f"unknown building ids for {site}: {sorted(unknown)}")
        stamps = list(idx.to_pydatetime())
        out = []
        for bid in building_ids:
            p50 = np.round(self.profile_kw(by_id[bid], idx, t), 2)
            out.append(DemandForecast(
                building_id=bid, hours=stamps,
                p05=np.round(p50 * (1 - self.BAND), 2).tolist(), p50=p50.tolist(),
                p95=np.round(p50 * (1 + self.BAND), 2).tolist()))
        return out

    def model_card(self) -> ModelCard:
        return ModelCard(
            name="Mock demand model", version="0.1",
            method="degree-day (base 18 C) space heat + hot-water daily profile, calibrated to annual totals",
            datasets=["mock building inventory", "mock weather"],
            assumptions=[f"p05/p95 = p50 x (1 -/+ {self.BAND})",
                         "hot-water shares by use type: " + ", ".join(f"{k} {v:.0%}" for k, v in DHW_SHARE.items())],
            is_mock=True)


# ===================================================================== supply

class MockSupplyProvider:
    """Recoverable data center heat: capacity x capture x daily load cycle.

    ML_TEAM_INTEGRATION: replace with the supply model trained on IT load data.
    """

    def get_supply_forecast(self, site: SiteId, start: datetime, hours: int,
                            scenario: SupplyScenario = "base") -> SupplyForecast:
        dc = load_site(site).data_center
        idx = hour_index(site, start, hours)
        h = idx.hour.to_numpy()
        lf = 0.85 + 0.10 * np.sin(2 * np.pi * (h - 8) / 24)
        lf = np.clip(lf + _block_noise(f"supply|{site}", _epoch_hours(idx), 0.015), 0.72, 0.98)
        cap = dc.capacity_mw_th.value * 1000 * dc.capture_fraction.value
        p50 = cap * lf
        tmin, tmax = dc.supply_temp_c_min.value, dc.supply_temp_c_max.value
        temp = tmin + (tmax - tmin) * np.clip((lf - 0.75) / 0.2, 0, 1)

        if scenario == "tenant_leaves":
            p50 = p50 * 0.7
        elif scenario == "server_outage":
            p50[:6] = 0.0
            temp[:6] = tmin
        elif scenario == "flex_off":
            p50 = p50 * (1 - dc.flexible_compute_share.value)

        return SupplyForecast(
            hours=list(idx.to_pydatetime()), p05=np.round(p50 * 0.9, 2).tolist(),
            p50=np.round(p50, 2).tolist(), p95=np.round(np.minimum(p50 * 1.05, cap), 2).tolist(),
            supply_temp_c=np.round(temp, 2).tolist())

    def model_card(self) -> ModelCard:
        return ModelCard(
            name="Mock supply model", version="0.1",
            method="capacity x capture fraction x daily load factor (0.75-0.95) + noise",
            datasets=["site config"], assumptions=["p05 = 0.9 x p50; p95 = 1.05 x p50 (capped)"],
            is_mock=True)


# ===================================================================== confidence

DRIVERS = {
    "cop_eta": "Heat pump efficiency",
    "demand_mult": "Building heat demand",
    "supply_mult": "Data center heat supply",
    "electricity_price_mult": "Electricity price",
    "fuel_price_mult": "Fuel / steam price",
}


class MockConfidenceProvider:
    """~100 jittered futures through the engine's simulator.

    ML_TEAM_INTEGRATION: replace with 1,000-run Latin hypercube + GEV cold snap.
    """

    def __init__(self, simulator: "FutureSimulator"):
        self.simulator = simulator

    def sample_futures(self, state: SimState, n: int) -> list[FutureInputs]:
        rng = np.random.default_rng(_seed("futures", state.site, state.hour_index, state.time, n))
        return [FutureInputs(
            seed=int(rng.integers(2**31)),
            cop_eta=float(rng.uniform(0.5, 0.8)), demand_mult=float(rng.uniform(0.85, 1.15)),
            supply_mult=float(rng.uniform(0.8, 1.2)), electricity_price_mult=float(rng.uniform(0.8, 1.2)),
            fuel_price_mult=float(rng.uniform(0.8, 1.2))) for _ in range(n)]

    def get_confidence(self, site: SiteId, plan: Plan, state: SimState, n_futures: int = 100) -> ConfidenceResult:
        cfg = load_site(site)
        futures = self.sample_futures(state, n_futures)
        if hasattr(self.simulator, "simulate_futures"):
            outcomes = self.simulator.simulate_futures(plan, state, futures)
        else:
            outcomes = [self.simulator.simulate_future(plan, state, f) for f in futures]

        guaranteed = plan.guaranteed_ids()
        # every connected building fully served by network + storage (no backup) over the horizon
        all_warm = [all(h == 0 for h in o.unmet_hours_by_building.values()) for o in outcomes]
        unmet_hours = [max(o.unmet_hours_by_building.values(), default=0) for o in outcomes]
        party_ahead = {p.id: float(np.mean([o.party_net_usd.get(p.id, 0.0) > 0 for o in outcomes]))
                       for p in cfg.parties}

        alpha = 0.95
        prices = {}
        for b in guaranteed:
            r = np.array([o.refunds_usd_by_building.get(b, 0.0) for o in outcomes])
            tail = np.sort(r)[int(np.floor(alpha * len(r))):]
            cvar = float(tail.mean()) if len(tail) else float(r.max())
            prices[b] = round(r.mean() + (cvar - r.mean()), 2)   # expected refunds + CVaR95 risk margin

        kept = {b: float(np.mean([o.unmet_hours_by_building.get(b, 0) == 0 for o in outcomes])) for b in guaranteed}
        return ConfidenceResult(
            p_all_warm=float(np.mean(all_warm)), p_each_party_ahead=party_ahead,
            expected_unmet_hours=float(np.mean(unmet_hours)), guarantee_prices=prices, p_guarantee_kept=kept,
            top_uncertainty_drivers=self._drivers(futures, outcomes),
            n_futures=n_futures, horizon_hours=state.horizon_hours, method="mock_uniform_jitter")

    @staticmethod
    def _drivers(futures: Sequence[FutureInputs], outcomes: Sequence[FutureOutcome]) -> list[UncertaintyDriver]:
        """Squared standardized regression coefficients on the outcome that varies."""
        X = np.array([[getattr(f, k) for k in DRIVERS] for f in futures])
        y = np.array([o.unmet_mwh for o in outcomes])
        if y.std() < 1e-9:
            y = np.array([o.system_cost_usd for o in outcomes])
        if y.std() < 1e-9:
            return [UncertaintyDriver(name=n, share=1 / len(DRIVERS)) for n in DRIVERS.values()]
        Xs = (X - X.mean(0)) / X.std(0)
        beta, *_ = np.linalg.lstsq(np.c_[np.ones(len(y)), Xs], (y - y.mean()) / y.std(), rcond=None)
        w = beta[1:] ** 2
        shares = w / w.sum()
        ranked = sorted(zip(DRIVERS.values(), shares), key=lambda t: -t[1])
        return [UncertaintyDriver(name=n, share=round(float(s), 4)) for n, s in ranked]

    def model_card(self) -> ModelCard:
        return ModelCard(
            name="Mock confidence engine", version="0.1",
            method="100 futures, uniform jitter, engine simulator",
            datasets=["engine simulator outputs"],
            assumptions=["COP efficiency U(0.5, 0.8)", "demand x U(0.85, 1.15)", "supply x U(0.8, 1.2)",
                         "electricity and fuel price x U(0.8, 1.2)",
                         "guarantee premium = E[refunds] + (CVaR95 - E[refunds])"],
            is_mock=True)


# ===================================================================== Jev

def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


class MockJevProvider:
    """Rule-based stand-in for Jev's typed playbook decision.

    ML_TEAM_INTEGRATION: replace with the TypeSafe AI Jev client.
    """

    def get_jev_opinion(self, state: SimState) -> JevOpinion:
        cfg = load_site(state.site)
        dis_kw = sum(s.max_discharge_mw.value for s in cfg.storage) * 1000
        soc_frac = state.storage_soc_mwh / state.storage_capacity_mwh if state.storage_capacity_mwh else 0.0
        storage_kw = dis_kw if soc_frac > 0.1 else 0.0
        deficit = state.demand_kw - state.supply_kw
        margin = (state.supply_kw + storage_kw - state.demand_kw) / max(state.demand_kw, 1.0)

        if deficit <= 0:
            playbook = "curtail_cooling_export" if cfg.loop.sells_cooling and state.t_out_c > 24 else "draw_storage"
            p_play = 0.55 + 0.4 * _sigmoid(-deficit / max(state.supply_kw, 1.0) * 4)
        elif storage_kw >= deficit and soc_frac > 0.25:
            playbook, p_play = "draw_storage", 0.80 + 0.15 * min(1, soc_frac)
        elif state.flexible_compute_available and cfg.data_center.compute_follows_heat:
            playbook, p_play = "shift_compute", 0.82
        elif state.site == "chelsea" and soc_frac > 0.1:
            playbook, p_play = "start_steam_hp", 0.76
        else:
            playbook, p_play = "start_backup", 0.88

        latency = 40 + (_seed("jev", state.site, state.hour_index) % 100)
        return JevOpinion(
            playbook=playbook, playbook_probability=round(min(p_play, 0.99), 4),
            p_supply_meets_guarantees=round(_sigmoid(6 * margin + 1.5), 4),
            latency_ms=float(latency), available=True)

    def model_card(self) -> ModelCard:
        return ModelCard(name="Mock Jev", version="0.1", method="rule-based playbook choice",
                         datasets=[], assumptions=["probabilities from a logistic margin heuristic"],
                         is_mock=True)
