import pytest

from ml import team_data

pytestmark = pytest.mark.skipif(not team_data.available("chelsea"), reason="team output (out/plan.json) not present")


@pytest.mark.parametrize("site,prefix", [("chelsea", "CH-"), ("lansing", "LA-")])
def test_team_inventory_maps_to_contract(site, prefix):
    bs = team_data.TeamBuildingProvider().get_buildings(site)
    assert len(bs) > 50
    assert all(b.id.startswith(prefix) and b.site == site for b in bs)
    assert len({b.id for b in bs}) == len(bs)
    assert all(b.annual_heat_mwh > 0 and b.floor_area_m2 > 0 for b in bs)


def test_chosen_buildings_keep_measured_values():
    import json
    plan = json.loads(team_data.PLAN["chelsea"].read_text())
    chosen = next(b for b in plan["buildings"] if b.get("chosen") and b.get("heat_demand_mwh_year"))
    b = next(x for x in team_data.TeamBuildingProvider().get_buildings("chelsea") if x.id == f"CH-{chosen['bbl']}")
    assert b.annual_heat_mwh == pytest.approx(chosen["heat_demand_mwh_year"], rel=1e-3)


@pytest.mark.parametrize("site", ["chelsea", "lansing"])
def test_overlay_has_routes_and_assets(site):
    o = team_data.team_overlay(site)
    assert o["pipes"] and all(len(p["points"]) >= 2 for p in o["pipes"])
    assert o["chosen"]
    if site == "chelsea":
        assert any(a["type"] == "public_housing" for a in o["infrastructure"])


def test_engine_plans_on_team_inventory():
    from engine.config import load_site
    from engine.recommend import build_plan, supply_budget_kw
    bs = team_data.TeamBuildingProvider().get_buildings("chelsea")
    d = build_plan("chelsea", bs)
    assert d.plan.connected_ids() and d.used_kw <= supply_budget_kw(load_site("chelsea")) + 1e-6
