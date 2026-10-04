"""Transport-method comparison and third-party list reach the bundle (skipped without the team outputs)."""

import pytest

from ml import team_data

pytestmark = pytest.mark.skipif(not team_data.TRANSPORT.exists(), reason="outputs/transport/options_summary.csv not present")


@pytest.mark.parametrize("site,best", [("chelsea", "4gdh_hot_water"), ("lansing", "greenhouse_direct")])
def test_transport_options_rank_and_label(site, best):
    t = team_data.transport_options(site)
    assert t["best"]["cost-optimal"] == best
    assert all(r["label"] != r["option"] for r in t["options"])       # every method has a readable label


def test_third_parties_come_from_repo_data():
    names = {c["company"] for c in team_data.transport_companies("chelsea")}
    assert "Con Edison" in names and "NYCHA" in names
    assert any(c["company"] == "Cornell University" for c in team_data.transport_companies("lansing"))
