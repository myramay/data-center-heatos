from datetime import datetime

import numpy as np
import pytest

from engine import providers
from engine.guarantees import everyone_wins, quote
from engine.impact import CATEGORIES, impact
from engine.ledger import DealTerms, compute_ledger
from engine.recommend import quick_plan
from engine.scenarios import SCENARIOS, scenarios_for
from engine.sim import Simulation
from engine.site_scoring import site_scores

WINTER = datetime(2026, 1, 12)
YEAR = datetime(2026, 1, 1)


@pytest.fixture(scope="module")
def year_runs():
    out = {}
    for site in ("chelsea", "lansing"):
        sim = Simulation(site, start=YEAR, hours=8760, narrate=False)
        out[site] = (sim, sim.run())
    return out


def _fire(site, name, start=WINTER, hours=240, warmup=24):
    sim = Simulation(site, start=start, hours=hours)
    sim.run(warmup)
    sim.apply_scenario(name)
    return sim, sim.run()


# ------------------------------------------------------------------ scenarios

def test_every_site_has_its_stress_tests():
    assert {s.name for s in scenarios_for("chelsea")} == {
        "polar_vortex", "tenant_leaves", "server_outage", "heat_wave", "price_spike"}
    assert {s.name for s in scenarios_for("lansing")} >= {
        "lake_effect_cold_snap", "greenhouse_off_season", "bitcoin_price_crash"}


@pytest.mark.parametrize("site,name", [(s, n) for n, sc in SCENARIOS.items() for s in sc.sites])
def test_each_scenario_fires_and_narrates(site, name):
    start = datetime(2026, 7, 10) if name == "heat_wave" else WINTER
    sim, r = _fire(site, name, start=start)
    opening = [e for e in sim.events if e["kind"] == "scenario"]
    assert len(opening) == 1 and opening[0]["hour_index"] == 24
    assert r.flows.balance_error.max() <= 1e-3


def test_polar_vortex_pushes_buildings_to_backup():
    base = Simulation("chelsea", start=WINTER, hours=240).run()
    _, pv = _fire("chelsea", "polar_vortex")
    assert pv.summary["backup_heat_mwh"] > 3 * base.summary["backup_heat_mwh"]
    assert pv.summary["storage_discharged_mwh"] > base.summary["storage_discharged_mwh"]


def test_input_edits_match_spec():
    sim, _ = _fire("chelsea", "price_spike", hours=72)
    p = sim.cfg.prices.electricity_usd_per_mwh.value
    assert np.allclose(sim.inp.elec_usd_per_mwh[24:48], 3 * p) and np.allclose(sim.inp.elec_usd_per_mwh[48:], p)
    sim, r = _fire("chelsea", "server_outage", hours=48)
    assert r.supply_kw[24:30].sum() == 0 and r.supply_kw[30] > 0
    sim, _ = _fire("lansing", "bitcoin_price_crash", hours=400)
    assert (sim.inp.flex_available[24:360] == 0).all() and sim.inp.flex_available[360] == 1
    sim, _ = _fire("lansing", "greenhouse_off_season", hours=48)
    gh = [i for i, b in enumerate(sim.net.buildings) if b.use_type == "greenhouse"]
    base = Simulation("lansing", start=WINTER, hours=48)
    assert np.allclose(sim.inp.demand_kw[30, gh], 0.2 * base.inp.demand_kw[30, gh])


def test_heat_wave_sells_cooling():
    start = datetime(2026, 7, 10)
    base = Simulation("chelsea", start=start, hours=168).run()
    _, hw = _fire("chelsea", "heat_wave", start=start, hours=168)
    assert hw.summary["cooling_sold_mwh"] > base.summary["cooling_sold_mwh"]


def test_scenario_expires_with_recovery_event():
    sim, _ = _fire("chelsea", "server_outage", hours=48)
    assert "server_outage" not in sim.active_scenarios
    assert any(e["text"].startswith("Stress test over") for e in sim.events)


def test_scenario_guards():
    sim = Simulation("chelsea", hours=24)
    with pytest.raises(KeyError):
        sim.apply_scenario("meteor")
    with pytest.raises(ValueError):
        sim.apply_scenario("bitcoin_price_crash")


def test_confidence_dips_under_stress_using_live_run():
    sim = Simulation("chelsea", start=WINTER, hours=240, run_id="dip-test")
    sim.run(24)
    before = providers.get_confidence("chelsea", sim.plan, sim.state(), 40)
    sim.apply_scenario("polar_vortex")
    after = providers.get_confidence("chelsea", sim.plan, sim.state(), 40)
    assert after.expected_unmet_hours > before.expected_unmet_hours
    assert after.p_all_warm <= before.p_all_warm
    assert set(after.p_guarantee_kept) == set(sim.plan.guaranteed_ids())


# ------------------------------------------------------------------ ledger

def test_every_party_has_economics(year_runs):
    for site, (sim, r) in year_runs.items():
        led = compute_ledger(sim, r)
        assert set(led.parties) == {p.id for p in sim.cfg.parties}
        assert led.annualized_from == "full_year"
        for p in led.parties.values():
            assert len(p.cashflows) == p.horizon_years + 1
        sk = led.sankey()
        assert sk["links"] and all(l["value_usd"] > 0 for l in sk["links"])


def test_lansing_money_is_conserved(year_runs):
    """Party gains = buildings' avoided heat cost - real costs + avoided cooling + abatement."""
    sim, r = year_runs["lansing"]
    led = compute_ledger(sim, r)
    f = r.flows
    price = sim.inp.elec_usd_per_mwh / 1000
    cost = np.array([b.current_heat_cost_usd_per_mwh for b in sim.net.buildings]) / 1000
    system = ((f.delivered_kw * cost).sum() - (f.hp_elec_kw.sum(1) * price).sum() - (f.pump_kw * price).sum()
              + (r.dc_used_kw / sim.cfg.data_center.chiller_cop.value * price).sum()
              + sim.cfg.policy["tax_abatement_offset_usd_per_yr"].value)
    assert sum(p.window_operating_usd for p in led.parties.values()) == pytest.approx(system, rel=1e-6)


def test_premiums_are_a_pure_transfer(year_runs):
    sim, r = year_runs["chelsea"]
    gid = sim.plan.guaranteed_ids()[0]
    a = compute_ledger(sim, r)
    b = compute_ledger(sim, r, premiums={gid: 50_000.0})
    total = lambda led: sum(p.annual_operating_usd for p in led.parties.values())
    assert total(a) == pytest.approx(total(b), rel=1e-9)
    assert b.parties["guarantee_buyers"].annual_operating_usd == pytest.approx(
        a.parties["guarantee_buyers"].annual_operating_usd - 50_000.0)
    assert any(f.label == "guarantee premium" for f in b.flows)


def test_discount_moves_money_from_owner_to_offtakers(year_runs):
    sim, r = year_runs["lansing"]
    base = DealTerms.from_config(sim.cfg)
    lo, hi = compute_ledger(sim, r, base.with_discount(0.05)), compute_ledger(sim, r, base.with_discount(0.25))
    assert hi.parties["ag_schools"].npv_usd > lo.parties["ag_schools"].npv_usd
    assert hi.parties["joint_venture"].npv_usd < lo.parties["joint_venture"].npv_usd


def test_payback_semantics(year_runs):
    led = compute_ledger(*year_runs["chelsea"])
    assert led.parties["google"].payback_year == 0            # no capex, positive from day one
    assert led.parties["con_ed"].upfront_usd > 0


def test_short_window_extrapolates(year_runs):
    sim = Simulation("chelsea", start=WINTER, hours=48)
    led = compute_ledger(sim, sim.run())
    assert led.annualized_from == "heat_share_extrapolation"


# ------------------------------------------------------------------ guarantees + deal

def test_quote_is_expected_plus_cvar_margin():
    r = np.r_[np.zeros(95), np.full(5, 100.0)]
    q = quote(r, "x")
    assert q.expected_refunds_usd == pytest.approx(5.0) and q.premium_usd == pytest.approx(100.0)


def test_everyone_wins_lansing_clears():
    plan = quick_plan("lansing", providers.get_buildings("lansing"))
    rep = everyone_wins("lansing", plan, n_futures=6)
    assert rep.cleared and all(p >= 0.8 for p in rep.p_ahead.values()) and rep.changes == []


def test_everyone_wins_reports_binding_parties_when_it_cannot_clear():
    plan = quick_plan("chelsea", providers.get_buildings("chelsea"))
    rep = everyone_wins("chelsea", plan, n_futures=6)
    if not rep.cleared:
        assert rep.binding_parties and set(rep.gap_usd_per_yr) == set(rep.binding_parties)
        assert rep.candidates_tried > 1


# ------------------------------------------------------------------ impact + site scoring

def test_impact_ranges_and_sanity(year_runs):
    for site, (sim, r) in year_runs.items():
        im = impact(sim, r)
        c = im["net_co2_avoided_t"]
        assert c["low"] <= c["central"] <= c["high"]
        assert im["sanity_ok"], (site, im["net_co2_t_per_mw_yr"], im["sanity_band_t_per_mw_yr"])
        assert 0 < im["erf"] < 1 and im["ere"] < sim.cfg.data_center.pue.value
        assert [row["category"] for row in im["scorecard"]] == CATEGORIES
        assert all(-2 <= row["with_heatos"] <= 2 and row["with_heatos"] >= row["baseline"] for row in im["scorecard"])
        assert (im["water"] is not None) == sim.cfg.data_center.cooling_towers


def test_site_scores_reproduce_spec():
    s = site_scores()
    assert s["totals"] == {"chelsea": 4.0, "lansing": 3.35}
    assert s["robustness"]["p_chelsea_wins"] == pytest.approx(0.97, abs=0.01)
    assert sum(c["weight"] for c in s["criteria"]) == pytest.approx(1.0)
    assert s["recommended"] == "chelsea"
