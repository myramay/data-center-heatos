"""Interface contracts between the HeatOS engine and the ML team.

Every model here has a mirror in web/src/types.ts (enforced by
tests/test_contracts.py::test_typescript_parity). Units are in field names
or docstrings; all power series are kW thermal unless noted, hourly.

ML_TEAM_INTEGRATION: these shapes are the agreement. Real providers in ml/
must return exactly these models. Changing a field means changing it here,
in types.ts, and in ml/README.md together.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------- enums

SiteId = Literal["chelsea", "lansing"]
UseType = Literal[
    "residential", "public_housing", "office", "retail", "food", "school",
    "greenhouse", "aquaculture", "clinic", "home",
]
HeatingSystem = Literal["steam", "gas_boiler", "oil", "propane", "electric", "unknown"]
WeatherScenario = Literal[
    "typical", "cold_year", "warm_year", "polar_vortex", "heat_wave", "lake_effect",
]
SupplyScenario = Literal["base", "tenant_leaves", "server_outage", "flex_off"]
Playbook = Literal[
    "draw_storage", "start_steam_hp", "start_backup", "shift_compute", "curtail_cooling_export",
]
Verdict = Literal["ACT", "REVIEW", "ESCALATE"]
ConnectionOption = Literal[
    "direct_link", "loop_hp", "steam_hp", "direct_use", "booster", "central_hp", "not_connected",
]

Probability = Field(ge=0.0, le=1.0)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


def _check_bands(p05: list[float], p50: list[float], p95: list[float], n: int) -> None:
    if not (len(p05) == len(p50) == len(p95) == n):
        raise ValueError(f"series lengths differ: hours={n} p05={len(p05)} p50={len(p50)} p95={len(p95)}")
    for i, (lo, mid, hi) in enumerate(zip(p05, p50, p95)):
        if lo < 0 or not (lo <= mid + 1e-9 and mid <= hi + 1e-9):
            raise ValueError(f"band violated at hour {i}: p05={lo} p50={mid} p95={hi}")


# ---------------------------------------------------------------- (a) Building

class Building(_Contract):
    """One candidate building (or, for Lansing homes, one cluster of homes)."""

    id: str
    name: str
    site: SiteId
    lat: float
    lon: float
    x_m: float = Field(description="local metres east (crosstown) of the data center")
    y_m: float = Field(description="local metres north (uptown) of the data center")
    street_distance_m: float = Field(ge=0, description="pipe route length along streets")
    height_m: float = Field(gt=0)
    footprint_m2: float = Field(gt=0)
    floor_area_m2: float = Field(gt=0)
    use_type: UseType
    year_built: int | None = None
    heating_system: HeatingSystem
    annual_heat_mwh: float = Field(ge=0)
    current_heat_cost_usd_per_mwh: float = Field(ge=0)
    required_supply_temp_c: float
    boiler_age_years: float | None = None
    equity_score: float = Probability
    is_estimated: bool
    notes: str = ""


# ---------------------------------------------------------------- weather (added)

class WeatherSeries(_Contract):
    hours: list[datetime]
    t_out_c: list[float]
    scenario: WeatherScenario

    @model_validator(mode="after")
    def _lengths(self) -> "WeatherSeries":
        if len(self.hours) != len(self.t_out_c):
            raise ValueError("hours and t_out_c lengths differ")
        return self


# ---------------------------------------------------------------- (b) DemandForecast

class DemandForecast(_Contract):
    building_id: str
    hours: list[datetime]
    p05: list[float]
    p50: list[float]
    p95: list[float]

    @model_validator(mode="after")
    def _bands(self) -> "DemandForecast":
        _check_bands(self.p05, self.p50, self.p95, len(self.hours))
        return self


# ---------------------------------------------------------------- (c) SupplyForecast

class SupplyForecast(_Contract):
    hours: list[datetime]
    p05: list[float]
    p50: list[float]
    p95: list[float]
    supply_temp_c: list[float]

    @model_validator(mode="after")
    def _bands(self) -> "SupplyForecast":
        _check_bands(self.p05, self.p50, self.p95, len(self.hours))
        if len(self.supply_temp_c) != len(self.hours):
            raise ValueError("supply_temp_c length differs from hours")
        return self


# ---------------------------------------------------------------- engine-owned inputs to ML providers

class PlanItem(_Contract):
    building_id: str
    option: ConnectionOption
    connect: bool
    npv_usd: float
    phase: int | None = Field(default=None, ge=1, le=3)
    guaranteed: bool = False
    design_capacity_kw: float = Field(ge=0, default=0.0)
    reason_codes: list[str] = []


class Plan(_Contract):
    """Output of recommend.py; passed into get_confidence."""

    site: SiteId
    items: list[PlanItem]
    created_by: str = "recommend.py"

    def connected_ids(self) -> list[str]:
        return [i.building_id for i in self.items if i.connect]

    def guaranteed_ids(self) -> list[str]:
        return [i.building_id for i in self.items if i.connect and i.guaranteed]


class SimState(_Contract):
    """Snapshot of the running network at the current simulated hour."""

    site: SiteId
    time: datetime
    hour_index: int = Field(ge=0)
    horizon_hours: int = Field(default=48, ge=1, le=8784)
    t_out_c: float
    supply_kw: float = Field(ge=0)
    demand_kw: float = Field(ge=0)
    storage_soc_mwh: float = Field(ge=0)
    storage_capacity_mwh: float = Field(ge=0)
    unmet_kw: float = Field(ge=0, default=0.0)
    backup_kw: float = Field(ge=0, default=0.0)
    electricity_price_usd_per_mwh: float = Field(ge=0)
    flexible_compute_available: bool = False
    active_scenarios: list[str] = []
    weather_scenario: WeatherScenario = "typical"
    supply_scenario: SupplyScenario = "base"
    run_id: str | None = Field(default=None, description="live run; futures branch from its inputs")


# ---------------------------------------------------------------- (d) ConfidenceResult

class UncertaintyDriver(_Contract):
    name: str
    share: float = Probability


class ConfidenceResult(_Contract):
    p_all_warm: float = Probability
    p_each_party_ahead: dict[str, float]
    expected_unmet_hours: float = Field(ge=0)
    guarantee_prices: dict[str, float] = Field(
        description="building_id -> USD premium for the evaluated horizon "
                    "(expected refunds + CVaR95 risk margin)")
    p_guarantee_kept: dict[str, float] = Field(
        default_factory=dict, description="building_id -> P(no missed hour over the horizon)")
    top_uncertainty_drivers: list[UncertaintyDriver]
    n_futures: int = Field(ge=1)
    horizon_hours: int = Field(ge=1)
    method: str

    @model_validator(mode="after")
    def _probabilities(self) -> "ConfidenceResult":
        for party, p in self.p_each_party_ahead.items():
            if not 0.0 <= p <= 1.0:
                raise ValueError(f"p_each_party_ahead[{party}]={p} outside [0,1]")
        return self


# ---------------------------------------------------------------- Monte Carlo <-> simulator

class FutureInputs(_Contract):
    """One sampled future. The confidence engine samples these and asks the
    engine's simulator to play them out.

    ML_TEAM_INTEGRATION: the real engine replaces uniform jitter with Latin
    hypercube draws (and adds GEV cold snaps via scenarios).
    """

    seed: int
    cop_eta: float = Field(gt=0, le=1)
    demand_mult: float = Field(gt=0)
    supply_mult: float = Field(ge=0)
    electricity_price_mult: float = Field(gt=0)
    fuel_price_mult: float = Field(gt=0)


class FutureOutcome(_Contract):
    unmet_hours_by_building: dict[str, int]
    unmet_mwh: float = Field(ge=0)
    refunds_usd_by_building: dict[str, float]
    party_net_usd: dict[str, float]
    system_cost_usd: float


# ---------------------------------------------------------------- (e) JevOpinion

class JevOpinion(_Contract):
    playbook: Playbook
    playbook_probability: float = Probability
    p_supply_meets_guarantees: float = Probability
    latency_ms: float = Field(ge=0)
    available: bool


# ---------------------------------------------------------------- model metadata (added)

class ModelCard(_Contract):
    """Metadata each provider exposes for report section 2 (data analysis)."""

    name: str
    version: str
    method: str
    datasets: list[str]
    assumptions: list[str]
    metrics: dict[str, float] = {}
    is_mock: bool
