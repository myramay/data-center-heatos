"""Who pays whom, and does every party come out ahead?

Conventions (all configurable in the site YAML; flagged where they are
interpretations of the deal terms):

- Heat price is ALL-IN: a connected building's heat costs it
  heat_price_fraction x its current $/MWh, including the electricity its own
  heat pump uses. The network owner receives that bill minus the building's
  heat pump electricity (the "loop charge"), so the owner carries electricity
  price risk and buildings get a predictable saving.
- Backup heat (existing boiler / steam) is paid at the building's current cost.
- Capex is annuitized at each party's financing terms (C-PACE, on-bill, or the
  party's own discount rate for upfront spend). For windows shorter than a year,
  annual charges are allocated by the window's share of annual heat.
- Chelsea: Local Law 97 avoided fines count as a building benefit (verify each
  building's LL97 exposure). Lansing: the tax abatement offset is modelled as a
  benefit to TeraWulf for joining the network (interpretation - verify).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import numpy as np

from engine.config import SiteConfig
from engine.physics import KIND_HP, pipe_tree

if TYPE_CHECKING:
    from engine.sim import RunResult, Simulation

EXTERNAL = {"grid": "Electric grid", "fuel": "Fuel / steam suppliers", "lenders": "Lenders (C-PACE / on-bill)",
            "nyserda": "NYSERDA grant", "town": "Town of Lansing (tax abatement)"}


def crf(rate: float, years: float) -> float:
    return rate / (1 - (1 + rate) ** -years) if rate > 0 else 1 / years


# ===================================================================== terms

@dataclass(frozen=True)
class DealTerms:
    heat_price_fraction: dict[str, float]     # party -> share of current heat cost it pays
    grant_share: float = 1.0                  # share of public-housing building capex covered by grant + surcharge
    phase_delay_years: int = 0                # delay applied to phase 2-3 buildings

    @classmethod
    def from_config(cls, cfg: SiteConfig) -> "DealTerms":
        fr = {p.id: p.terms["heat_price_fraction"].value for p in cfg.parties if "heat_price_fraction" in p.terms}
        ph = next((p for p in cfg.parties if p.id == "public_housing"), None)
        grant = ph.terms["grant_share"].value if ph and "grant_share" in ph.terms else 0.0
        return cls(heat_price_fraction=fr, grant_share=grant)

    def with_discount(self, discount: float) -> "DealTerms":
        """Offtakers pay (1 - discount) of current cost; public housing never pays more than before."""
        fr = {p: (min(f, 1 - discount) if p == "public_housing" else 1 - discount)
              for p, f in self.heat_price_fraction.items()}
        return replace(self, heat_price_fraction=fr)


# ===================================================================== capex

@dataclass(frozen=True)
class CapexLine:
    party: str
    label: str
    usd: float
    building: int | None                     # index into net.buildings; None = network-wide
    financing: tuple[float, float] | None    # (rate, years) if financed, else paid upfront


def party_finance(cfg: SiteConfig, party: str) -> tuple[float, int]:
    """(discount rate, horizon years) used for a party's NPV."""
    if party == "con_ed":
        t = cfg.party("con_ed").terms
        return t["regulated_return"].value, int(t["recovery_years"].value)
    return cfg.finance.discount_rate.value, cfg.finance.horizon_years


def capex_lines(sim: "Simulation", terms: DealTerms) -> list[CapexLine]:
    cfg, net = sim.cfg, sim.net
    cap = {k: v.value for k, v in cfg.capex.items()}
    items = {i.building_id: i for i in sim.plan.items}
    pipe_m = sum(e.length_m for e in pipe_tree(sim.site, net.buildings))
    storage = sum(s.capacity_mwh.value * cap.get({"hot_water_tank": "tank_usd_per_mwh", "borehole": "borehole_usd_per_mwh",
                                                   "pit": "pit_usd_per_mwh"}[s.type], 0.0) for s in cfg.storage)
    lines: list[CapexLine] = []
    chelsea = sim.site == "chelsea"

    if chelsea:
        ct = cfg.party("commercial").terms
        cpace = (ct["cpace_rate"].value, ct["cpace_term_years"].value)
        for i, b in enumerate(net.buildings):
            it = items[b.id]
            party = cfg.party_by_use_type[b.use_type]
            kw = it.design_capacity_kw
            station = cap["transfer_station_anchor_usd"] * (b.floor_area_m2 / cap["transfer_station_ref_m2"]) ** cap["transfer_station_scale_exp"]
            hp = kw * cap["heat_pump_usd_per_kw"] if net.kind[i] == KIND_HP else 0.0
            if it.option == "direct_link":
                equip = cap["direct_link_usd"] + hp
            elif it.option == "steam_hp":
                lines.append(CapexLine("con_ed", f"steam heat pump {b.id}", kw * cap["steam_hp_usd_per_kw"], i, None))
                equip = 0.0
            else:
                equip = hp
            cost = station + equip
            if party == "public_housing":
                lines.append(CapexLine("nyserda", f"grant {b.id}", cost * terms.grant_share, i, None))
                if terms.grant_share < 1:
                    lines.append(CapexLine(party, f"building equipment {b.id}", cost * (1 - terms.grant_share), i, cpace))
            else:
                lines.append(CapexLine(party, f"building equipment {b.id}", cost, i, cpace))
        lines.append(CapexLine("con_ed", "ambient loop pipe + pumps", pipe_m * cap["pipe_usd_per_m"], None, None))
        lines.append(CapexLine("con_ed", "storage (tanks + boreholes)", storage, None, None))
        lines.append(CapexLine("con_ed", "data center heat exchanger", cap["dc_heat_exchanger_usd"], None, None))
    else:
        tw = cfg.party("terawulf").terms["network_share"].value
        ht = cfg.party("homes_coop").terms
        onbill = (ht["on_bill_rate"].value, ht["on_bill_term_years"].value)
        loop = pipe_m * cap["pipe_usd_per_m"]
        for i, b in enumerate(net.buildings):
            it = items[b.id]
            kw = it.design_capacity_kw
            if it.option == "booster":
                lines.append(CapexLine("homes_coop", f"booster heat pumps {b.id}",
                                       kw * net.dhw_share[i] * cap["booster_usd_per_kw"], i, onbill))
            loop += kw * cap["direct_use_station_usd_per_kw"]
        lines.append(CapexLine("terawulf", "share of warm loop + stations", loop * tw, None, None))
        lines.append(CapexLine("terawulf", "heat export station", cap["export_station_usd"], None, None))
        lines.append(CapexLine("joint_venture", "warm loop + stations", loop * (1 - tw), None, None))
        lines.append(CapexLine("joint_venture", "pit thermal storage", storage, None, None))
    return lines


# ===================================================================== hourly money

@dataclass
class Flow:
    src: str
    dst: str
    label: str
    usd: np.ndarray        # (H,)


@dataclass
class PartyResult:
    id: str
    name: str
    role: str
    upfront_usd: float
    financed_usd: float
    annual_operating_usd: float
    annual_financing_usd: float
    window_operating_usd: float
    window_net_usd: float              # operating minus capex charges allocated to the window
    cashflows: list[float]             # year 0..N
    npv_usd: float
    payback_year: int | None
    discount_rate: float
    horizon_years: int
    p_ahead: float | None = None

    @property
    def ahead(self) -> bool:
        return self.npv_usd >= 0


@dataclass
class Ledger:
    site: str
    terms: DealTerms
    hours: int
    annualized_from: str                       # "full_year" | "heat_share_extrapolation"
    parties: dict[str, PartyResult]
    flows: list[Flow]
    hourly_party_net: dict[str, np.ndarray]    # (H,) operating cash by party
    refunds_by_building: dict[str, float]      # window refunds (guaranteed buildings)
    annual_refunds_by_building: dict[str, float]
    capex: list[CapexLine] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)

    def sankey(self, h0: int = 0, h1: int | None = None) -> dict:
        totals: dict[tuple[str, str, str], float] = {}
        for f in self.flows:
            v = float(f.usd[h0:h1].sum())
            if v > 0.5:
                totals[(f.src, f.dst, f.label)] = totals.get((f.src, f.dst, f.label), 0.0) + v
        ids = sorted({n for k in totals for n in k[:2]})
        return {"nodes": [{"id": n, "name": self.names.get(n, n)} for n in ids],
                "links": [{"source": s, "target": t, "label": lab, "value_usd": round(v, 2)}
                          for (s, t, lab), v in sorted(totals.items(), key=lambda kv: -kv[1])]}

    def cumulative_cash(self) -> dict[str, list[float]]:
        return {p: list(np.cumsum(r.cashflows)) for p, r in self.parties.items()}


@dataclass
class Operating:
    """Hourly operating money for one run; independent of capex, grants and timing."""

    hours: int
    full_year: bool
    party_of: list[str]
    owner_split: dict[str, float]            # who receives the network owner's take
    annual_b: dict[str, np.ndarray]          # party -> (B,) annual operating cash by building
    annual_n: dict[str, float]               # party -> network-level annual operating cash
    window_b: dict[str, np.ndarray]
    window_n: dict[str, float]
    window_share_b: np.ndarray
    network_share: float
    hourly_party_net: dict[str, np.ndarray]
    flows: list[Flow]
    premium_profile: np.ndarray              # (H, B) hourly share of annual heat (spreads premiums)
    refunds_window_b: np.ndarray
    refunds_annual_b: np.ndarray


def operating(sim: "Simulation", result: "RunResult", fractions: dict[str, float]) -> Operating:
    cfg, net = sim.cfg, sim.net
    H = len(result.supply_kw)
    sl = slice(result.start_hour, result.start_hour + H)
    f = result.flows
    chelsea = sim.site == "chelsea"
    owner = "con_ed" if chelsea else "joint_venture"
    party_of = [cfg.party_by_use_type[b.use_type] for b in net.buildings]
    B = net.n

    need = sim.inp.demand_kw[sl]
    price = sim.inp.elec_usd_per_mwh[sl] / 1000                                   # $/kWh (H,)
    cost = np.array([b.current_heat_cost_usd_per_mwh for b in net.buildings])[None, :] \
        * sim.inp.fuel_price_mult[sl][:, None] / 1000                            # $/kWh heat (H, B)
    frac = np.array([fractions.get(p, 0.85) for p in party_of])

    bill = f.delivered_kw * cost * frac
    hp_elec = f.hp_elec_kw * price[:, None]
    loop_charge = bill - hp_elec
    saving = f.delivered_kw * cost * (1 - frac)
    backup_cost = f.backup_kw * cost

    annual_need = np.array([b.annual_heat_mwh * 1000 for b in net.buildings])
    window_share_b = np.divide(need.sum(0), annual_need, out=np.zeros(B), where=annual_need > 0)
    network_share = float(need.sum() / annual_need.sum()) if annual_need.sum() else 0.0
    full_year = H >= 8760

    gb = next((p for p in cfg.parties if p.id == "guarantee_buyers"), None)
    refund_mult = gb.terms["refund_multiple"].value if gb else 3.0
    missed = f.backup_kw > 0.01 * np.maximum(need, 1e-9)
    refunds = np.where(missed & net.guaranteed[None, :], refund_mult * need * cost * frac, 0.0)

    contrib: dict[str, np.ndarray] = {p.id: np.zeros((H, B)) for p in cfg.parties}
    network: dict[str, np.ndarray] = {p.id: np.zeros(H) for p in cfg.parties}
    flows: list[Flow] = []
    col = lambda arr, p: arr[:, [k for k, q in enumerate(party_of) if q == p]].sum(1)

    for k, p in enumerate(party_of):
        contrib[p][:, k] += saving[:, k] + refunds[:, k]
    owner_take = loop_charge - refunds

    if chelsea:
        owner_split = {"con_ed": 1.0}
        fine = cfg.policy["ll97_fine_usd_per_t"].value
        grid = cfg.emissions.grid_t_per_mwh.value
        avoided_t = (f.delivered_kw / net.backup_eff * net.fuel_t_per_mwh - f.hp_elec_kw * grid) / 1000
        ll97 = avoided_t * fine
        surcharge_rate = cfg.party("public_housing").terms["commercial_surcharge"].value
        surcharge = np.where(np.array(party_of) == "commercial", surcharge_rate * bill, 0.0)
        ccop = cfg.loop.cooling_cop.value if cfg.loop.cooling_cop else 6.0
        accepted = np.divide(result.cooling_in_kw, sim.der.cooling_total_kw[sl],
                             out=np.zeros(H), where=sim.der.cooling_total_kw[sl] > 0)
        cool_sold = sim.der.cooling_in_kw[sl] * accepted[:, None] / (1 + 1 / ccop)
        cool_pay = cool_sold * cfg.prices.cooling_sale_usd_per_mwh.value / 1000
        cool_benefit = cool_sold * price[:, None] / cfg.data_center.chiller_cop.value - cool_pay
        for k, p in enumerate(party_of):
            contrib[p][:, k] += ll97[:, k] - surcharge[:, k] + cool_benefit[:, k]
            contrib["public_housing"][:, k] += surcharge[:, k]
        contrib[owner] += owner_take + cool_pay

        chiller_avoided = result.dc_used_kw / cfg.data_center.chiller_cop.value * price
        fee = chiller_avoided * cfg.party("google").terms["heat_removal_fee_share"].value
        pump = f.pump_kw * price
        network["google"] += chiller_avoided - fee
        network["con_ed"] += fee - pump
        flows += [Flow("google", "con_ed", "heat removal fee", fee), Flow("con_ed", "grid", "pumping", pump),
                  Flow("commercial", "public_housing", "2% equity surcharge", surcharge.sum(1)),
                  Flow("commercial", "con_ed", "cooling sales", cool_pay.sum(1))]
    else:
        share = cfg.party("terawulf").terms["heat_revenue_share"].value
        owner_split = {"joint_venture": 1 - share, "terawulf": share}
        contrib["joint_venture"] += owner_take * (1 - share)
        contrib["terawulf"] += owner_take * share
        chiller_avoided = result.dc_used_kw / cfg.data_center.chiller_cop.value * price
        abatement = np.full(H, cfg.policy["tax_abatement_offset_usd_per_yr"].value / 8760)
        pump = f.pump_kw * price
        network["terawulf"] += chiller_avoided + abatement
        network["joint_venture"] -= pump
        flows += [Flow("joint_venture", "terawulf", "heat revenue share", owner_take.sum(1) * share),
                  Flow("town", "terawulf", "tax abatement offset", abatement),
                  Flow("joint_venture", "grid", "pumping", pump)]

    for p in sorted(set(party_of)):
        flows += [Flow(p, owner, "heat (loop charge)", np.maximum(col(loop_charge, p), 0.0)),
                  Flow(owner, p, "electricity credit", np.maximum(-col(loop_charge, p), 0.0)),
                  Flow(p, "grid", "heat pump electricity", col(hp_elec, p)),
                  Flow(p, "fuel", "backup heat", col(backup_cost, p)),
                  Flow(owner, p, "missed-hour refunds", col(refunds, p))]

    scale_b = np.ones(B) if full_year else np.divide(1.0, window_share_b, out=np.zeros(B), where=window_share_b > 0)
    scale_n = 1.0 if full_year else (1.0 / network_share if network_share > 0 else 0.0)
    yr = slice(0, min(H, 8760))
    return Operating(
        hours=H, full_year=full_year, party_of=party_of, owner_split=owner_split,
        annual_b={p: contrib[p][yr].sum(0) * scale_b for p in contrib},
        annual_n={p: float(network[p][yr].sum()) * scale_n for p in network},
        window_b={p: contrib[p].sum(0) for p in contrib}, window_n={p: float(network[p].sum()) for p in network},
        window_share_b=window_share_b, network_share=network_share,
        hourly_party_net={p: contrib[p].sum(1) + network[p] for p in contrib}, flows=flows,
        premium_profile=need / np.where(annual_need > 0, annual_need, 1.0)[None, :],
        refunds_window_b=refunds.sum(0), refunds_annual_b=refunds[yr].sum(0) * scale_b)


def economics(sim: "Simulation", op: Operating, terms: DealTerms,
              premiums: dict[str, float] | None = None) -> dict[str, PartyResult]:
    """Annual cash flows, NPV and payback per party for given terms."""
    cfg, net = sim.cfg, sim.net
    B = net.n
    premiums = premiums or {}
    prem = np.array([premiums.get(bid, 0.0) for bid in net.ids])
    lines = capex_lines(sim, terms)
    items = {i.building_id: i for i in sim.plan.items}
    delay = np.array([terms.phase_delay_years if (items[bid].phase or 1) >= 2 else 0 for bid in net.ids], int)

    parties: dict[str, PartyResult] = {}
    for p in cfg.parties:
        rate, horizon = party_finance(cfg, p.id)
        op_b = op.annual_b[p.id].copy()
        win_b = op.window_b[p.id].copy()
        for k in range(B):        # guarantee premiums: buyer -> network owner(s)
            if prem[k]:
                sign = -1.0 if op.party_of[k] == p.id else 0.0
                sign += op.owner_split.get(p.id, 0.0)
                op_b[k] += sign * prem[k]
                win_b[k] += sign * prem[k] * op.window_share_b[k]
        op_n = op.annual_n[p.id]
        cf = np.zeros(horizon + 1)
        cf[1:] += op_n
        for k in range(B):
            cf[1 + delay[k]:] += op_b[k]
        upfront = financed = annual_fin = window_charge = 0.0
        for ln in (x for x in lines if x.party == p.id):
            d = int(delay[ln.building]) if ln.building is not None else 0
            share = op.window_share_b[ln.building] if ln.building is not None else op.network_share
            if ln.financing:
                pay = ln.usd * crf(*ln.financing)
                n = int(ln.financing[1])
                cf[1 + d:min(1 + d + n, horizon + 1)] -= pay
                financed += ln.usd
                annual_fin += pay
                window_charge += pay * share
            else:
                cf[d] -= ln.usd
                upfront += ln.usd
                window_charge += ln.usd * crf(rate, horizon) * share
        npv = float(np.sum(cf / (1 + rate) ** np.arange(horizon + 1)))
        cum = np.cumsum(cf)
        first = 0 if upfront == 0 and cf[0] >= 0 and (horizon == 0 or cf[1] >= 0) else 1
        pay_year = next((t for t in range(first, horizon + 1) if cum[t] >= 0 and (cum[t:] >= 0).all()), None)
        window_op = float(win_b.sum() + op.window_n[p.id])
        parties[p.id] = PartyResult(
            id=p.id, name=p.name, role=p.role, upfront_usd=upfront, financed_usd=financed,
            annual_operating_usd=float(op_b.sum() + op_n), annual_financing_usd=annual_fin,
            window_operating_usd=window_op, window_net_usd=window_op - window_charge,
            cashflows=[round(float(x), 2) for x in cf], npv_usd=npv, payback_year=pay_year,
            discount_rate=rate, horizon_years=horizon)
    return parties


def compute_ledger(sim: "Simulation", result: "RunResult", terms: DealTerms | None = None,
                   premiums: dict[str, float] | None = None) -> Ledger:
    terms = terms or DealTerms.from_config(sim.cfg)
    premiums = premiums or {}
    op = operating(sim, result, terms.heat_price_fraction)
    parties = economics(sim, op, terms, premiums)
    net = sim.net
    owner = next(iter(op.owner_split)) if len(op.owner_split) == 1 else "joint_venture"
    flows = list(op.flows)
    for k, bid in enumerate(net.ids):
        if premiums.get(bid):
            flows.append(Flow(op.party_of[k], owner, "guarantee premium", op.premium_profile[:, k] * premiums[bid]))
    H = op.hours
    for p in parties.values():
        if p.annual_financing_usd:
            flows.append(Flow(p.id, "lenders", "equipment financing", np.full(H, p.annual_financing_usd / 8760)))
    g = net.guaranteed
    return Ledger(
        site=sim.site, terms=terms, hours=H, annualized_from="full_year" if op.full_year else "heat_share_extrapolation",
        parties=parties, flows=flows, hourly_party_net=op.hourly_party_net,
        refunds_by_building={bid: float(op.refunds_window_b[k]) for k, bid in enumerate(net.ids) if g[k]},
        annual_refunds_by_building={bid: float(op.refunds_annual_b[k]) for k, bid in enumerate(net.ids) if g[k]},
        capex=capex_lines(sim, terms), names={**EXTERNAL, **{p.id: p.name for p in sim.cfg.parties}})


def hour_money(sim: "Simulation", h: int, terms: DealTerms | None = None,
               premiums: dict[str, float] | None = None) -> dict:
    """Money flows for one simulated hour (live frames): links, party net, refunds by building."""
    terms = terms or DealTerms.from_config(sim.cfg)
    premiums = premiums or {}
    op = operating(sim, sim.hour_result(h), terms.heat_price_fraction)
    net = sim.net
    owner = "con_ed" if sim.site == "chelsea" else "joint_venture"
    links: dict[tuple[str, str, str], float] = {}
    for f in op.flows:
        v = float(f.usd.sum())
        if v > 0.005:
            links[(f.src, f.dst, f.label)] = links.get((f.src, f.dst, f.label), 0.0) + v
    party_net = {p: float(v.sum()) for p, v in op.hourly_party_net.items()}
    for k, bid in enumerate(net.ids):
        if premiums.get(bid):
            v = float(op.premium_profile[0, k] * premiums[bid])
            links[(op.party_of[k], owner, "guarantee premium")] = links.get((op.party_of[k], owner, "guarantee premium"), 0.0) + v
            party_net[op.party_of[k]] -= v
            for p, share in op.owner_split.items():
                party_net[p] += v * share
    return {"links": [{"source": s, "target": t, "label": lab, "value_usd": round(v, 2)} for (s, t, lab), v in links.items()],
            "party_net": {p: round(v, 2) for p, v in party_net.items()},
            "refunds_by_building": {bid: float(op.refunds_window_b[k]) for k, bid in enumerate(net.ids) if net.guaranteed[k]}}
