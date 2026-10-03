import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pydantic
import pytest

from engine import contracts, providers
from engine.config import load_site
from engine.contracts import Building, DemandForecast, JevOpinion, SimState
from engine.mocks import heuristic_plan
from engine.verdict import verdict

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 1, 20, 6)


def _state(site, when=START, horizon=48, weather="typical"):
    cfg = load_site(site)
    cap = sum(s.capacity_mwh.value for s in cfg.storage)
    soc = sum(s.capacity_mwh.value * s.initial_soc_fraction.value for s in cfg.storage)
    return SimState(site=site, time=when, hour_index=0, horizon_hours=horizon, t_out_c=0.0,
                    supply_kw=4000, demand_kw=5000, storage_soc_mwh=soc, storage_capacity_mwh=cap,
                    electricity_price_usd_per_mwh=cfg.prices.electricity_usd_per_mwh.value,
                    weather_scenario=weather)


# ------------------------------------------------------------------ site configs

@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_site_config_loads_and_every_param_has_a_source(site):
    cfg = load_site(site)
    params = list(cfg.iter_params())
    assert len(params) > 40
    assert all(p.source for _, p in params)


def test_chelsea_spec_values():
    cfg = load_site("chelsea")
    assert cfg.prices.electricity_usd_per_mwh.value == 225
    assert cfg.prices.electricity_usd_per_mwh.range == (200, 250)
    assert cfg.emissions.grid_t_per_mwh.range == (0.25, 0.442)
    assert cfg.data_center.capacity_mw_th.value == 5.0
    assert cfg.loop.pipe_loss_per_km.range == (0.005, 0.015)


# ------------------------------------------------------------------ buildings

def test_chelsea_buildings_match_spec():
    bs = providers.get_buildings("chelsea")
    assert 35 <= len(bs) <= 45
    ph = [b for b in bs if b.use_type == "public_housing"]
    assert 4 <= len(ph) <= 6 and all(b.x_m < 0 for b in ph), "public housing towers to the west"
    steam = sum(b.heating_system == "steam" for b in bs) / len(bs)
    assert 0.25 <= steam <= 0.38
    assert {"office", "food", "clinic"} <= {b.use_type for b in bs}
    assert max(b.street_distance_m for b in bs) < 1200
    assert any(b.street_distance_m < 100 for b in bs), "some direct-link candidates"
    assert len({b.id for b in bs}) == len(bs)


def test_lansing_buildings_match_spec():
    bs = providers.get_buildings("lansing")
    assert 20 <= len(bs) <= 30
    uses = Counter(b.use_type for b in bs)
    assert uses["greenhouse"] >= 3 and uses["aquaculture"] >= 1 and uses["school"] >= 3
    homes = [b for b in bs if b.use_type == "home"]
    assert all(1000 <= np.hypot(b.x_m, b.y_m) <= 4500 for b in homes)


def test_buildings_are_deterministic():
    a = providers.get_buildings("chelsea")
    b = providers.get_buildings("chelsea")
    assert [x.model_dump() for x in a] == [y.model_dump() for y in b]


# ------------------------------------------------------------------ weather / demand / supply

def test_weather_is_window_independent():
    long = providers.get_weather("chelsea", datetime(2026, 1, 1), 96)
    short = providers.get_weather("chelsea", datetime(2026, 1, 2), 24)
    assert long.t_out_c[24:48] == short.t_out_c


def test_weather_scenarios():
    base = np.array(providers.get_weather("chelsea", START, 96).t_out_c)
    pv = np.array(providers.get_weather("chelsea", START, 96, "polar_vortex").t_out_c)
    assert pv[12:60].max() < -15 and np.allclose(pv[80:], base[80:])
    hw = np.array(providers.get_weather("chelsea", datetime(2026, 7, 10), 168, "heat_wave").t_out_c)
    assert hw[6:102].reshape(4, 24).max(axis=1).min() > 32, "every wave day peaks above 32 C"


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_demand_calibrates_to_annual_totals(site):
    bs = providers.get_buildings(site)
    fc = providers.get_demand_forecast(site, [b.id for b in bs], datetime(2025, 1, 1), 8760)
    for b, f in zip(bs, fc):
        assert sum(f.p50) / 1000 == pytest.approx(b.annual_heat_mwh, rel=0.05), b.id
        assert np.allclose(f.p05, np.array(f.p50) * 0.85, atol=0.02)


def test_demand_responds_to_cold_and_has_daily_peaks():
    bid = "CH-01"
    jan = providers.get_demand_forecast("chelsea", [bid], datetime(2026, 1, 15), 168)[0]
    jul = providers.get_demand_forecast("chelsea", [bid], datetime(2026, 7, 15), 168)[0]
    pv = providers.get_demand_forecast("chelsea", [bid], datetime(2026, 1, 15), 168, "polar_vortex")[0]
    assert np.mean(jan.p50) > 3 * np.mean(jul.p50)
    assert np.mean(pv.p50[:72]) > np.mean(jan.p50[:72])
    summer = np.array(jul.p50).reshape(7, 24).mean(axis=0)
    assert summer[7] > summer[3] and summer[19] > summer[3], "morning and evening hot-water peaks"


def test_unknown_building_id_raises():
    with pytest.raises(KeyError):
        providers.get_demand_forecast("chelsea", ["NOPE"], START, 24)


@pytest.mark.parametrize("site,cap_kw", [("chelsea", 5000 * 0.85), ("lansing", 20000 * 0.85)])
def test_supply_forecast(site, cap_kw):
    s = providers.get_supply_forecast(site, START, 48)
    p50 = np.array(s.p50)
    assert (p50 >= 0.72 * cap_kw - 1).all() and (p50 <= cap_kw).all()
    outage = providers.get_supply_forecast(site, START, 48, "server_outage")
    assert sum(outage.p50[:6]) == 0 and outage.p50[6:] == s.p50[6:]
    tl = providers.get_supply_forecast(site, START, 48, "tenant_leaves")
    assert np.allclose(tl.p50, p50 * 0.7, atol=0.02)


def test_lansing_supply_is_hot_and_chelsea_is_lukewarm():
    assert min(providers.get_supply_forecast("lansing", START, 24).supply_temp_c) >= 45
    assert max(providers.get_supply_forecast("chelsea", START, 24).supply_temp_c) <= 35


def test_band_validation_rejects_bad_forecasts():
    hours = [START + timedelta(hours=i) for i in range(2)]
    with pytest.raises(pydantic.ValidationError):
        DemandForecast(building_id="x", hours=hours, p05=[2, 1], p50=[1, 1], p95=[3, 3])
    with pytest.raises(pydantic.ValidationError):
        DemandForecast(building_id="x", hours=hours, p05=[1], p50=[1, 1], p95=[1, 1])


# ------------------------------------------------------------------ confidence / Jev

def test_confidence_shape_and_stress_dip():
    bs = providers.get_buildings("chelsea")
    plan = heuristic_plan("chelsea", bs)
    normal = providers.get_confidence("chelsea", plan, _state("chelsea"), 100)
    vortex = providers.get_confidence("chelsea", plan, _state("chelsea", weather="polar_vortex"), 100)
    assert normal.n_futures == 100 and normal.horizon_hours == 48
    assert set(normal.p_each_party_ahead) == {p.id for p in load_site("chelsea").parties}
    assert set(normal.guarantee_prices) == set(plan.guaranteed_ids())
    assert sum(d.share for d in normal.top_uncertainty_drivers) == pytest.approx(1, abs=1e-3)
    assert vortex.p_all_warm < normal.p_all_warm
    assert vortex.expected_unmet_hours > normal.expected_unmet_hours


def test_confidence_is_deterministic():
    plan = heuristic_plan("lansing", providers.get_buildings("lansing"))
    a = providers.get_confidence("lansing", plan, _state("lansing"), 50)
    b = providers.get_confidence("lansing", plan, _state("lansing"), 50)
    assert a == b


def test_heuristic_plan_respects_capacity():
    plan = heuristic_plan("chelsea", providers.get_buildings("chelsea"))
    assert 5 <= len(plan.connected_ids()) < 40
    assert set(plan.guaranteed_ids()) == {"CH-14", "CH-17"}


def test_jev_mock():
    op = providers.get_jev_opinion(_state("chelsea"))
    assert op.available and 0 <= op.playbook_probability <= 1


def _jev(p):
    return JevOpinion(playbook="draw_storage", playbook_probability=0.9,
                      p_supply_meets_guarantees=p, latency_ms=50, available=True)


@pytest.mark.parametrize("p_mc,p_jev,expected", [
    (0.95, 0.93, "ACT"),
    (0.95, 0.72, "REVIEW"),     # disagreement > 0.20
    (0.95, 0.65, "ESCALATE"),   # escalate wins over disagreement
    (0.80, 0.82, "REVIEW"),     # gap case: neither ACT nor ESCALATE
    (0.91, 0.89, "REVIEW"),
    (0.60, 0.95, "ESCALATE"),
])
def test_verdict_precedence(p_mc, p_jev, expected):
    assert verdict(p_mc, _jev(p_jev)) == expected


def test_verdict_without_jev():
    off = _jev(0.99).model_copy(update={"available": False})
    assert verdict(0.95, off) == "ACT"
    assert verdict(0.80, None) == "REVIEW"
    assert verdict(0.50, off) == "ESCALATE"


# ------------------------------------------------------------------ registry / integration surface

def test_registry_providers_satisfy_protocols():
    assert isinstance(providers.BUILDINGS, providers.BuildingProvider)
    assert isinstance(providers.WEATHER, providers.WeatherProvider)
    assert isinstance(providers.DEMAND, providers.DemandProvider)
    assert isinstance(providers.SUPPLY, providers.SupplyProvider)
    assert isinstance(providers.CONFIDENCE, providers.ConfidenceProvider)
    assert isinstance(providers.JEV, providers.JevProvider)
    assert isinstance(providers.SIMULATOR, providers.FutureSimulator)
    assert all(c.is_mock for c in providers.model_cards().values())


def test_swapping_a_provider_only_touches_the_registry(monkeypatch):
    class FakeBuildings:
        def get_buildings(self, site):
            return providers.mocks.MockBuildingProvider().get_buildings(site)[:3]

        def model_card(self):
            raise NotImplementedError

    monkeypatch.setattr(providers, "BUILDINGS", FakeBuildings())
    assert len(providers.get_buildings("chelsea")) == 3


def test_no_engine_module_imports_mocks_directly():
    for path in (ROOT / "engine").glob("*.py"):
        if path.name in {"providers.py", "mocks.py"}:
            continue
        assert "mocks" not in path.read_text(), f"{path.name} must go through engine.providers"


# ------------------------------------------------------------------ TypeScript parity

def _ts_interfaces(text):
    out = {}
    for name, body in re.findall(r"export interface (\w+) \{(.*?)\n\}", text, re.S):
        out[name] = set(re.findall(r"^\s+(\w+)\??:", body, re.M))
    return out


def test_typescript_parity():
    ts = _ts_interfaces((ROOT / "web/src/types.ts").read_text())
    models = {n: m for n, m in vars(contracts).items()
              if isinstance(m, type) and issubclass(m, pydantic.BaseModel)
              and m.__module__ == contracts.__name__ and not n.startswith("_")}
    assert set(models) == set(ts), f"model sets differ: {set(models) ^ set(ts)}"
    for name, model in models.items():
        assert set(model.model_fields) == ts[name], f"{name}: {set(model.model_fields) ^ ts[name]}"
