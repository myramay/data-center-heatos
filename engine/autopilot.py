"""Autopilot policies. Each returns a physics.Decision for the current hour.

OFF ("rules"): data center heat first, then storage (short-term tanks before
seasonal storage), then each building's existing backup; surplus charges
storage; at Lansing, compute follows heat (deferred when heat would be wasted,
run when it is short).

ON ("mpc"): every simulated hour, a 48 h linear program (CVXPY + HiGHS)
chooses how much heat to serve, storage charge/discharge, steam heat pumps
on/off (relaxed), and compute shifting, minimizing electricity + backup fuel +
carbon (at the site carbon price) + 10,000 $/MWh of unmet guaranteed heat.
The first hour is applied, then it re-plans. Forecasts are the run's p50
inputs; a list of demand scenarios can be passed and the scenario-average cost
is minimized with a shared first-hour decision.
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


class MPCPolicy:
    """48 h receding-horizon LP. See module docstring."""

    name = "mpc"
    UNMET_GUARANTEE_USD_PER_MWH = 10_000.0

    # ML_TEAM_INTEGRATION: pass demand scenario multipliers (e.g. from p05/p50/p95
    # or Monte Carlo draws) to minimize the scenario-average cost.
    def __init__(self, horizon: int = 48, demand_scenarios: tuple[float, ...] = (1.0,)):
        self.horizon = horizon
        self.scen = tuple(demand_scenarios)
        self._problems: dict[tuple, tuple] = {}
        self.last_status = ""
        self.solve_ms: list[float] = []

    # ------------------------------------------------------------------ problem template

    def _build(self, T: int, U: int, flex: bool):
        import cvxpy as cp

        K = len(self.scen)
        P = {k: cp.Parameter(T, nonneg=True, name=k) for k in
             ("S", "C", "N", "M", "G", "kN", "kM", "bN", "bM", "chill")}
        P["soc0"] = cp.Parameter(U, nonneg=True, name="soc0")
        P["bank0"] = cp.Parameter(nonneg=True, name="bank0")
        P["term"] = cp.Parameter(nonneg=True, name="term")
        P["dc_room"] = cp.Parameter(T, nonneg=True, name="dc_room")
        P["flex_cap"] = cp.Parameter(T, nonneg=True, name="flex_cap")
        cons, cost = [], 0
        first = {}
        for k, mult in enumerate(self.scen):
            x, z = cp.Variable(T, nonneg=True), cp.Variable(T, nonneg=True)
            c, d = cp.Variable((U, T), nonneg=True), cp.Variable((U, T), nonneg=True)
            soc = cp.Variable((U, T + 1))
            slack = cp.Variable(T, nonneg=True)
            w = cp.Variable(T) if flex else None
            bank = cp.Variable(T + 1) if flex else None
            shift = w if flex else 0
            cons += [x <= mult * P["N"], z <= mult * P["M"],
                     x + z + cp.sum(c, axis=0) <= P["S"] + shift + P["C"] + cp.sum(d, axis=0),
                     x + slack >= mult * P["G"], soc[:, 0] == P["soc0"]]
            for u, spec in enumerate(self._specs):
                cons += [soc[u, 1:] == (1 - spec.loss_per_hour) * (soc[u, :-1] + c[u] - d[u]),
                         soc[u, 1:] >= spec.min_kwh * (1 - spec.loss_per_hour) - 1e-3, soc[u, 1:] <= spec.capacity_kwh,
                         c[u] <= spec.max_charge_kw, d[u] <= spec.max_discharge_kw]
            if flex:
                cons += [bank[0] == P["bank0"], bank[1:] == bank[:-1] - w, bank >= 0, bank <= self._flex * 24,
                         w <= P["flex_cap"], -w <= P["flex_cap"], w <= P["dc_room"], -w <= P["S"]]
            dc_used = x + z + cp.sum(c, axis=0) - cp.sum(d, axis=0)
            # backup cost of unserved heat = bN @ (N - x); the bN @ N part is constant and dropped (keeps DPP)
            cost_k = (P["kN"] @ x + P["kM"] @ z - P["bN"] @ x - P["bM"] @ z
                      - P["chill"] @ dc_used
                      + self.UNMET_GUARANTEE_USD_PER_MWH / 1000 * cp.sum(slack)
                      + 1e-4 * (cp.sum(c) + cp.sum(d))
                      - P["term"] * cp.sum(soc[:, T]))
            cost += cost_k / K
            first[k] = (x, z, c, d, w)
        for k in range(1, K):       # non-anticipativity: one decision for the first hour
            for a, b in zip(first[0][:4], first[k][:4]):
                cons.append(a[..., 0] == b[..., 0])
            if flex:
                cons.append(first[0][4][0] == first[k][4][0])
        prob = cp.Problem(cp.Minimize(cost), cons)
        return prob, P, first[0]

    def decide(self, sim: "Simulation", h: int) -> Decision:
        import time

        import cvxpy as cp

        net, der, inp, cfg = sim.net, sim.der, sim.inp, sim.cfg
        T = min(self.horizon, sim.hours - h)
        U = len(net.storages)
        flex = net.flex_kw > 0
        self._specs, self._flex = net.storages, net.flex_kw
        key = (T, U, flex, id(net))
        if key not in self._problems:
            self._problems[key] = self._build(T, U, flex)
        prob, P, (x, z, c, d, w) = self._problems[key]

        sl = slice(h, h + T)
        need = inp.demand_kw[sl]
        send = der.send_kw[sl]
        steam = net.steam_hp
        carbon = cfg.policy["carbon_price_usd_per_t"].value if "carbon_price_usd_per_t" in cfg.policy else 0.0
        grid = cfg.emissions.grid_t_per_mwh.value
        elec_cost = (inp.elec_usd_per_mwh[sl] + carbon * grid) / 1000                      # $/kWh incl. carbon
        cost_b = (np.array([b.current_heat_cost_usd_per_mwh for b in net.buildings])[None, :] * inp.fuel_price_mult[sl][:, None]
                  + carbon * net.fuel_t_per_mwh[None, :] / net.backup_eff[None, :]) / 1000      # $/kWh heat on backup

        def per_send(mask, coef):
            s_ = send[:, mask].sum(1)
            v = (need[:, mask] * coef[:, mask]).sum(1)
            return np.divide(v, s_, out=np.zeros(T), where=s_ > 1e-9), s_

        elec_per = need * der.elec_per_kw[sl]
        kN, N = per_send(~steam, elec_per * elec_cost[:, None] / np.maximum(need, 1e-9))
        kM, M = per_send(steam, elec_per * elec_cost[:, None] / np.maximum(need, 1e-9))
        bN, _ = per_send(~steam, cost_b)
        bM, _ = per_send(steam, cost_b)
        G = send[:, net.guaranteed & ~steam].sum(1)

        P["S"].value = np.maximum(inp.supply_kw[sl], 0)
        P["C"].value = np.maximum(der.cooling_total_kw[sl], 0)
        P["N"].value, P["M"].value, P["G"].value = N, M, G
        P["kN"].value, P["kM"].value = np.maximum(kN, 0), np.maximum(kM, 0)
        P["bN"].value, P["bM"].value = np.maximum(bN, 0), np.maximum(bM, 0)
        P["chill"].value = elec_cost / cfg.data_center.chiller_cop.value
        P["soc0"].value = np.maximum(np.array(sim.soc_kwh, float), 0)
        P["bank0"].value = max(sim.flex_bank_kwh, 0.0)
        # stored heat left at the horizon is worth half the cheapest saving it could make later
        P["term"].value = 0.5 * float(np.min(np.maximum(bN - kN, 0))) if N.any() else 0.0
        P["dc_room"].value = np.maximum(net.dc_cap_kw - inp.supply_kw[sl], 0)
        P["flex_cap"].value = net.flex_kw * np.clip(inp.flex_available[sl], 0, 1)

        t0 = time.perf_counter()
        try:
            prob.solve(solver=cp.HIGHS)
        except Exception:                                            # pragma: no cover - solver failure
            self.last_status = "solver_error"
            return RulesPolicy().decide(sim, h)
        self.solve_ms.append((time.perf_counter() - t0) * 1000)
        self.last_status = prob.status
        if prob.status not in ("optimal", "optimal_inaccurate") or x.value is None:
            return RulesPolicy().decide(sim, h)

        flows = [float(c.value[u, 0] - d.value[u, 0]) for u in range(U)]
        steam_on = bool(M[0] <= 1e-6 or z.value[0] >= 0.5 * M[0])
        # Serve less than demand only when heat pumps cost more than boilers at the margin
        # (e.g. a price spike). The LP averages backup costs across buildings while physics
        # cuts by priority, so during a plain shortage never cut service or bank heat: discharge
        # at least as hard as the rules would.
        economic = kN[0] > bN[0]
        cap = float(x.value[0]) if economic and x.value[0] < N[0] - 1.0 else None
        short = N[0] + (M[0] if steam_on else 0.0) > inp.supply_kw[h] + der.cooling_total_kw[h]
        if cap is None and short:
            rules = RulesPolicy().decide(sim, h).storage_kw
            flows = [min(f, r) for f, r in zip(flows, rules)]
        return Decision(storage_kw=flows, shift_kw=float(w.value[0]) if flex else 0.0,
                        steam_hp_on=steam_on, serve_cap_kw=cap)


POLICIES: dict[str, type] = {"rules": RulesPolicy, "mpc": MPCPolicy}


def compare_autopilots(site: str, start, hours: int = 168, scenario: str | None = None, at: int = 24) -> dict:
    """Run the same window with rules and MPC; returns both summaries and the deltas."""
    from engine.sim import Simulation

    out = {}
    for name in ("rules", "mpc"):
        sim = Simulation(site, start=start, hours=hours, autopilot=name, narrate=False)
        if scenario:
            sim.run(at)
            sim.apply_scenario(scenario)
        s = sim.run().summary
        carbon = sim.cfg.policy["carbon_price_usd_per_t"].value if "carbon_price_usd_per_t" in sim.cfg.policy else 0.0
        s["operating_cost_usd"] = s["electricity_cost_usd"] + s["backup_fuel_cost_usd"]
        s["cost_incl_carbon_usd"] = s["operating_cost_usd"] + s["network_emissions_t"] * carbon
        out[name] = s
    keys = ("operating_cost_usd", "cost_incl_carbon_usd", "network_emissions_t", "hours_with_backup", "backup_heat_mwh")
    out["delta_mpc_minus_rules"] = {k: out["mpc"][k] - out["rules"][k] for k in keys}
    return out


def make_policy(name: str) -> Policy:
    if name not in POLICIES:
        raise ValueError(f"unknown autopilot policy {name!r}; available: {sorted(POLICIES)}")
    return POLICIES[name]()
