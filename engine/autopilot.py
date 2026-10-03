"""Autopilot policies. Each returns a physics.Decision for the current hour.

OFF ("rules"): data center heat first, then storage (short-term tanks before
seasonal storage), then each building's existing backup; surplus charges
storage; at Lansing, compute follows heat (deferred when heat would be wasted,
run when it is short).

ON ("mpc"): 48 h LP re-planned every hour; arrives in build step 7.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import numpy as np

from engine.physics import Decision

if TYPE_CHECKING:
    from engine.sim import Simulation


class Policy(Protocol):
    name: str

    def decide(self, sim: "Simulation", h: int) -> Decision: ...


class RulesPolicy:
    name = "rules"

    def decide(self, sim: "Simulation", h: int) -> Decision:
        net, der = sim.net, sim.der
        steam_on = True                            # naive: always run; MPC decides on price + carbon
        g = der.send_groups[h]
        need = g[0] + (g[1] if steam_on else 0.0)
        net_surplus = sim.inp.supply_kw[h] + der.cooling_total_kw[h] - need

        flows, shift = [], 0.0
        if net_surplus >= 0:
            left = net_surplus
            for u, soc in zip(net.storages, sim.soc_kwh):
                c = min(left, u.max_charge_kw, max(u.capacity_kwh - soc, 0.0))
                flows.append(c)
                left -= c
            if net.flex_kw and left > 0 and self._shortfall_ahead(sim, h):
                shift = -left                      # heat would be wasted and a cold spell is coming: defer compute
        else:
            short = -net_surplus
            for u, soc in zip(net.storages, sim.soc_kwh):
                x = min(short, u.max_discharge_kw, max(soc - u.min_kwh, 0.0))
                flows.append(-x)
                short -= x
            if net.flex_kw and short > 0:
                shift = short                      # run deferred compute now
        return Decision(storage_kw=flows, shift_kw=shift, steam_hp_on=steam_on)

    @staticmethod
    def _shortfall_ahead(sim: "Simulation", h: int, lookahead: int = 24) -> bool:
        sl = slice(h + 1, h + 1 + lookahead)
        deficit = np.maximum(sim.der.send_groups[sl].sum(axis=1) - sim.inp.supply_kw[sl]
                             - sim.der.cooling_total_kw[sl], 0.0)
        dis_kw = sum(u.max_discharge_kw for u in sim.net.storages)
        stored_kwh = sum(max(soc - u.min_kwh, 0.0) for u, soc in zip(sim.net.storages, sim.soc_kwh))
        return bool(deficit.max(initial=0.0) > dis_kw or deficit.sum() > stored_kwh)


POLICIES: dict[str, type] = {"rules": RulesPolicy}


def make_policy(name: str) -> Policy:
    if name not in POLICIES:
        raise ValueError(f"unknown autopilot policy {name!r}; available: {sorted(POLICIES)}")
    return POLICIES[name]()
