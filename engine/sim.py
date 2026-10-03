"""Simulation orchestrator: site -> plan -> hour-by-hour autopilot + physics.

    sim = Simulation("chelsea", start=datetime(2026, 1, 12), hours=8760)
    result = sim.run()            # fast path, whole horizon
    # or, for streaming:
    while not sim.done:
        sim.step(); frame = sim.frame()

Deterministic: same arguments, same numbers.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from engine import providers
from engine.autopilot import Policy, make_policy
from engine.config import load_site
from engine.contracts import Plan, SimState, SiteId, SupplyScenario, WeatherScenario
from engine.physics import (
    BALANCE_TOLERANCE, EnergyBalanceError, Flows, HourDispatch, HourlyInputs, build_network,
    building_flows, check_balance, derive, dispatch_hour,
)
from engine.recommend import quick_plan
from engine.scenarios import Narrator, apply_scenario

DEFAULT_START = datetime(2026, 1, 12)

# Live runs by id, so Monte Carlo futures can branch from the live state
# (including any stress tests already applied).
LIVE_RUNS: "weakref.WeakValueDictionary[str, Simulation]" = weakref.WeakValueDictionary()

# Hot-water share of annual heat by use type (sizes Lansing's booster heat pumps).
DHW_SHARE = {
    "residential": 0.25, "public_housing": 0.28, "office": 0.08, "retail": 0.05, "food": 0.40,
    "clinic": 0.20, "school": 0.10, "greenhouse": 0.0, "aquaculture": 0.60, "home": 0.20,
}


@dataclass
class RunResult:
    site: SiteId
    times: list
    ids: list[str]
    flows: Flows
    supply_kw: np.ndarray
    dc_used_kw: np.ndarray
    dc_fallback_kw: np.ndarray
    cooling_in_kw: np.ndarray
    cooling_sold_kw: np.ndarray
    storage_in_kw: np.ndarray       # (H, U)
    storage_out_kw: np.ndarray
    soc_kwh: np.ndarray
    shift_kw: np.ndarray
    steam_hp_on: np.ndarray
    elec_usd_per_mwh: np.ndarray
    summary: dict


class Simulation:
    def __init__(self, site: SiteId, start: datetime = DEFAULT_START, hours: int = 168,
                 plan: Plan | None = None, autopilot: str | Policy = "rules",
                 weather_scenario: WeatherScenario = "typical", supply_scenario: SupplyScenario = "base",
                 seed: int = 0, run_id: str | None = None, narrate: bool = True):
        self.site = site
        self.run_id = run_id
        self.cfg = load_site(site)
        self.seed = seed
        self.weather_scenario = weather_scenario
        self.supply_scenario = supply_scenario
        self.all_buildings = providers.get_buildings(site)
        self.plan = plan or quick_plan(site, self.all_buildings)
        self.net = build_network(self.cfg, self.all_buildings, self.plan, DHW_SHARE)
        self.policy: Policy = make_policy(autopilot) if isinstance(autopilot, str) else autopilot
        self.inp = self._load_inputs(start, hours)
        self.der = derive(self.net, self.inp)
        self.active_scenarios: list[str] = []
        self.scenario_ends: dict[str, int] = {}
        self.events: list[dict] = []
        self.narrator = Narrator() if narrate else None
        self.reset()
        if run_id:
            LIVE_RUNS[run_id] = self

    # ------------------------------------------------------------------ setup

    def _load_inputs(self, start: datetime, hours: int) -> HourlyInputs:
        site = self.site
        weather = providers.get_weather(site, start, hours, self.weather_scenario)
        fc = providers.get_demand_forecast(site, self.net.ids, start, hours, self.weather_scenario)
        demand = np.array([f.p50 for f in fc]).T if fc else np.zeros((hours, 0))
        supply = providers.get_supply_forecast(site, start, hours, self.supply_scenario)
        return HourlyInputs(
            times=weather.hours, t_out_c=np.array(weather.t_out_c), demand_kw=demand,
            supply_kw=np.array(supply.p50), supply_temp_c=np.array(supply.supply_temp_c),
            elec_usd_per_mwh=np.full(hours, self.cfg.prices.electricity_usd_per_mwh.value),
            fuel_price_mult=np.ones(hours), flex_available=np.ones(hours))

    def reset(self, soc_kwh: list[float] | None = None) -> None:
        self.h = 0
        self.soc_kwh = list(soc_kwh) if soc_kwh is not None else [u.initial_kwh for u in self.net.storages]
        self._soc_start = list(self.soc_kwh)
        self.flex_bank_kwh = 0.0
        self._rec: list[HourDispatch] = []

    def refresh(self) -> None:
        """Re-derive physics after scenarios edit self.inp."""
        self.der = derive(self.net, self.inp)

    def set_policy(self, autopilot: str | Policy) -> None:
        self.policy = make_policy(autopilot) if isinstance(autopilot, str) else autopilot

    @property
    def hours(self) -> int:
        return self.inp.hours

    @property
    def done(self) -> bool:
        return self.h >= self.hours

    # ------------------------------------------------------------------ stepping

    def step(self) -> HourDispatch:
        if self.done:
            raise StopIteration("simulation horizon exhausted")
        h = self.h
        decision = self.policy.decide(self, h)
        r = dispatch_hour(self.net, self.der.send_groups[h], float(self.der.cooling_total_kw[h]),
                          float(self.inp.supply_kw[h]), self.soc_kwh, self.flex_bank_kwh, decision,
                          flex_kw=self.net.flex_kw * float(self.inp.flex_available[h]))
        self._rec.append(r)
        self.soc_kwh = r.soc_kwh
        self.flex_bank_kwh = r.flex_bank_kwh
        if self.narrator:
            self.events.extend(self.narrator.observe(self, h, r))
        for name, end in list(self.scenario_ends.items()):
            if h + 1 >= end:
                del self.scenario_ends[name]
                self.active_scenarios.remove(name)
                self.events.append(self.event(h, "recovery", "ok", f"Stress test over: {name.replace('_', ' ')}"))
        self.h += 1
        return r

    # ------------------------------------------------------------------ scenarios

    def apply_scenario(self, name: str) -> list[dict]:
        """Fire a stress test from the current hour; returns its opening events."""
        events = apply_scenario(self, name)
        self.events.extend(events)
        return events

    def event(self, h: int, kind: str, severity: str, text: str) -> dict:
        h = min(max(h, 0), self.hours - 1)
        return {"hour_index": h, "time": self.inp.times[h].isoformat(), "kind": kind,
                "severity": severity, "text": text}

    def window_clone(self, h0: int, hours: int, soc_kwh: list[float] | None = None) -> "Simulation":
        """Independent copy of hours [h0, h0+hours) of this run's inputs, starting
        from the given (default: current) storage state. Used for Monte Carlo futures."""
        c = object.__new__(Simulation)
        for k in ("site", "cfg", "seed", "weather_scenario", "supply_scenario", "all_buildings", "plan", "net", "policy"):
            setattr(c, k, getattr(self, k))
        sl = slice(h0, min(h0 + hours, self.hours))
        i = self.inp
        c.inp = HourlyInputs(
            times=i.times[sl], t_out_c=i.t_out_c[sl].copy(), demand_kw=i.demand_kw[sl].copy(),
            supply_kw=i.supply_kw[sl].copy(), supply_temp_c=i.supply_temp_c[sl].copy(),
            elec_usd_per_mwh=i.elec_usd_per_mwh[sl].copy(), fuel_price_mult=i.fuel_price_mult[sl].copy(),
            flex_available=i.flex_available[sl].copy())
        c.der = derive(c.net, c.inp)
        c.run_id, c.narrator, c.events = None, None, []
        c.active_scenarios, c.scenario_ends = list(self.active_scenarios), {}
        c.reset(self.soc_kwh if soc_kwh is None else soc_kwh)
        c.flex_bank_kwh = self.flex_bank_kwh
        return c

    def run(self, hours: int | None = None) -> RunResult:
        stop = self.hours if hours is None else min(self.h + hours, self.hours)
        while self.h < stop:
            self.step()
        return self.results()

    # ------------------------------------------------------------------ outputs

    def _flows(self, sl: slice) -> Flows:
        rec = self._rec[sl]
        flows = building_flows(
            self.net, self.inp, self.der, sl,
            served=[r.served_send_kw for r in rec],
            steam_on=[r.steam_hp_on for r in rec], dc_used=[r.dc_used_kw for r in rec],
            cooling_in=[r.cooling_in_kw for r in rec],
            storage_in=np.array([r.storage_in_kw for r in rec]).reshape(len(rec), -1),
            storage_out=np.array([r.storage_out_kw for r in rec]).reshape(len(rec), -1))
        check_balance(flows, first_hour=sl.start or 0)
        return flows

    def results(self) -> RunResult:
        n = self.h
        if n == 0:
            raise ValueError("nothing simulated yet")
        sl = slice(0, n)
        flows = self._flows(sl)
        rec = self._rec
        arr = lambda attr: np.array([getattr(r, attr) for r in rec])
        storage_in, storage_out = arr("storage_in_kw").reshape(n, -1), arr("storage_out_kw").reshape(n, -1)
        soc, loss = arr("soc_kwh").reshape(n, -1), arr("storage_loss_kw").reshape(n, -1)
        self._check_storage(soc, storage_in, storage_out, loss)
        ccop = self.cfg.loop.cooling_cop.value if self.cfg.loop.cooling_cop else 6.0
        cooling_in = arr("cooling_in_kw")
        res = RunResult(
            site=self.site, times=self.inp.times[:n], ids=self.net.ids, flows=flows,
            supply_kw=arr("supply_kw"), dc_used_kw=arr("dc_used_kw"), dc_fallback_kw=arr("dc_fallback_kw"),
            cooling_in_kw=cooling_in, cooling_sold_kw=cooling_in / (1 + 1 / ccop),
            storage_in_kw=storage_in, storage_out_kw=storage_out, soc_kwh=soc,
            shift_kw=arr("shift_kw"), steam_hp_on=arr("steam_hp_on"),
            elec_usd_per_mwh=self.inp.elec_usd_per_mwh[:n], summary={})
        res.summary = self._summarize(res, sl)
        return res

    def _check_storage(self, soc, s_in, s_out, loss) -> None:
        start = np.array(self._soc_start)
        prev = np.vstack([start, soc[:-1]]) if len(soc) else soc
        err = np.abs(prev + s_in - s_out - loss - soc)
        if err.size and err.max() > BALANCE_TOLERANCE * max(1.0, start.max()):
            raise EnergyBalanceError(f"storage balance off by {err.max():.3f} kWh")
        for i, u in enumerate(self.net.storages):
            # standing losses may let an idle store drift below its floor; discharging below it may not
            drained_below = (s_out[:, i] > 1e-6) & (prev[:, i] - s_out[:, i] < u.min_kwh - 1e-6)
            if drained_below.any() or (soc[:, i] < -1e-6).any() or (soc[:, i] > u.capacity_kwh + 1e-6).any():
                raise EnergyBalanceError(f"storage {u.id} left its state-of-charge limits")
            if (s_in[:, i] > u.max_charge_kw + 1e-6).any() or (s_out[:, i] > u.max_discharge_kw + 1e-6).any():
                raise EnergyBalanceError(f"storage {u.id} exceeded its rate limits")

    def _summarize(self, r: RunResult, sl: slice) -> dict:
        cfg, net, f = self.cfg, self.net, r.flows
        mwh = lambda kw: float(np.sum(kw) / 1000)
        need = self.inp.demand_kw[sl]
        grid = cfg.emissions.grid_t_per_mwh.value
        price = r.elec_usd_per_mwh
        elec_kw = f.hp_elec_kw.sum(1) + f.pump_kw
        fuel_mult = self.inp.fuel_price_mult[sl][:, None]
        network_t = mwh(elec_kw) * grid + float(np.sum(f.backup_fuel_kw * net.fuel_t_per_mwh) / 1000)
        counterfactual_t = float(np.sum(need / net.backup_eff * net.fuel_t_per_mwh) / 1000)
        chiller_avoided_mwh = mwh(r.dc_used_kw) / cfg.data_center.chiller_cop.value
        missed = f.backup_kw > 0.01 * np.maximum(need, 1e-9)
        carbon_price = cfg.policy["carbon_price_usd_per_t"].value if "carbon_price_usd_per_t" in cfg.policy else 0.0
        return {
            "hours": int(len(r.supply_kw)),
            "connected_buildings": net.n,
            "heat_demand_mwh": mwh(need),
            "network_heat_mwh": mwh(f.delivered_kw),
            "backup_heat_mwh": mwh(f.backup_kw),
            "network_share": mwh(f.delivered_kw) / max(mwh(need), 1e-9),
            "unmet_building_hours": int(missed.sum()),
            "hours_with_backup": int(missed.any(axis=1).sum()),
            "dc_offered_mwh": mwh(r.supply_kw),
            "dc_used_mwh": mwh(r.dc_used_kw),
            "dc_utilization": mwh(r.dc_used_kw) / max(mwh(r.supply_kw), 1e-9),
            "hp_electricity_mwh": mwh(f.hp_elec_kw),
            "pump_electricity_mwh": mwh(f.pump_kw),
            "pipe_loss_mwh": mwh(f.pipe_loss_kw),
            "cooling_sold_mwh": mwh(r.cooling_sold_kw),
            "storage_discharged_mwh": mwh(r.storage_out_kw),
            "electricity_cost_usd": float(np.sum(elec_kw * price) / 1000),
            "backup_fuel_cost_usd": float(np.sum(f.backup_fuel_kw * net.fuel_usd_per_mwh * fuel_mult) / 1000),
            "counterfactual_heat_cost_usd": float(np.sum(need / net.backup_eff * net.fuel_usd_per_mwh * fuel_mult) / 1000),
            "network_emissions_t": network_t,
            "counterfactual_emissions_t": counterfactual_t,
            "chiller_avoided_t": chiller_avoided_mwh * grid,
            "net_co2_avoided_t": counterfactual_t - network_t + chiller_avoided_mwh * grid,
            "carbon_cost_usd": network_t * carbon_price,
            "freed_mw_peak": float(np.max(r.dc_used_kw) / cfg.data_center.chiller_cop.value / 1000),
            "max_balance_error": float(f.balance_error.max()),
        }

    # ------------------------------------------------------------------ live views

    def state(self) -> SimState:
        h = min(self.h, self.hours - 1)
        backup = float(self._flows(slice(self.h - 1, self.h)).backup_kw.sum()) if self._rec else 0.0
        return SimState(
            site=self.site, time=self.inp.times[h], hour_index=h, t_out_c=float(self.inp.t_out_c[h]),
            supply_kw=float(self.inp.supply_kw[h]), demand_kw=float(self.inp.demand_kw[h].sum()),
            storage_soc_mwh=sum(self.soc_kwh) / 1000,
            storage_capacity_mwh=sum(u.capacity_kwh for u in self.net.storages) / 1000,
            unmet_kw=backup, backup_kw=backup,      # network shortfall, covered by existing boilers
            electricity_price_usd_per_mwh=float(self.inp.elec_usd_per_mwh[h]),
            flexible_compute_available=self.net.flex_kw > 0 and self.flex_bank_kwh > 0,
            active_scenarios=list(self.active_scenarios), weather_scenario=self.weather_scenario,
            supply_scenario=self.supply_scenario, run_id=self.run_id)

    def frame(self) -> dict:
        """Snapshot of the last simulated hour (WebSocket payload, formalized in step 4)."""
        if not self._rec:
            raise ValueError("call step() first")
        h = self.h - 1
        r = self._rec[h]
        f = self._flows(slice(h, h + 1))
        by_idx = {bid: i for i, bid in enumerate(self.net.ids)}
        buildings = []
        for b in self.all_buildings:
            i = by_idx.get(b.id)
            if i is None:
                buildings.append({"id": b.id, "delivered_kw": 0.0, "unmet_kw": 0.0, "temp_c": None,
                                  "mode": "off", "backup_on": False})
                continue
            frac, need = float(f.served_frac[0, i]), float(self.inp.demand_kw[h, i])
            mode = ("backup" if frac < 0.01 and need > 0 else "mixed" if frac < 0.99
                    else "storage" if sum(r.storage_out_kw) > 0 else "network")
            buildings.append({
                "id": b.id, "delivered_kw": round(float(f.delivered_kw[0, i]), 1),
                "unmet_kw": round(float(f.backup_kw[0, i]), 1), "temp_c": b.required_supply_temp_c,
                "mode": mode, "backup_on": bool(f.backup_kw[0, i] > 0.01 * max(need, 1e-9))})
        cop = self.cfg.data_center.chiller_cop.value
        return {
            "time": self.inp.times[h].isoformat(), "hour_index": h,
            "weather": {"t_out_c": round(float(self.inp.t_out_c[h]), 1)},
            "buildings": buildings,
            "data_center": {"offered_kw": round(r.supply_kw, 1), "used_kw": round(r.dc_used_kw, 1),
                            "fallback_kw": round(r.dc_fallback_kw, 1),
                            "temp_c": round(float(self.inp.supply_temp_c[h]), 1),
                            "freed_mw": round(r.dc_used_kw / cop / 1000, 3), "shift_kw": round(r.shift_kw, 1)},
            "storage": [{"id": u.id, "type": u.type, "soc_mwh": round(s / 1000, 2),
                         "soc_frac": round(s / u.capacity_kwh, 4), "in_kw": round(i_, 1), "out_kw": round(o, 1)}
                        for u, s, i_, o in zip(self.net.storages, r.soc_kwh, r.storage_in_kw, r.storage_out_kw)],
            "loop": {"flow_m3h": round(float(f.flow_m3h[0]), 1),
                     "supply_temp_c": round(float(self.der.loop_temp_c[h]), 1),
                     "return_temp_c": round(float(f.return_temp_c[0]), 1),
                     "pump_kw": round(float(f.pump_kw[0]), 1)},
            "cooling_sold_kw": round(r.cooling_in_kw / (1 + 1 / (self.cfg.loop.cooling_cop.value if self.cfg.loop.cooling_cop else 6.0)), 1),
            "steam_hp_on": r.steam_hp_on,
            "autopilot": self.policy.name,
            "events": [e for e in self.events if e.get("hour_index") == h],
        }
