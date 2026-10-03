import httpx

from engine.sim import Simulation
from ml.jev_openjev import OpenJevProvider, describe_state, questions


def _provider(handler):
    p = OpenJevProvider(url="http://jev.test", model="jev-latest", api_key="k")
    p._client = httpx.Client(transport=httpx.MockTransport(handler))
    return p


def _state(site="chelsea"):
    sim = Simulation(site, hours=24, narrate=False)
    sim.run(2)
    return sim.state()


def test_request_shape_and_parsing():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"model": "jev-latest", "answers": {
            "playbook": {"choice": "start_backup", "probabilities": {"start_backup": 0.81, "draw_storage": 0.19}, "confidence": 0.3},
            "supply_ok": {"noul": 0.93}}, "usage": {"input_tokens": 120}})

    op = _provider(handler).get_jev_opinion(_state())
    assert op.available and op.playbook == "start_backup"
    assert op.playbook_probability == 0.81 and op.p_supply_meets_guarantees == 0.93
    assert seen["auth"] == "Bearer k" and '"type": "choice"' in seen["body"].replace('":"', '": "') or '"choice"' in seen["body"]


def test_unreachable_server_means_unavailable():
    def handler(req):
        raise httpx.ConnectError("refused")
    p = _provider(handler)
    op = p.get_jev_opinion(_state())
    assert not op.available and "ConnectError" in p.last_error


def test_http_error_means_unavailable():
    op = _provider(lambda req: httpx.Response(400, text="Unknown model")).get_jev_opinion(_state())
    assert not op.available


def test_questions_only_offer_feasible_playbooks():
    lansing = questions(_state("lansing"))["playbook"]["criteria"]
    assert "start_steam_hp" not in lansing and "curtail_cooling_export" not in lansing
    assert "Lansing" in describe_state(_state("lansing"))
