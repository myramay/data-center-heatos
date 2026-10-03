import warnings
from datetime import datetime

import pytest

from engine.autopilot import MPCPolicy, compare_autopilots
from engine.sim import Simulation

WINTER = datetime(2026, 1, 12)
warnings.filterwarnings("ignore", category=UserWarning)


def test_mpc_beats_rules_on_price_spike():
    r = compare_autopilots("chelsea", WINTER, 72, "price_spike")
    assert r["delta_mpc_minus_rules"]["cost_incl_carbon_usd"] < 0
    assert r["mpc"]["hours_with_backup"] > r["rules"]["hours_with_backup"], "switches to boilers while power is x3"


@pytest.mark.parametrize("scenario", [None, "polar_vortex", "server_outage"])
def test_mpc_never_meaningfully_worse(scenario):
    r = compare_autopilots("chelsea", WINTER, 72, scenario)
    assert r["mpc"]["cost_incl_carbon_usd"] <= r["rules"]["cost_incl_carbon_usd"] * 1.005


def test_mpc_solves_optimally_and_balances():
    pol = MPCPolicy()
    sim = Simulation("lansing", start=WINTER, hours=48, autopilot=pol)
    r = sim.run()
    assert pol.last_status == "optimal"
    assert r.flows.balance_error.max() <= 1e-3


def test_mpc_with_forecast_scenarios():
    pol = MPCPolicy(horizon=24, demand_scenarios=(0.85, 1.0, 1.15))
    sim = Simulation("chelsea", start=WINTER, hours=12, autopilot=pol)
    sim.run()
    assert pol.last_status == "optimal" and len(pol.solve_ms) == 12


def test_toggle_mid_run():
    sim = Simulation("chelsea", start=WINTER, hours=24)
    sim.run(6)
    sim.set_policy("mpc")
    sim.run()
    assert sim.policy.name == "mpc" and sim.results().summary["hours"] == 24
