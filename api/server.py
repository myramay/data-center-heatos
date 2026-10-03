"""HeatOS API: REST control + WebSocket stream (one JSON frame per simulated hour).

    uvicorn api.server:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import time
import uuid
import warnings
from datetime import datetime
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from api.live import LiveRun, bundle, deal_report
from engine import providers
from engine.autopilot import compare_autopilots
from engine.contracts import SiteId
from engine.explain_tree import explain_building, site_tree
from engine.impact import impact
from engine.recommend import plan_details
from engine.report import build_report, to_html, to_pdf
from engine.scenarios import scenarios_for
from engine.site_scoring import site_scores

warnings.filterwarnings("ignore", category=UserWarning, module="cvxpy")

app = FastAPI(title="HeatOS", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

RUNS: dict[str, LiveRun] = {}
SUBSCRIBERS: dict[str, set[WebSocket]] = {}
TASKS: dict[str, asyncio.Task] = {}
MAX_RUNS = 6


class RunRequest(BaseModel):
    site: SiteId = "chelsea"
    start: datetime | None = None
    autopilot: bool = False
    hours: int = Field(default=168, ge=24, le=8760)
    speed: float = Field(default=10.0, ge=1, le=100)
    autostart: bool = True


class ScenarioRequest(BaseModel):
    run_id: str
    name: str


class AutopilotRequest(BaseModel):
    run_id: str
    on: bool


class SpeedRequest(BaseModel):
    run_id: str
    multiplier: float = Field(ge=1, le=100)


class PauseRequest(BaseModel):
    run_id: str
    paused: bool


def _run(run_id: str) -> LiveRun:
    if run_id not in RUNS:
        raise HTTPException(404, f"unknown run {run_id}")
    return RUNS[run_id]


def _site_of(building_id: str) -> SiteId:
    return "lansing" if building_id.upper().startswith("LA") else "chelsea"


async def _broadcast(run_id: str, msg: dict) -> None:
    dead = []
    for ws in list(SUBSCRIBERS.get(run_id, ())):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        SUBSCRIBERS[run_id].discard(ws)


async def _loop(run: LiveRun) -> None:
    loop = asyncio.get_running_loop()
    conf_task: asyncio.Future | None = None
    while not run.sim.done:
        if run.paused:
            await asyncio.sleep(0.1)
            continue
        t0 = time.perf_counter()
        if run.needs_confidence() and (conf_task is None or conf_task.done()):
            conf_task = loop.run_in_executor(None, run.compute_confidence)
        frame = await loop.run_in_executor(None, run.step_frame)
        await _broadcast(run.run_id, frame)
        await asyncio.sleep(max(0.0, 1.0 / run.speed - (time.perf_counter() - t0)))
    await _broadcast(run.run_id, {"type": "end", "run_id": run.run_id})


# ===================================================================== control

@app.get("/health")
def health():
    return {"ok": True, "runs": len(RUNS), "providers": {k: ("mock" if c.is_mock else c.name)
                                                        for k, c in providers.model_cards().items()}}


@app.post("/run")
async def start_run(req: RunRequest):
    if len(RUNS) >= MAX_RUNS:
        oldest = next(iter(RUNS))
        await stop_run(oldest)
    run_id = uuid.uuid4().hex[:10]
    run = await asyncio.get_running_loop().run_in_executor(
        None, lambda: LiveRun(run_id, req.site, req.start, req.hours, req.autopilot, req.speed))
    run.paused = not req.autostart
    RUNS[run_id] = run
    SUBSCRIBERS[run_id] = set()
    TASKS[run_id] = asyncio.create_task(_loop(run))
    return {"run_id": run_id, "site": req.site, "hours": run.sim.hours, "start": run.sim.inp.times[0].isoformat(),
            "autopilot": run.sim.policy.name}


@app.delete("/run/{run_id}")
async def stop_run(run_id: str):
    task = TASKS.pop(run_id, None)
    if task:
        task.cancel()
    RUNS.pop(run_id, None)
    SUBSCRIBERS.pop(run_id, None)
    return {"stopped": run_id}


@app.post("/scenario")
async def fire_scenario(req: ScenarioRequest):
    run = _run(req.run_id)
    try:
        with run.sim.lock:
            events = run.sim.apply_scenario(req.name)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    run.mark_dirty()
    await _broadcast(req.run_id, {"type": "events", "events": events})
    return {"events": events, "active": run.sim.active_scenarios}


@app.post("/autopilot")
def set_autopilot(req: AutopilotRequest):
    run = _run(req.run_id)
    with run.sim.lock:
        run.sim.set_policy("mpc" if req.on else "rules")
    run.mark_dirty()
    return {"autopilot": run.sim.policy.name}


@app.post("/speed")
def set_speed(req: SpeedRequest):
    run = _run(req.run_id)
    run.speed = req.multiplier
    return {"speed": run.speed}


@app.post("/pause")
def set_pause(req: PauseRequest):
    run = _run(req.run_id)
    run.paused = req.paused
    return {"paused": run.paused}


@app.websocket("/stream/{run_id}")
async def stream(ws: WebSocket, run_id: str):
    await ws.accept()
    if run_id not in RUNS:
        await ws.send_json({"type": "error", "message": f"unknown run {run_id}"})
        await ws.close()
        return
    SUBSCRIBERS[run_id].add(ws)
    await ws.send_json(RUNS[run_id].hello())
    try:
        while True:
            await ws.receive_text()          # keepalive / ignored
    except WebSocketDisconnect:
        SUBSCRIBERS.get(run_id, set()).discard(ws)


# ===================================================================== data

@app.get("/bundle")
async def get_bundle(site: SiteId = "chelsea"):
    return await asyncio.get_running_loop().run_in_executor(None, bundle, site)


@app.get("/buildings")
def get_buildings(site: SiteId = "chelsea"):
    return [b.model_dump() for b in providers.get_buildings(site)]


@app.get("/plan")
def get_plan(site: SiteId = "chelsea"):
    return plan_details(site).to_dict()


@app.get("/explain/{building_id}")
def get_explain(building_id: str, site: SiteId | None = None):
    try:
        return explain_building(site or _site_of(building_id), building_id)
    except KeyError:
        raise HTTPException(404, f"unknown building {building_id}")


@app.get("/tree")
def get_tree(site: SiteId = "chelsea"):
    return site_tree(site).to_json()


@app.get("/site-scores")
def get_site_scores():
    return site_scores()


@app.get("/scenarios")
def get_scenarios(site: SiteId = "chelsea"):
    return [{"name": s.name, "label": s.label, "hours": s.hours, "severity": s.severity} for s in scenarios_for(site)]


@app.get("/deal")
async def get_deal(site: SiteId = "chelsea"):
    from api.live import _jsonable
    return _jsonable(await asyncio.get_running_loop().run_in_executor(None, deal_report, site))


@lru_cache(maxsize=32)
def _compare(site: SiteId, scenario: str | None, hours: int):
    from api.live import DEFAULT_START, _jsonable
    start = datetime(2026, 7, 10) if scenario == "heat_wave" else DEFAULT_START[site]
    return _jsonable(compare_autopilots(site, start, hours, scenario))


@app.get("/compare")
async def get_compare(site: SiteId = "chelsea", scenario: str | None = None, hours: int = Query(120, ge=48, le=336)):
    return await asyncio.get_running_loop().run_in_executor(None, _compare, site, scenario, hours)


@app.get("/alternatives")
async def get_alternatives(site: SiteId = "chelsea"):
    from api.live import _jsonable
    from engine.alternatives import compare
    return _jsonable(await asyncio.get_running_loop().run_in_executor(None, compare, site))


@app.get("/impact")
async def get_impact(run_id: str):
    from api.live import _jsonable
    run = _run(run_id)
    if run.sim.h == 0:
        raise HTTPException(409, "run has not started")

    def calc():
        with run.sim.lock:
            return _jsonable({"impact": impact(run.sim, run.sim.results()), "running": run.running})
    return await asyncio.get_running_loop().run_in_executor(None, calc)


@app.get("/report")
async def get_report(run_id: str | None = None, site: SiteId | None = None, format: str = "md"):
    run = RUNS.get(run_id) if run_id else None
    if run_id and run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    the_site = run.site if run else (site or "chelsea")

    def make():
        from engine.contracts import ConfidenceResult
        conf = ConfidenceResult.model_validate(run.confidence) if run and run.confidence else None
        return build_report(the_site, live_sim=run.sim if run else None, confidence=conf, deal=deal_report(the_site))

    md = await asyncio.get_running_loop().run_in_executor(None, make)
    if format == "md":
        return PlainTextResponse(md, media_type="text/markdown")
    if format == "html":
        return HTMLResponse(to_html(md))
    if format == "pdf":
        pdf = await asyncio.get_running_loop().run_in_executor(None, to_pdf, md)
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="heatos_{the_site}_report.pdf"'})
    raise HTTPException(400, "format must be md, html or pdf")
