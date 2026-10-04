import time
from dataclasses import replace
from datetime import datetime

import numpy as np
import pytest

from engine import physics, providers
from engine.recommend import quick_plan
from engine.physics import Decision, EnergyBalanceError, cop
from engine.sim import Simulation

YEAR = datetime(2025, 1, 1)


@pytest.fixture(scope="module")
def chelsea_year():
    return Simulation("chelsea", start=YEAR, hours=8760).run()


@pytest.fixture(scope="module")
def lansing_year():
    return Simulation("lansing", start=YEAR, hours=8760).run()


# ------------------------------------------------------------------ physics primitives

def test_cop_values():
    assert cop(0.6, 75, 25) == pytest.approx(0.6 * 348.15 / 50, rel=1e-6)    # ~4.2
    assert 1.8 < cop(0.45, 120, 25) < 2.1                                      # steam heat pump
    assert cop(0.6, 50, 49.9) == physics.COP_CAP                              # capped at tiny lifts


# ------------------------------------------------------------------ acceptance: balance + speed

@pytest.mark.parametrize("fixture", ["chelsea_year", "lansing_year"])
def test_energy_balance_closes_every_hour(fixture, request):
    r = request.getfixturevalue(fixture)
    assert len(r.flows.balance_error) == 8760
    assert r.flows.balance_error.max() <= physics.BALANCE_TOLERANCE


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_year_runs_under_one_second(site):
    sim = Simulation(site, start=YEAR, hours=8760)       # data loading excluded
    t = time.perf_counter()
    sim.run()
    assert time.perf_counter() - t < 1.0


def test_balance_check_catches_a_broken_hour():
    sim = Simulation("chelsea", hours=24)
    sim.run()
    sim._rec[5].dc_used_kw += 500.0
    with pytest.raises(EnergyBalanceError, match="hour 5"):
        sim.results()


# ------------------------------------------------------------------ physical behaviour

def test_data_center_protection(chelsea_year):
    r = chelsea_year
    assert (r.dc_used_kw <= r.supply_kw + 1e-6).all()
    assert (r.dc_fallback_kw >= -1e-6).all()
    assert np.allclose(r.dc_used_kw + r.dc_fallback_kw, r.supply_kw)


def test_storage_limits_respected(chelsea_year):
    sim = Simulation("chelsea", start=YEAR, hours=10)
    for i, u in enumerate(sim.net.storages):
        assert chelsea_year.soc_kwh[:, i].max() <= u.capacity_kwh + 1e-6
        assert chelsea_year.storage_in_kw[:, i].max() <= u.max_charge_kw + 1e-6
        assert chelsea_year.storage_out_kw[:, i].max() <= u.max_discharge_kw + 1e-6


def test_guaranteed_buildings_served_first(chelsea_year):
    f = chelsea_year.flows.served_frac
    g = Simulation("chelsea", hours=1).net.guaranteed
    assert (f[:, g].min(axis=1) >= f[:, ~g].max(axis=1) - 1e-9).all() or not g.any()


def test_seasonality_and_summer_cooling(chelsea_year):
    s = chelsea_year.summary
    assert 0.5 < s["network_share"] <= 1.0
    assert s["cooling_sold_mwh"] > 0
    months = np.array([t.month for t in chelsea_year.times])
    backup = chelsea_year.flows.backup_kw.sum(1)
    assert backup[np.isin(months, [1, 2])].sum() > backup[np.isin(months, [6, 7, 8])].sum()


def test_net_co2_in_spec_sanity_range(chelsea_year):
    s = chelsea_year.summary
    per_mw = s["net_co2_avoided_t"] / 5.0          # 5 MW thermal site
    assert 600 < per_mw < 1300, per_mw             # spec: ~790-1,030 t per MW-yr at 60% utilization


def test_server_outage_draws_storage_then_backup():
    base = Simulation("chelsea", hours=24).run()
    out = Simulation("chelsea", hours=24, supply_scenario="server_outage").run()
    assert out.supply_kw[:6].sum() == 0
    assert out.storage_out_kw[:6].sum() > base.storage_out_kw[:6].sum()
    assert out.summary["backup_heat_mwh"] >= base.summary["backup_heat_mwh"]


def test_steam_hp_off_sends_steam_buildings_to_backup():
    class SteamOff:
        name = "steam_off"

        def decide(self, sim, h):
            return Decision(storage_kw=[0.0] * len(sim.net.storages), steam_hp_on=False)

    base = quick_plan("chelsea", providers.get_buildings("chelsea"))
    steam_ids = {b.id for b in providers.get_buildings("chelsea") if b.heating_system == "steam"}
    plan = base.model_copy(update={"items": [
        i.model_copy(update={"option": "steam_hp", "connect": True, "design_capacity_kw": 500.0})
        if i.building_id in steam_ids else i for i in base.items]})
    sim = Simulation("chelsea", hours=24, autopilot=SteamOff(), plan=plan)
    r = sim.run()
    st = sim.net.steam_hp
    assert st.any()
    assert np.allclose(r.flows.delivered_kw[:, st], 0.0)
    assert np.allclose(r.flows.backup_kw[:, st], sim.inp.demand_kw[:24, st])


def test_lansing_compute_follows_heat():
    sim = Simulation("lansing", hours=96)
    # small store + heat-short spell after day 2, so storage alone can't bridge it
    sim.net.storages = [replace(u, capacity_kwh=2000.0, min_kwh=100.0, max_charge_kw=500.0,
                                max_discharge_kw=500.0) for u in sim.net.storages]
    sim.inp.supply_kw[48:] *= 0.05
    sim.refresh()
    sim.reset(soc_kwh=[100.0])
    r = sim.run()
    assert r.shift_kw[:48].min() < 0, "defers compute ahead of the shortfall"
    assert r.shift_kw[48:].max() > 0, "runs deferred compute when heat is short"
    assert np.abs(r.shift_kw).max() <= sim.net.flex_kw + 1e-6


def test_chelsea_has_no_flexible_compute(chelsea_year):
    assert np.allclose(chelsea_year.shift_kw, 0.0)


# ------------------------------------------------------------------ orchestration

def test_step_mode_matches_batch_mode():
    a = Simulation("chelsea", hours=48)
    while not a.done:
        a.step()
    b = Simulation("chelsea", hours=48).run()
    assert a.results().summary == b.summary


def test_deterministic():
    assert Simulation("lansing", hours=72).run().summary == Simulation("lansing", hours=72).run().summary


def test_frame_and_state():
    sim = Simulation("chelsea", hours=12)
    sim.step()
    fr = sim.frame()
    assert len(fr["buildings"]) == sim.net.n
    assert {b["mode"] for b in fr["buildings"]} <= {"network", "storage", "mixed", "backup", "off"}
    assert fr["data_center"]["used_kw"] <= fr["data_center"]["offered_kw"] + 1e-6
    st = sim.state()
    assert st.hour_index == 1 and st.storage_capacity_mwh == pytest.approx(1530)
