"""The team's trained models behind the provider contracts (skipped when outputs/models is absent)."""

from datetime import datetime

import numpy as np
import pytest

from ml import team_data, team_ml

pytestmark = pytest.mark.skipif(not (team_ml.available() and team_data.available("chelsea") and team_data.available("lansing")),
                                reason="team model outputs / NOAA weather not present")


@pytest.fixture(scope="module")
def providers():
    b = team_data.TeamBuildingProvider()
    w = team_ml.TeamWeatherProvider()
    return b, w, team_ml.TeamDemandProvider(b, w), team_ml.TeamSupplyProvider()


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_weather_is_real_and_full(providers, site):
    _, w, _, _ = providers
    t = np.asarray(w.get_weather(site, datetime(2025, 1, 1), 8760).t_out_c)
    assert len(t) == 8760 and np.isfinite(t).all()
    assert -35 < t.min() < -5 and 25 < t.max() < 40


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_demand_keeps_annual_totals_and_reacts_to_cold(providers, site):
    b, _, d, _ = providers
    bs = [x for x in b.get_buildings(site) if d._type(x)][:5]
    fc = d.get_demand_forecast(site, [x.id for x in bs], datetime(2025, 1, 1), 8760)
    for x, f in zip(bs, fc):
        assert sum(f.p50) / 1000 == pytest.approx(x.annual_heat_mwh, rel=0.03)
        assert all(lo <= m <= hi for lo, m, hi in zip(f.p05, f.p50, f.p95))
    typ = d.get_demand_forecast(site, [bs[0].id], datetime(2025, 1, 10), 72)[0]
    pv = d.get_demand_forecast(site, [bs[0].id], datetime(2025, 1, 10), 72, "polar_vortex")[0]
    assert sum(pv.p50) > 1.1 * sum(typ.p50)


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_supply_scaled_to_config_when_asked(providers, site, monkeypatch):
    monkeypatch.setenv("HEATOS_SUPPLY_SCALE", "config")
    _, _, _, s = providers
    from engine.config import load_site
    dc = load_site(site).data_center
    cap = dc.capacity_mw_th.value * 1000 * dc.capture_fraction.value
    f = s.get_supply_forecast(site, datetime(2025, 1, 1), 8760)
    assert max(f.p50) == pytest.approx(cap, rel=0.01)
    out = s.get_supply_forecast(site, datetime(2025, 1, 1), 24, "server_outage")
    assert out.p50[:6] == [0.0] * 6


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_supply_uses_team_magnitude_by_default(providers, site, monkeypatch):
    monkeypatch.delenv("HEATOS_SUPPLY_SCALE", raising=False)
    _, _, _, s = providers
    f = s.get_supply_forecast(site, datetime(2025, 1, 1), 8760)
    assert np.mean(f.p50) / 1000 == pytest.approx(team_ml.team_supply_mw(site), rel=0.05)
