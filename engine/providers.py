"""Provider registry: the single place where mock vs real ML modules are chosen.

Engine code calls the functions at the bottom of this module (get_buildings,
get_demand_forecast, ...) and never imports engine.mocks or ml.* directly.

ML_TEAM_INTEGRATION: to drop in a real module, change only the assignments in
the REGISTRY block, e.g.

    from ml.demand import DemandModel
    DEMAND: DemandProvider = DemandModel(buildings=BUILDINGS, weather=WEATHER)
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from engine import mocks
from engine.futures import EngineFutureSimulator
from engine.contracts import (
    Building, ConfidenceResult, DemandForecast, FutureInputs, FutureOutcome, JevOpinion, ModelCard,
    Plan, SiteId, SimState, SupplyForecast, SupplyScenario, WeatherScenario, WeatherSeries,
)


# ===================================================================== interfaces

@runtime_checkable
class BuildingProvider(Protocol):
    def get_buildings(self, site: SiteId) -> list[Building]: ...
    def model_card(self) -> ModelCard: ...


@runtime_checkable
class WeatherProvider(Protocol):
    def get_weather(self, site: SiteId, start: datetime, hours: int,
                    scenario: WeatherScenario = "typical") -> WeatherSeries: ...
    def model_card(self) -> ModelCard: ...


@runtime_checkable
class DemandProvider(Protocol):
    def get_demand_forecast(self, site: SiteId, building_ids: Sequence[str], start: datetime,
                            hours: int, weather_scenario: WeatherScenario = "typical") -> list[DemandForecast]: ...
    def model_card(self) -> ModelCard: ...


@runtime_checkable
class SupplyProvider(Protocol):
    def get_supply_forecast(self, site: SiteId, start: datetime, hours: int,
                            scenario: SupplyScenario = "base") -> SupplyForecast: ...
    def model_card(self) -> ModelCard: ...


@runtime_checkable
class ConfidenceProvider(Protocol):
    def get_confidence(self, site: SiteId, plan: Plan, state: SimState,
                       n_futures: int = 100) -> ConfidenceResult: ...
    def model_card(self) -> ModelCard: ...


@runtime_checkable
class JevProvider(Protocol):
    def get_jev_opinion(self, state: SimState) -> JevOpinion: ...
    def model_card(self) -> ModelCard: ...


@runtime_checkable
class FutureSimulator(Protocol):
    """Implemented by the ENGINE; called BY the confidence provider."""

    def simulate_future(self, plan: Plan, state: SimState, inputs: FutureInputs) -> FutureOutcome: ...


# ===================================================================== REGISTRY
# ML_TEAM_INTEGRATION: swap mocks.* for ml.* here. Nothing else changes.

BUILDINGS: BuildingProvider = mocks.MockBuildingProvider()
WEATHER: WeatherProvider = mocks.MockWeatherProvider()
DEMAND: DemandProvider = mocks.MockDemandProvider(buildings=BUILDINGS, weather=WEATHER)
SUPPLY: SupplyProvider = mocks.MockSupplyProvider()
# Engine-owned (real physics + ledger), not an ML provider.
SIMULATOR: FutureSimulator = EngineFutureSimulator()
CONFIDENCE: ConfidenceProvider = mocks.MockConfidenceProvider(simulator=SIMULATOR)
JEV: JevProvider = mocks.MockJevProvider()


# ===================================================================== facade used by the engine

def get_buildings(site: SiteId) -> list[Building]:
    return BUILDINGS.get_buildings(site)


def get_weather(site: SiteId, start: datetime, hours: int,
                scenario: WeatherScenario = "typical") -> WeatherSeries:
    return WEATHER.get_weather(site, start, hours, scenario)


def get_demand_forecast(site: SiteId, building_ids: Sequence[str], start: datetime, hours: int,
                        weather_scenario: WeatherScenario = "typical") -> list[DemandForecast]:
    return DEMAND.get_demand_forecast(site, building_ids, start, hours, weather_scenario)


def get_supply_forecast(site: SiteId, start: datetime, hours: int,
                        scenario: SupplyScenario = "base") -> SupplyForecast:
    return SUPPLY.get_supply_forecast(site, start, hours, scenario)


def get_confidence(site: SiteId, plan: Plan, state: SimState, n_futures: int = 100) -> ConfidenceResult:
    return CONFIDENCE.get_confidence(site, plan, state, n_futures)


def get_jev_opinion(state: SimState) -> JevOpinion:
    return JEV.get_jev_opinion(state)


def model_cards() -> dict[str, ModelCard]:
    return {name: p.model_card() for name, p in
            [("buildings", BUILDINGS), ("weather", WEATHER), ("demand", DEMAND),
             ("supply", SUPPLY), ("confidence", CONFIDENCE), ("jev", JEV)]}
