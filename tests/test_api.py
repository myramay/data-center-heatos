import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.server import app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_static_endpoints(client):
    assert client.get("/health").json()["ok"]
    assert len(client.get("/buildings?site=chelsea").json()) == 40
    assert client.get("/plan?site=lansing").json()["plan"]["site"] == "lansing"
    assert client.get("/explain/CH-03").json()["building_id"] == "CH-03"
    assert client.get("/explain/NOPE").status_code == 404
    assert client.get("/tree?site=chelsea").json()["root"]["samples"] == 5000
    assert client.get("/site-scores").json()["totals"]["chelsea"] == 4.0
    assert {s["name"] for s in client.get("/scenarios?site=lansing").json()} >= {"bitcoin_price_crash"}


def test_bundle_shape(client):
    b = client.get("/bundle?site=chelsea").json()
    for key in ("config", "buildings", "plan", "pipes", "tree", "explanations", "scenarios", "site_scores", "deal", "annual"):
        assert key in b
    assert all(len(p["points"]) >= 2 for p in b["pipes"])


def test_live_run_stream_and_controls(client):
    r = client.post("/run", json={"site": "chelsea", "speed": 100, "hours": 48}).json()
    rid = r["run_id"]
    with client.websocket_connect(f"/stream/{rid}") as ws:
        assert ws.receive_json()["type"] == "hello"
        frames = []
        while len(frames) < 6:
            m = ws.receive_json()
            if m["type"] == "frame":
                frames.append(m)
        assert client.post("/scenario", json={"run_id": rid, "name": "server_outage"}).json()["active"] == ["server_outage"]
        assert client.post("/scenario", json={"run_id": rid, "name": "bitcoin_price_crash"}).status_code == 400
        assert client.post("/autopilot", json={"run_id": rid, "on": True}).json()["autopilot"] == "mpc"
        assert client.post("/speed", json={"run_id": rid, "multiplier": 20}).json()["speed"] == 20
    f = frames[-1]
    for key in ("time", "weather", "buildings", "data_center", "storage", "loop", "money", "guarantees", "margin",
                "impact_running", "events"):
        assert key in f
    assert len(f["buildings"]) >= 1
    assert client.get(f"/impact?run_id={rid}").status_code == 200
    md = client.get(f"/report?run_id={rid}&format=md").text
    assert "## 1. Decision framework" in md and "## 5. Implementation vision" in md
    client.delete(f"/run/{rid}")


def test_recordings_exist_for_every_scenario():
    index = json.loads((ROOT / "recordings/index.json").read_text())
    for site, names in index.items():
        assert (ROOT / f"recordings/{site}_bundle.json").exists()
        for n in names:
            rec = json.loads((ROOT / f"recordings/{site}_{n}.json").read_text())
            assert len(rec["frames"]) == rec["hours"]
