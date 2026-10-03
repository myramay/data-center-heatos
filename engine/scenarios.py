"""Stress tests and the live event narrator.

A stress test edits the running simulation's hourly inputs from "now" for N
hours (weather and demand come from the providers, so the ML team's models
decide how buildings respond), then re-derives the physics. The Narrator
watches each simulated hour and writes plain-language events for the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import numpy as np

from engine import providers
from engine.physics import served_fractions

if TYPE_CHECKING:
    from engine.physics import HourDispatch
    from engine.sim import Simulation


@dataclass(frozen=True)
class Scenario:
    name: str
    label: str
    sites: tuple[str, ...]
    hours: int | None            # None = rest of the run
    severity: str
    opening: str                 # first event text
    apply: Callable[["Simulation", int, int], None]


def _splice_weather(sim: "Simulation", h0: int, n: int, weather: str) -> None:
    """Replace weather and demand for [h0, h0+n) with the providers' scenario.

    ML_TEAM_INTEGRATION: cold-snap depth comes from the weather provider
    (GEV 1-in-50-year low once the Monte Carlo engine supplies it)."""
    start = sim.inp.times[h0]
    w = providers.get_weather(sim.site, start, n, weather)
    sim.inp.t_out_c[h0:h0 + n] = w.t_out_c
    if sim.net.n:
        fc = providers.get_demand_forecast(sim.site, sim.net.ids, start, n, weather)
        sim.inp.demand_kw[h0:h0 + n] = np.array([f.p50 for f in fc]).T


def _supply_mult(mult: float) -> Callable[["Simulation", int, int], None]:
    def apply(sim, h0, n):
        sim.inp.supply_kw[h0:h0 + n] *= mult
    return apply


def _server_outage(sim, h0, n):
    sim.inp.supply_kw[h0:h0 + n] = 0.0
    sim.inp.supply_temp_c[h0:h0 + n] = sim.cfg.data_center.supply_temp_c_min.value


def _price_spike(sim, h0, n):
    sim.inp.elec_usd_per_mwh[h0:h0 + n] *= 3.0


def _greenhouse_off(sim, h0, n):
    cols = [i for i, b in enumerate(sim.net.buildings) if b.use_type == "greenhouse"]
    sim.inp.demand_kw[h0:h0 + n, cols] *= 0.2


def _bitcoin_crash(sim, h0, n):
    share = sim.cfg.data_center.flexible_compute_share.value
    sim.inp.supply_kw[h0:h0 + n] *= 1.0 - share
    sim.inp.flex_available[h0:h0 + n] = 0.0


SCENARIOS: dict[str, Scenario] = {s.name: s for s in [
    Scenario("polar_vortex", "Polar vortex", ("chelsea",), 72, "alert",
             "Polar vortex: outdoor air falling toward -18 C for 72 h (1-in-50-year placeholder)",
             lambda sim, h0, n: _splice_weather(sim, h0, n, "polar_vortex")),
    Scenario("tenant_leaves", "Tenant leaves", ("chelsea",), None, "warn",
             "Major tenant leaves 111 8th Ave: recoverable heat down 30% for the rest of the run",
             _supply_mult(0.7)),
    Scenario("server_outage", "Server outage", ("chelsea", "lansing"), 6, "alert",
             "Server outage: data center heat output at zero for 6 h", _server_outage),
    Scenario("heat_wave", "Heat wave", ("chelsea",), 120, "warn",
             "Heat wave: five days above 32 C; heating falls, offices push cooling load into the loop",
             lambda sim, h0, n: _splice_weather(sim, h0, n, "heat_wave")),
    Scenario("price_spike", "Price spike", ("chelsea", "lansing"), 24, "warn",
             "Electricity price spike: x3 for 24 h", _price_spike),
    Scenario("lake_effect_cold_snap", "Lake-effect cold snap", ("lansing",), 72, "alert",
             "Lake-effect cold snap: outdoor air falling toward -22 C for 72 h",
             lambda sim, h0, n: _splice_weather(sim, h0, n, "lake_effect")),
    Scenario("greenhouse_off_season", "Greenhouse off-season", ("lansing",), None, "info",
             "Greenhouses enter off-season: their heat demand drops 80%", _greenhouse_off),
    Scenario("bitcoin_price_crash", "Bitcoin price crash", ("lansing",), 336, "warn",
             "Bitcoin price crash: flexible mining load switched off for 14 days", _bitcoin_crash),
]}


def scenarios_for(site: str) -> list[Scenario]:
    return [s for s in SCENARIOS.values() if site in s.sites]


def apply_scenario(sim: "Simulation", name: str) -> list[dict]:
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}")
    sc = SCENARIOS[name]
    if sim.site not in sc.sites:
        raise ValueError(f"{name} is not available at {sim.site}")
    if sim.done:
        raise ValueError("simulation has finished")
    h0 = sim.h
    n = (sim.hours - h0) if sc.hours is None else min(sc.hours, sim.hours - h0)
    sc.apply(sim, h0, n)
    sim.refresh()
    if name not in sim.active_scenarios:
        sim.active_scenarios.append(name)
    if sc.hours is not None:
        sim.scenario_ends[name] = h0 + n
    return [sim.event(h0, "scenario", sc.severity, sc.opening)]


# ===================================================================== narrator

STORAGE_LABEL = {"hot_water_tank": "hot-water tanks", "borehole": "borehole field", "pit": "pit storage"}


class Narrator:
    """Turns hour-to-hour changes in dispatch into short events."""

    def __init__(self) -> None:
        self.discharging = False
        self.on_backup = 0
        self.guarantee_risk = False
        self.steam_on: bool | None = None
        self.shift_sign = 0
        self.cooling_announced = -10**9

    def observe(self, sim: "Simulation", h: int, r: "HourDispatch") -> list[dict]:
        out: list[dict] = []
        net = sim.net
        ev = lambda kind, sev, text: out.append(sim.event(h, kind, sev, text))

        out_kw = sum(r.storage_out_kw)
        if out_kw > 100 and not self.discharging:
            units = [STORAGE_LABEL.get(u.type, u.id) for u, x in zip(net.storages, r.storage_out_kw) if x > 1]
            ev("ops", "info", f"Storage discharging {out_kw / 1000:.1f} MW ({', '.join(units)})")
        elif out_kw < 1 and self.discharging:
            ev("ops", "ok", "Storage back to charging")
        self.discharging = out_kw > 100 or (self.discharging and out_kw >= 1)

        need = sim.inp.demand_kw[h] > 0
        if r.served_send_kw >= r.requested_send_kw - 1e-6 and (r.steam_hp_on or not net.steam_hp.any()):
            frac = np.ones(net.n)                                       # fast path: nobody short
        else:
            frac = served_fractions(net, sim.der.send_kw[h:h + 1], [r.served_send_kw], [r.steam_hp_on])[0]
        n_backup = int(((frac < 0.99) & need).sum())
        if n_backup and not self.on_backup:
            ev("ops", "warn", f"Backup boilers engaged at {n_backup} building{'s' if n_backup > 1 else ''}")
        elif n_backup and abs(n_backup - self.on_backup) >= 3:
            ev("ops", "warn", f"Backup boilers now running at {n_backup} buildings")
        elif not n_backup and self.on_backup:
            ev("recovery", "ok", "All connected buildings back on network heat")
        self.on_backup = n_backup

        risk = bool((net.guaranteed & need & (frac < 0.99)).any())
        if risk and not self.guarantee_risk:
            names = [b.name for b, g in zip(net.buildings, net.guaranteed) if g]
            ev("ops", "alert", f"Guarantee at risk: {', '.join(names)} on backup; refunds accruing")
        elif not risk and self.guarantee_risk:
            ev("recovery", "ok", "Guaranteed buildings fully served again")
        self.guarantee_risk = risk

        if net.steam_hp.any():
            if self.steam_on is not None and r.steam_hp_on != self.steam_on:
                n = int(net.steam_hp.sum())
                ev("ops", "info", f"Steam heat pumps {'engaged' if r.steam_hp_on else 'paused'} at {n} buildings")
            self.steam_on = r.steam_hp_on

        sign = int(np.sign(round(r.shift_kw)))
        if sign != self.shift_sign:
            if sign > 0:
                ev("ops", "info", f"Compute follows heat: running deferred compute, +{r.shift_kw / 1000:.1f} MW heat")
            elif sign < 0:
                ev("ops", "info", f"Compute follows heat: deferring {-r.shift_kw / 1000:.1f} MW of flexible load ahead of the cold")
        self.shift_sign = sign

        if r.cooling_in_kw > 300 and h - self.cooling_announced >= 24:
            ev("ops", "info", f"Loop selling cooling: absorbing {r.cooling_in_kw / 1000:.1f} MW from offices")
            self.cooling_announced = h
        return out
