"""Heat guarantees and the "Everyone wins?" deal check.

Guarantee premium per building = E[annual refunds] + (CVaR95[refunds] - E[refunds]),
where a refund of refund_multiple x the hour's heat bill is owed for every
missed hour (network can't fully serve the building).

The deal is proposed only if every party's P(20-yr NPV >= 0) >= 0.80 across
sampled futures. If not, HeatOS searches the levers (heat price discount
5-25%, public-housing grant share, phase timing), preferring the smallest
change from the configured terms, and reports what moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from engine import providers
from engine.config import load_site
from engine.contracts import ConfidenceResult, FutureInputs, Plan, SimState, SiteId
from engine.ledger import DealTerms, Ledger, crf, economics, operating, party_finance

AHEAD_THRESHOLD = 0.80
DISCOUNTS = (0.05, 0.10, 0.15, 0.20, 0.25)
GRANT_SHARES = (1.0, 0.75, 0.5)
PHASE_DELAYS = (0, 1, 2)
ANNUAL_START = datetime(2026, 1, 1)


@dataclass
class GuaranteeQuote:
    building_id: str
    expected_refunds_usd: float
    cvar95_usd: float
    premium_usd: float


def quote(refunds: np.ndarray, building_id: str, alpha: float = 0.95) -> GuaranteeQuote:
    r = np.sort(np.asarray(refunds, float))
    tail = r[int(np.floor(alpha * len(r))):] if len(r) else r
    mean = float(r.mean()) if len(r) else 0.0
    cvar = float(tail.mean()) if len(tail) else mean
    return GuaranteeQuote(building_id, mean, cvar, mean + (cvar - mean))


def guarantee_status(sim, ledger: Ledger, confidence: ConfidenceResult | None,
                     premiums: dict[str, float] | None = None) -> list[dict]:
    """Guarantees panel rows for the live run."""
    premiums = premiums or {}
    rows = []
    n = sim.h
    need = sim.inp.demand_kw[:n]
    flows = sim._flows(slice(0, n)) if n else None
    for k, b in enumerate(sim.net.buildings):
        if not sim.net.guaranteed[k]:
            continue
        missed = int((flows.backup_kw[:, k] > 0.01 * np.maximum(need[:, k], 1e-9)).sum()) if n else 0
        rows.append({
            "building_id": b.id, "name": b.name,
            "premium_usd_per_yr": round(premiums.get(b.id, 0.0), 2),
            "p_kept": None if confidence is None else confidence.p_guarantee_kept.get(b.id),
            "missed_hours": missed,
            "refunds_owed_usd": round(ledger.refunds_by_building.get(b.id, 0.0), 2),
        })
    return rows


# ===================================================================== deal search

@dataclass
class DealReport:
    site: SiteId
    cleared: bool
    terms: DealTerms
    baseline_terms: DealTerms
    p_ahead: dict[str, float]
    median_npv_usd: dict[str, float]
    premiums_usd: dict[str, float]
    changes: list[str]
    binding_parties: list[str]
    gap_usd_per_yr: dict[str, float]      # annual cash a binding party is short (median), to reach NPV 0
    n_futures: int
    candidates_tried: int
    grid: list[dict] = field(default_factory=list)


def annual_state(site: SiteId) -> SimState:
    cfg = load_site(site)
    cap = sum(s.capacity_mwh.value for s in cfg.storage)
    soc = sum(s.capacity_mwh.value * s.initial_soc_fraction.value for s in cfg.storage)
    return SimState(site=site, time=ANNUAL_START, hour_index=0, horizon_hours=8760, t_out_c=0.0, supply_kw=0.0,
                    demand_kw=0.0, storage_soc_mwh=soc, storage_capacity_mwh=cap,
                    electricity_price_usd_per_mwh=cfg.prices.electricity_usd_per_mwh.value)


def _sample(state: SimState, n: int, seed: int) -> list[FutureInputs]:
    # ML_TEAM_INTEGRATION: uses the confidence engine's sampler (LHS once real) when it has one.
    sampler = getattr(providers.CONFIDENCE, "sample_futures", None)
    if sampler:
        return sampler(state, n)
    rng = np.random.default_rng(seed)
    return [FutureInputs(seed=int(rng.integers(2**31)), cop_eta=float(rng.uniform(0.5, 0.8)),
                         demand_mult=float(rng.uniform(0.85, 1.15)), supply_mult=float(rng.uniform(0.8, 1.2)),
                         electricity_price_mult=float(rng.uniform(0.8, 1.2)), fuel_price_mult=float(rng.uniform(0.8, 1.2)))
            for _ in range(n)]


def _describe(base: DealTerms, t: DealTerms, discount: float | None) -> list[str]:
    out = []
    if discount is not None:
        out.append(f"heat price set to {1 - discount:.0%} of current cost for offtakers "
                   f"(was {', '.join(f'{p} {f:.0%}' for p, f in sorted(base.heat_price_fraction.items()))})")
    if t.grant_share != base.grant_share:
        out.append(f"public-housing grant share {base.grant_share:.0%} -> {t.grant_share:.0%}")
    if t.phase_delay_years != base.phase_delay_years:
        out.append(f"phase 2-3 buildings delayed {t.phase_delay_years} year(s)")
    return out


def everyone_wins(site: SiteId, plan: Plan, n_futures: int = 30, threshold: float = AHEAD_THRESHOLD,
                  seed: int = 7) -> DealReport:
    cfg = load_site(site)
    state = annual_state(site)
    futures = _sample(state, n_futures, seed)
    base = DealTerms.from_config(cfg)
    discount_opts: list[float | None] = [None, *DISCOUNTS]

    # physics once per future; operating money once per (future, price level)
    ops: dict[float | None, list] = {d: [] for d in discount_opts}
    sims = []
    for f in futures:
        sim, r = providers.SIMULATOR.play(plan, state, f)
        sims.append(sim)
        for d in discount_opts:
            terms = base if d is None else base.with_discount(d)
            ops[d].append(operating(sim, r, terms.heat_price_fraction))

    def evaluate(d, grant, delay):
        terms = (base if d is None else base.with_discount(d))
        terms = DealTerms(terms.heat_price_fraction, grant, delay)
        gids = [bid for bid, g in zip(sims[0].net.ids, sims[0].net.guaranteed) if g]
        idx = {bid: k for k, bid in enumerate(sims[0].net.ids)}
        premiums = {b: quote(np.array([o.refunds_annual_b[idx[b]] for o in ops[d]]), b).premium_usd for b in gids}
        results = [economics(s, o, terms, premiums) for s, o in zip(sims, ops[d])]
        npv = {p.id: np.array([r[p.id].npv_usd for r in results]) for p in cfg.parties}
        p_ahead = {p: float((v >= 0).mean()) for p, v in npv.items()}
        return terms, p_ahead, {p: float(np.median(v)) for p, v in npv.items()}, premiums

    grid = []
    has_grant = any(p.id == "public_housing" for p in cfg.parties)
    grants = sorted({base.grant_share, *GRANT_SHARES}, reverse=True) if has_grant else [base.grant_share]
    candidates = sorted(((d, g, y) for d in discount_opts for g in grants for y in PHASE_DELAYS),
                        key=lambda c: ((c[0] is not None) + (c[1] != base.grant_share) + (c[2] != 0),
                                       abs((c[0] or 0) - (1 - max(base.heat_price_fraction.values(), default=0.85)))))
    best = None
    for d, g, y in candidates:
        terms, p_ahead, med, prem = evaluate(d, g, y)
        worst = min(p_ahead.values())
        score = (worst, sum(v >= threshold for v in p_ahead.values()), float(np.mean(list(p_ahead.values()))))
        grid.append({"discount": d, "grant_share": g, "phase_delay_years": y, "min_p_ahead": worst})
        if best is None or score > best[1]:
            best = ((d, terms, p_ahead, med, prem), score)
        if worst >= threshold:
            break
    (d, terms, p_ahead, med, prem), (worst, _, _) = best
    binding = sorted(p for p, v in p_ahead.items() if v < threshold)
    gap = {}
    for p in binding:
        rate, horizon = party_finance(cfg, p)
        gap[p] = round(max(-med[p], 0.0) * crf(rate, horizon), 2)
    return DealReport(
        site=site, cleared=worst >= threshold, terms=terms, baseline_terms=base, p_ahead=p_ahead,
        median_npv_usd=med, premiums_usd=prem, changes=_describe(base, terms, d),
        binding_parties=binding, gap_usd_per_yr=gap, n_futures=n_futures, candidates_tried=len(grid), grid=grid)
