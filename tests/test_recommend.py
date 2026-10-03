import pytest

from engine import providers
from engine.config import load_site
from engine.explain_tree import explain_building, site_tree
from engine.recommend import build_plan, evaluate_building, plan_details, supply_budget_kw


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_plan_respects_npv_and_capacity(site):
    d = plan_details(site)
    assert d.used_kw <= supply_budget_kw(load_site(site)) + 1e-6
    for item in d.plan.items:
        if item.connect:
            assert item.npv_usd > 0 and item.phase in (1, 2, 3)
        else:
            assert item.reason_codes[0] in ("npv_negative", "capacity_limit")
            if item.reason_codes[0] == "npv_negative":
                assert item.npv_usd <= 0


def test_options_follow_site_rules():
    cfg = load_site("chelsea")
    for b in providers.get_buildings("chelsea"):
        opts = {o.option for o in evaluate_building(cfg, b).options}
        assert ("steam_hp" in opts) == (b.heating_system == "steam")
        assert ("direct_link" in opts) == (b.street_distance_m < 100)
    cfg = load_site("lansing")
    for b in providers.get_buildings("lansing"):
        opts = {o.option for o in evaluate_building(cfg, b).options}
        assert opts == ({"booster"} if b.use_type == "home" else {"direct_use"})


def test_carbon_value_is_counted():
    cfg = load_site("chelsea")
    b = next(x for x in providers.get_buildings("chelsea") if x.id == "CH-01")
    with_c, without = evaluate_building(cfg, b, use_carbon=True), evaluate_building(cfg, b, use_carbon=False)
    assert with_c.options[0].npv_value_usd > without.options[0].npv_value_usd


def test_phases_split_connected_set_and_guarantees_included():
    plan = plan_details("chelsea").plan
    phases = {i.phase for i in plan.items if i.connect}
    assert phases == {1, 2, 3}
    assert set(plan.guaranteed_ids()) == {"CH-14", "CH-17"}


def test_more_capacity_connects_more():
    small = build_plan("chelsea", providers.get_buildings("chelsea"))
    assert 3 <= len(small.order) <= 15


@pytest.mark.parametrize("site,bid", [("chelsea", "CH-03"), ("chelsea", "CH-08"), ("lansing", "LA-02")])
def test_explanations(site, bid):
    e = explain_building(site, bid)
    assert e["path"][0]["samples"] == 5000 and e["path"][-1]["leaf"]
    assert all(0 < n["confidence"] <= 1 for n in e["path"])
    assert all(n["answer"] in ("yes", "no") for n in e["path"][:-1])
    assert e["recommendation"]["building_id"] == bid and e["options"]


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_tree_quality_and_json(site):
    st = site_tree(site)
    assert st.train_accuracy > 0.8
    j = st.to_json()

    def depth(n):
        return 0 if n["leaf"] else 1 + max(depth(n["yes"]), depth(n["no"]))
    assert 2 <= depth(j["root"]) <= 5


def test_tree_mostly_agrees_with_plan():
    agree = [explain_building("chelsea", b.id)["agrees"] for b in providers.get_buildings("chelsea")]
    assert sum(agree) / len(agree) >= 0.7
