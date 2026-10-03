"""Site configuration: YAML -> typed SiteConfig.

Every tunable number is a Param carrying its value, an optional (low, high)
range and a source label, so the report can trace each assumption. In YAML a
Param can be a bare number or {value, range, source}.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from engine.contracts import SiteId, UseType

SITES_DIR = Path(__file__).parent / "sites"
VERIFY = "assumption - verify"


class Param(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float
    range: tuple[float, float] | None = None
    source: str = VERIFY

    @model_validator(mode="before")
    @classmethod
    def _from_scalar(cls, data: Any) -> Any:
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            return {"value": float(data)}
        return data

    @model_validator(mode="after")
    def _range_contains_value(self) -> "Param":
        if self.range is not None:
            lo, hi = self.range
            if not lo <= self.value <= hi:
                raise ValueError(f"value {self.value} outside range {self.range}")
        return self

    def __float__(self) -> float:
        return self.value

    @property
    def low(self) -> float:
        return self.range[0] if self.range else self.value

    @property
    def high(self) -> float:
        return self.range[1] if self.range else self.value


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SiteInfo(_Section):
    id: SiteId
    name: str
    address: str
    owner: str
    lat: float
    lon: float
    timezone: str
    climate: Literal["nyc", "ithaca"]
    recommended: bool
    grid_rotation_deg: float = 0.0


class DataCenter(_Section):
    name: str
    capacity_mw_th: Param
    capture_fraction: Param
    supply_temp_c_min: Param
    supply_temp_c_max: Param
    it_load_mw: Param
    pue: Param
    chiller_cop: Param
    cooling_towers: bool
    liquid_cooled: bool
    flexible_compute_share: Param
    compute_follows_heat: bool


class Loop(_Section):
    type: Literal["ambient_two_way", "warm"]
    supply_temp_c: Param
    return_temp_c: Param
    pipe_material: str
    pipe_loss_per_km: Param
    ambient_loss_ref_c: float = 12.0
    ambient_loss_span_c: float = 40.0
    direct_link_max_m: Param
    direct_use_margin_c: Param
    sells_cooling: bool
    cooling_cop: Param | None = None


class HeatPumps(_Section):
    eta: Param
    steam_hp_t_hot_c: Param
    steam_hp_eta: Param
    booster_t_hot_c: Param


class Storage(_Section):
    id: str
    type: Literal["hot_water_tank", "borehole", "pit"]
    capacity_mwh: Param
    loss_per_hour: Param
    max_charge_mw: Param
    max_discharge_mw: Param
    min_soc_fraction: Param
    initial_soc_fraction: Param


class Pumping(_Section):
    design_flow_m3h: Param
    design_power_kw: Param


class Prices(_Section):
    electricity_usd_per_mwh: Param
    fuels_usd_per_mwh: dict[str, Param]
    cooling_sale_usd_per_mwh: Param


class Emissions(_Section):
    grid_t_per_mwh: Param
    fuels_t_per_mwh: dict[str, Param]
    water_gal_per_kwh_electricity: Param


class Finance(_Section):
    discount_rate: Param
    horizon_years: int


class Party(_Section):
    id: str
    name: str
    role: str
    pays_for: list[str]
    earns: list[str]
    terms: dict[str, Param] = {}


class SiteConfig(_Section):
    site: SiteInfo
    data_center: DataCenter
    loop: Loop
    heat_pumps: HeatPumps
    storage: list[Storage]
    pumping: Pumping
    prices: Prices
    emissions: Emissions
    backup_efficiency: dict[str, Param]
    finance: Finance
    capex: dict[str, Param]
    policy: dict[str, Param] = {}
    parties: list[Party]
    party_by_use_type: dict[UseType, str]
    guarantee_use_types: list[UseType] = []
    guarantee_building_ids: list[str] = []

    @model_validator(mode="after")
    def _party_refs(self) -> "SiteConfig":
        ids = {p.id for p in self.parties}
        missing = set(self.party_by_use_type.values()) - ids
        if missing:
            raise ValueError(f"party_by_use_type references unknown parties: {missing}")
        return self

    def party(self, party_id: str) -> Party:
        return next(p for p in self.parties if p.id == party_id)

    def iter_params(self) -> Iterator[tuple[str, Param]]:
        """Yield (dotted.path, Param) for every assumption, for the report."""
        yield from _walk(self, "")


def _walk(obj: Any, prefix: str) -> Iterator[tuple[str, Param]]:
    if isinstance(obj, Param):
        yield prefix, obj
    elif isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            yield from _walk(getattr(obj, name), f"{prefix}.{name}" if prefix else name)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = getattr(v, "id", i)
            yield from _walk(v, f"{prefix}[{key}]")


@lru_cache(maxsize=None)
def load_site(site: SiteId) -> SiteConfig:
    path = SITES_DIR / f"{site}.yaml"
    with path.open() as f:
        return SiteConfig.model_validate(yaml.safe_load(f))
