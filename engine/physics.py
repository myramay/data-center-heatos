"""Heat network physics.

Two layers, so a full year runs in well under a second:

1. derive(): everything that does not depend on dispatch, vectorized over
   (hours x buildings): heat pump COPs, loop temperature, pipe losses, how
   much loop heat each building draws per kW delivered, summer cooling.
2. dispatch_hour(): the only stateful part (storage, compute bank), in plain
   scalar Python. It takes the autopilot's Decision, enforces every physical
   limit, and returns what actually happened.
3. building_flows(): expands the recorded per-hour dispatch back to
   per-building delivered / backup / electricity, vectorized, and checks the
   hourly energy balance

   dc_used + cooling_in + storage_out + hp_electricity + backup
       == heat_demand + pipe_losses + storage_in          (within 0.1%)

Data center protection: the network only ever takes heat the data center
offers; whatever it doesn't take goes to the data center's own cooling
(dc_fallback_kw). Data center overheating is never modelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from engine.config import SiteConfig
from engine.contracts import Building, Plan

CP_WATER_KWH_PER_M3K = 1.163
BALANCE_TOLERANCE = 1e-3
COP_CAP = 8.0

# ML_TEAM_INTEGRATION: summer cooling demand is estimated here from weather
# until a cooling forecast is added to the contracts.
COOLING_BALANCE_C = 22.0
COOLING_W_PER_M2K = {"office": 3.0, "retail": 3.0, "food": 3.5, "clinic": 3.0}

KIND_DIRECT, KIND_HP, KIND_STEAM_HP, KIND_BOOSTER = 0, 1, 2, 3


class EnergyBalanceError(RuntimeError):
    pass


def cop(eta, t_hot_c, t_cold_c, cap: float = COP_CAP):
    """Carnot-fraction heat pump COP: eta * T_hot / (T_hot - T_cold), Kelvin."""
    th = np.asarray(t_hot_c, dtype=float) + 273.15
    tc = np.asarray(t_cold_c, dtype=float) + 273.15
    return np.clip(eta * th / np.maximum(th - tc, 1.0), 1.0, cap)


# ===================================================================== static network

@dataclass(frozen=True)
class StorageSpec:
    id: str
    type: str
    capacity_kwh: float
    min_kwh: float
    max_charge_kw: float
    max_discharge_kw: float
    loss_per_hour: float
    initial_kwh: float


@dataclass
class Network:
    cfg: SiteConfig
    ids: list[str]
    buildings: list[Building]
    kind: np.ndarray            # (B,) KIND_*
    hot_c: np.ndarray           # (B,) heat pump output temperature
    from_dc: np.ndarray         # (B,) bool: direct link to the data center (not the loop)
    dist_km: np.ndarray
    guaranteed: np.ndarray      # (B,) bool
    priority: np.ndarray        # (B,) building indices in serving order when heat is short
    steam_hp: np.ndarray        # (B,) bool
    dhw_share: np.ndarray
    backup_eff: np.ndarray
    fuel_usd_per_mwh: np.ndarray
    fuel_t_per_mwh: np.ndarray
    cooling_kw_per_k: np.ndarray
    storages: list[StorageSpec]
    flex_kw: float              # flexible compute, heat-equivalent kW
    dc_cap_kw: float

    @property
    def n(self) -> int:
        return len(self.ids)


def build_network(cfg: SiteConfig, buildings: list[Building], plan: Plan,
                  dhw_share: dict[str, float] | None = None) -> Network:
    by_id = {b.id: b for b in buildings}
    items = [i for i in plan.items if i.connect]
    bs = [by_id[i.building_id] for i in items]
    loop_t = cfg.loop.supply_temp_c.value
    dc_t = cfg.data_center.supply_temp_c_max.value
    margin = cfg.loop.direct_use_margin_c.value
    booster_t = cfg.heat_pumps.booster_t_hot_c.value

    kind, hot, from_dc = [], [], []
    for it, b in zip(items, bs):
        if it.option == "direct_link":
            kind.append(KIND_DIRECT if b.required_supply_temp_c <= dc_t - margin else KIND_HP)
            from_dc.append(True)
        elif it.option == "central_hp":
            kind.append(KIND_HP)                  # lift happens at the central plant, straight from data center water
            from_dc.append(True)
        elif it.option == "direct_use":
            kind.append(KIND_DIRECT if b.required_supply_temp_c <= loop_t - margin else KIND_HP)
            from_dc.append(False)
        else:
            kind.append({"loop_hp": KIND_HP, "steam_hp": KIND_STEAM_HP, "booster": KIND_BOOSTER}[it.option])
            from_dc.append(False)
        hot.append(cfg.loop.central_supply_c.value if it.option == "central_hp"
                   else cfg.heat_pumps.steam_hp_t_hot_c.value if it.option == "steam_hp"
                   else booster_t if it.option == "booster" else b.required_supply_temp_c)

    def fuel_param(table, b, default):
        p = table.get(b.heating_system) or table.get("gas_boiler")
        return p.value if p else default

    elec = cfg.prices.electricity_usd_per_mwh.value
    sells_cooling = cfg.loop.sells_cooling
    dc = cfg.data_center
    cap_kw = dc.capacity_mw_th.value * 1000 * dc.capture_fraction.value
    shares = dhw_share or {}
    return Network(
        cfg=cfg, ids=[b.id for b in bs], buildings=bs,
        kind=np.array(kind, int), hot_c=np.array(hot, float), from_dc=np.array(from_dc, bool),
        dist_km=np.array([b.street_distance_m / 1000 for b in bs]),
        guaranteed=np.array([i.guaranteed for i in items], bool),
        priority=np.array(sorted(range(len(bs)), key=lambda k: (not items[k].guaranteed, -bs[k].equity_score,
                                                               bs[k].street_distance_m)), int),
        steam_hp=np.array([i.option == "steam_hp" for i in items], bool),
        dhw_share=np.array([shares.get(b.use_type, 0.2) for b in bs]),
        backup_eff=np.array([(cfg.backup_efficiency.get(b.heating_system) or cfg.backup_efficiency["unknown"]).value for b in bs]),
        fuel_usd_per_mwh=np.array([elec if b.heating_system == "electric" else fuel_param(cfg.prices.fuels_usd_per_mwh, b, 50.0) for b in bs]),
        fuel_t_per_mwh=np.array([fuel_param(cfg.emissions.fuels_t_per_mwh, b, 0.181) for b in bs]),
        cooling_kw_per_k=np.array([COOLING_W_PER_M2K.get(b.use_type, 0.0) * b.floor_area_m2 / 1000
                                     if sells_cooling and it.option != "central_hp" else 0.0 for it, b in zip(items, bs)]),
        storages=[StorageSpec(
            id=s.id, type=s.type, capacity_kwh=s.capacity_mwh.value * 1000,
            min_kwh=s.capacity_mwh.value * 1000 * s.min_soc_fraction.value,
            max_charge_kw=s.max_charge_mw.value * 1000, max_discharge_kw=s.max_discharge_mw.value * 1000,
            loss_per_hour=s.loss_per_hour.value,
            initial_kwh=s.capacity_mwh.value * 1000 * s.initial_soc_fraction.value) for s in cfg.storage],
        flex_kw=cap_kw * dc.flexible_compute_share.value if dc.compute_follows_heat else 0.0,
        dc_cap_kw=cap_kw,
    )


# ===================================================================== pipe routes

@dataclass(frozen=True)
class PipeEdge:
    a: str          # "DC" or building id
    b: str
    length_m: float


def pipe_tree(site: str, buildings: list[Building]) -> list[PipeEdge]:
    """Minimum spanning tree from the data center (origin) to the connected
    buildings. Chelsea routes follow the street grid (Manhattan metric);
    Lansing roads get a 5% detour factor. Used for pipe capex and the 3D scene."""
    from scipy.sparse.csgraph import breadth_first_order, minimum_spanning_tree

    if not buildings:
        return []
    names = ["DC"] + [b.id for b in buildings]
    xy = np.array([[0.0, 0.0]] + [[b.x_m, b.y_m] for b in buildings])
    diff = np.abs(xy[:, None, :] - xy[None, :, :])
    dist = diff.sum(-1) if site == "chelsea" else np.hypot(diff[..., 0], diff[..., 1]) * 1.05
    tree = minimum_spanning_tree(np.maximum(dist, 1e-3)).toarray()
    tree = np.maximum(tree, tree.T)
    order, parent = breadth_first_order(tree, 0, directed=False, return_predecessors=True)
    return [PipeEdge(a=names[parent[i]], b=names[i], length_m=round(float(tree[parent[i], i]), 1))
            for i in order[1:]]


# ===================================================================== hourly inputs and derived physics

@dataclass
class HourlyInputs:
    """Hourly drivers for a run. Scenarios edit these arrays in place, then
    call Simulation.refresh()."""

    times: list
    t_out_c: np.ndarray            # (H,)
    demand_kw: np.ndarray          # (H, B) heat demand of connected buildings
    supply_kw: np.ndarray          # (H,) heat the data center offers
    supply_temp_c: np.ndarray      # (H,)
    elec_usd_per_mwh: np.ndarray   # (H,)
    fuel_price_mult: np.ndarray    # (H,)
    flex_available: np.ndarray | None = None   # (H,) 0..1, e.g. 0 when flexible load is switched off

    @property
    def hours(self) -> int:
        return len(self.t_out_c)


@dataclass
class Derived:
    loop_temp_c: np.ndarray        # (H,)
    cop: np.ndarray                # (H, B)
    elec_per_kw: np.ndarray        # (H, B) heat pump electricity per kW delivered
    send_per_kw: np.ndarray        # (H, B) loop heat sent per kW delivered (incl. pipe loss)
    loss_frac: np.ndarray          # (H, B) share of sent heat lost in pipes
    cooling_in_kw: np.ndarray      # (H, B) heat rejected into the loop by cooling customers
    cooling_elec_kw: np.ndarray    # (H, B)
    send_kw: np.ndarray            # (H, B) loop heat each building needs sent
    send_groups: np.ndarray        # (H, 2) [non-steam buildings, steam heat pump buildings]
    cooling_total_kw: np.ndarray   # (H,)


def derive(net: Network, inp: HourlyInputs, eta: float | None = None) -> Derived:
    cfg = net.cfg
    eta = cfg.heat_pumps.eta.value if eta is None else eta
    lo, hi = cfg.loop.supply_temp_c.low, cfg.loop.supply_temp_c.high
    loop_t = np.clip(inp.supply_temp_c - 3.0, lo, hi)                        # 3 K heat exchanger approach
    cold = np.where(net.from_dc[None, :], inp.supply_temp_c[:, None], loop_t[:, None])
    steam_eta = cfg.heat_pumps.steam_hp_eta.value * eta / cfg.heat_pumps.eta.value
    eta_b = np.where(net.steam_hp, steam_eta, eta)
    c = cop(eta_b[None, :], net.hot_c[None, :], cold)

    direct = net.kind == KIND_DIRECT
    booster = net.kind == KIND_BOOSTER
    hp_share = np.where(booster, net.dhw_share, 1.0)                          # booster lifts hot water only
    elec = np.where(direct, 0.0, hp_share / c)
    loop_frac = 1.0 - elec

    if cfg.loop.type == "ambient_two_way":
        scale = np.clip((loop_t - cfg.loop.ambient_loss_ref_c) / cfg.loop.ambient_loss_span_c, 0.0, None)[:, None]
    else:
        scale = np.ones((inp.hours, 1))
    loss = np.clip(cfg.loop.pipe_loss_per_km.value * net.dist_km[None, :] * scale, 0.0, 0.5)
    send = loop_frac / (1.0 - loss)

    if cfg.loop.sells_cooling and net.n:
        ccop = cfg.loop.cooling_cop.value if cfg.loop.cooling_cop else 6.0
        cool = net.cooling_kw_per_k[None, :] * np.maximum(inp.t_out_c - COOLING_BALANCE_C, 0.0)[:, None]
        cooling_in, cooling_elec = cool * (1 + 1 / ccop), cool / ccop
    else:
        cooling_in = cooling_elec = np.zeros_like(inp.demand_kw)

    s = inp.demand_kw * send
    st = net.steam_hp
    groups = np.stack([s[:, ~st].sum(1), s[:, st].sum(1)], axis=1)
    return Derived(loop_temp_c=loop_t, cop=c, elec_per_kw=elec, send_per_kw=send, loss_frac=loss,
                   cooling_in_kw=cooling_in, cooling_elec_kw=cooling_elec, send_kw=s, send_groups=groups,
                   cooling_total_kw=cooling_in.sum(1))


# ===================================================================== dispatch

@dataclass
class Decision:
    """What the autopilot asks for this hour. Physics enforces the limits."""

    storage_kw: list[float]            # per storage unit: + charge, - discharge
    shift_kw: float = 0.0              # + run deferred compute now, - defer compute
    steam_hp_on: bool = True
    curtail_cooling_frac: float = 0.0  # share of cooling customers turned away
    serve_cap_kw: float | None = None  # cap on loop heat sent to non-steam buildings (rest go to backup by choice)


@dataclass
class HourDispatch:
    supply_kw: float                   # offered after compute shift
    dc_used_kw: float
    dc_fallback_kw: float              # heat the data center's own cooling handles
    cooling_in_kw: float
    storage_in_kw: list[float]
    storage_out_kw: list[float]
    storage_loss_kw: list[float]
    soc_kwh: list[float]               # end of hour
    served_send_kw: float
    shortfall_kw: float                # heat the network could not deliver
    requested_send_kw: float           # what all connected buildings needed (before any voluntary cap)
    steam_hp_on: bool
    shift_kw: float
    flex_bank_kwh: float


def dispatch_hour(net: Network, send_groups, cooling_total_kw: float, supply_kw: float,
                  soc_kwh: list[float], flex_bank_kwh: float, d: Decision,
                  flex_kw: float | None = None) -> HourDispatch:
    send_ns = float(send_groups[0])
    if d.serve_cap_kw is not None:
        send_ns = min(send_ns, max(d.serve_cap_kw, 0.0))
    send_req = send_ns + (float(send_groups[1]) if d.steam_hp_on else 0.0)

    # compute follows heat: bank holds deferred compute (heat-equivalent kWh)
    flex = net.flex_kw if flex_kw is None else flex_kw
    shift = 0.0
    if flex > 0:
        if d.shift_kw > 0:
            shift = min(d.shift_kw, flex, flex_bank_kwh, max(net.dc_cap_kw - supply_kw, 0.0))
        elif d.shift_kw < 0:
            shift = -min(-d.shift_kw, flex, supply_kw, max(net.flex_kw * 24 - flex_bank_kwh, 0.0))
    supply = supply_kw + shift
    cool_avail = cooling_total_kw * (1.0 - min(max(d.curtail_cooling_frac, 0.0), 1.0))

    # storage requests clipped to rate and state-of-charge limits
    charge_req, dis_req = [], []
    for u, soc, req in zip(net.storages, soc_kwh, d.storage_kw):
        charge_req.append(min(max(req, 0.0), u.max_charge_kw, max(u.capacity_kwh - soc, 0.0)))
        dis_req.append(min(max(-req, 0.0), u.max_discharge_kw, max(soc - u.min_kwh, 0.0)))

    base = cool_avail + supply
    # discharge only what the network actually needs
    dis_need = max(send_req - base, 0.0)
    dis_total = sum(dis_req)
    dis_scale = min(1.0, dis_need / dis_total) if dis_total > 0 else 0.0
    dis = [x * dis_scale for x in dis_req]
    # charge only from genuine surplus
    surplus = max(base + sum(dis) - send_req, 0.0)
    ch_total = sum(charge_req)
    ch_scale = min(1.0, surplus / ch_total) if ch_total > 0 else 0.0
    ch = [x * ch_scale for x in charge_req]

    available = base + sum(dis)
    served = min(send_req, available)
    leftover = available - served - sum(ch)          # heat nobody can use this hour
    dc_used = supply - min(leftover, supply)
    cool_used = cool_avail - max(leftover - supply, 0.0)

    new_soc, losses = [], []
    for u, s0, c, x in zip(net.storages, soc_kwh, ch, dis):
        mid = s0 + c - x
        loss = mid * u.loss_per_hour
        new_soc.append(mid - loss)
        losses.append(loss)

    return HourDispatch(
        supply_kw=supply, dc_used_kw=dc_used, dc_fallback_kw=supply - dc_used, cooling_in_kw=cool_used,
        storage_in_kw=ch, storage_out_kw=dis, storage_loss_kw=losses, soc_kwh=new_soc,
        served_send_kw=served, shortfall_kw=send_req - served,
        requested_send_kw=float(send_groups[0]) + (float(send_groups[1]) if d.steam_hp_on else 0.0),
        steam_hp_on=d.steam_hp_on, shift_kw=shift, flex_bank_kwh=flex_bank_kwh - shift)


# ===================================================================== per-building flows + balance check

@dataclass
class Flows:
    served_frac: np.ndarray       # (h, B)
    delivered_kw: np.ndarray      # network heat delivered
    backup_kw: np.ndarray         # heat from each building's existing boiler / steam
    backup_fuel_kw: np.ndarray
    hp_elec_kw: np.ndarray
    pipe_loss_kw: np.ndarray
    sent_kw: np.ndarray
    flow_m3h: np.ndarray          # (h,)
    pump_kw: np.ndarray           # (h,)
    return_temp_c: np.ndarray     # (h,)
    balance_error: np.ndarray     # (h,) relative
    extra: dict = field(default_factory=dict)


def served_fractions(net: Network, send_kw: np.ndarray, served_kw, steam_on) -> np.ndarray:
    """Share of each building's demand met by the network when heat is short:
    buildings are filled whole, in priority order (guarantees first, then equity),
    so a shortfall moves the fewest buildings onto backup."""
    off = net.steam_hp[None, :] & ~np.asarray(steam_on, bool)[:, None]
    send = np.where(off, 0.0, send_kw)[:, net.priority]
    before = np.cumsum(send, axis=1) - send
    frac_ord = np.clip((np.asarray(served_kw, float)[:, None] - before) / np.where(send > 0, send, 1.0), 0.0, 1.0)
    frac_ord = np.where(send > 0, frac_ord, 1.0)
    frac = np.empty_like(frac_ord)
    frac[:, net.priority] = frac_ord
    return np.where(off, 0.0, frac)


def building_flows(net: Network, inp: HourlyInputs, der: Derived, sl: slice,
                   served, steam_on, dc_used, cooling_in, storage_in, storage_out) -> Flows:
    need = inp.demand_kw[sl]
    frac = served_fractions(net, der.send_kw[sl], served, steam_on)
    delivered = need * frac
    backup = need - delivered
    sent = need * der.send_per_kw[sl] * frac
    loss = sent * der.loss_frac[sl]
    elec = delivered * der.elec_per_kw[sl]

    cfg = net.cfg
    dt = cfg.loop.supply_temp_c.value - cfg.loop.return_temp_c.value
    moved = sent.sum(1) + np.asarray(cooling_in)
    flow = moved / (CP_WATER_KWH_PER_M3K * dt)
    design_flow = cfg.pumping.design_flow_m3h.value
    pump = cfg.pumping.design_power_kw.value * (flow / design_flow) ** 3     # affinity laws
    ret = der.loop_temp_c[sl] - dt * np.clip(flow / design_flow, 0.0, 1.0)

    lhs = (np.asarray(dc_used) + np.asarray(cooling_in) + np.asarray(storage_out).sum(-1)
           + elec.sum(1) + backup.sum(1))
    rhs = need.sum(1) + loss.sum(1) + np.asarray(storage_in).sum(-1)
    err = np.abs(lhs - rhs) / np.maximum(np.maximum(lhs, rhs), 1.0)
    return Flows(served_frac=frac, delivered_kw=delivered, backup_kw=backup,
                 backup_fuel_kw=backup / net.backup_eff[None, :], hp_elec_kw=elec, pipe_loss_kw=loss,
                 sent_kw=sent, flow_m3h=flow, pump_kw=pump, return_temp_c=ret, balance_error=err)


def check_balance(flows: Flows, first_hour: int = 0) -> None:
    bad = np.flatnonzero(flows.balance_error > BALANCE_TOLERANCE)
    if len(bad):
        h = int(bad[0])
        raise EnergyBalanceError(
            f"energy balance off by {flows.balance_error[h]:.3%} at hour {first_hour + h} "
            f"({len(bad)} hours fail)")
