"""The engine's FutureSimulator: plays out one sampled future with the real
physics and ledger. The confidence provider (mock now, ML team's Monte Carlo
later) calls this; it never re-implements physics or money.

Futures branch from the live run when state.run_id names one, so stress tests
already in effect carry into every future.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

from engine.contracts import FutureInputs, FutureOutcome, Plan, SimState
from engine.ledger import DealTerms, compute_ledger
from engine.physics import derive


class EngineFutureSimulator:
    def __init__(self) -> None:
        self._bases: dict[tuple, object] = {}

    def _base(self, plan: Plan, state: SimState):
        from engine.sim import LIVE_RUNS, Simulation   # lazy: engine.sim imports engine.providers

        live = LIVE_RUNS.get(state.run_id) if state.run_id else None
        if live is not None and live.plan == plan:
            return live.window_clone(state.hour_index, state.horizon_hours)
        key = (plan.model_dump_json(), state.site, state.time, state.horizon_hours,
               state.weather_scenario, state.supply_scenario)
        if key not in self._bases:
            if len(self._bases) > 8:
                self._bases.clear()
            self._bases[key] = Simulation(state.site, start=state.time, hours=state.horizon_hours, plan=plan,
                                          weather_scenario=state.weather_scenario,
                                          supply_scenario=state.supply_scenario, narrate=False)
        base = self._bases[key]
        cap = sum(u.capacity_kwh for u in base.net.storages)
        fill = min(state.storage_soc_mwh * 1000 / cap, 1.0) if cap else 0.0
        return base.window_clone(0, state.horizon_hours, [u.capacity_kwh * fill for u in base.net.storages])

    @staticmethod
    def _jitter(sim, f: FutureInputs) -> None:
        rng = np.random.default_rng(f.seed)
        per_building = rng.normal(1.0, 0.05, sim.net.n)
        sim.inp.demand_kw *= f.demand_mult * per_building[None, :]
        sim.inp.supply_kw *= f.supply_mult
        sim.inp.elec_usd_per_mwh *= f.electricity_price_mult
        sim.inp.fuel_price_mult *= f.fuel_price_mult
        sim.der = derive(sim.net, sim.inp, eta=f.cop_eta)

    def play(self, plan: Plan, state: SimState, f: FutureInputs):
        """(simulation, result) for one future; used by deal search and pricing."""
        sim = self._base(plan, state)
        self._jitter(sim, f)
        return sim, sim.run()

    def simulate_futures(self, plan: Plan, state: SimState, futures: Sequence[FutureInputs],
                         terms: DealTerms | None = None, premiums: dict[str, float] | None = None) -> list[FutureOutcome]:
        out = []
        for f in futures:
            sim, r = self.play(plan, state, f)
            led = compute_ledger(sim, r, terms, premiums)
            s = r.summary
            missed = r.flows.backup_kw > 0.01 * np.maximum(sim.inp.demand_kw[:len(r.supply_kw)], 1e-9)
            carbon = sim.cfg.policy["carbon_price_usd_per_t"].value if "carbon_price_usd_per_t" in sim.cfg.policy else 0.0
            out.append(FutureOutcome(
                unmet_hours_by_building={bid: int(missed[:, k].sum()) for k, bid in enumerate(sim.net.ids)},
                unmet_mwh=s["backup_heat_mwh"],
                refunds_usd_by_building=led.refunds_by_building,
                party_net_usd={p: r_.window_net_usd for p, r_ in led.parties.items()},
                system_cost_usd=s["electricity_cost_usd"] + s["backup_fuel_cost_usd"] + s["network_emissions_t"] * carbon))
        return out

    def simulate_future(self, plan: Plan, state: SimState, inputs: FutureInputs) -> FutureOutcome:
        return self.simulate_futures(plan, state, [inputs])[0]
