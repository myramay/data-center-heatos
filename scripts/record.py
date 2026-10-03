"""Record demo runs for Replay mode (UI works with the backend off).

    python -m scripts.record            # all sites and stress tests
    python -m scripts.record chelsea    # one site

Writes recordings/<site>_bundle.json (static data + report) and
recordings/<site>_<scenario>.json (168 frames; stress test fired at hour 24).
Frames come from the same LiveRun code as the live WebSocket.
"""

from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning)

from api.live import LiveRun, _jsonable, bundle, deal_report  # noqa: E402
from engine.autopilot import compare_autopilots  # noqa: E402
from engine.contracts import ConfidenceResult  # noqa: E402
from engine.report import build_report  # noqa: E402
from engine.scenarios import scenarios_for  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "recordings"
HOURS = 168
FIRE_AT = 24


def record(site: str, scenario: str | None) -> dict:
    start = datetime(2026, 7, 10) if scenario == "heat_wave" else None
    run = LiveRun(f"rec-{site}-{scenario or 'baseline'}", site, start=start, hours=HOURS, speed=10)
    frames = []
    for h in range(HOURS):
        if scenario and h == FIRE_AT:
            run.sim.apply_scenario(scenario)
            run.mark_dirty()
        if run.needs_confidence():
            run.compute_confidence()
        if run.sim.h - run.jev_hour >= 3:
            run.jev_hour = run.sim.h
            run._refresh_jev(run.sim.state())
        frames.append(run.step_frame())
    return {"site": site, "scenario": scenario, "fire_at": FIRE_AT if scenario else None, "hours": HOURS,
            "start": run.sim.inp.times[0].isoformat(), "recorded_at": datetime.now().isoformat(), "frames": frames,
            "final_confidence": run.confidence}


def main(sites: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    index = json.loads((OUT / "index.json").read_text()) if (OUT / "index.json").exists() else {}
    for site in sites:
        t = time.perf_counter()
        b = dict(bundle(site))
        base = record(site, None)
        conf = ConfidenceResult.model_validate(base["final_confidence"]) if base["final_confidence"] else None
        b["report_md"] = build_report(site, confidence=conf, deal=deal_report(site))
        compare = {}
        for name in [None] + [sc.name for sc in scenarios_for(site)]:
            start = datetime(2026, 7, 10) if name == "heat_wave" else datetime(2026, 1, 12)
            compare[name or "baseline"] = _jsonable(compare_autopilots(site, start, 120, name))
        b["autopilot_compare"] = compare
        from engine.alternatives import compare as compare_alternatives
        b["alternatives"] = _jsonable(compare_alternatives(site))
        (OUT / f"{site}_bundle.json").write_text(json.dumps(_jsonable(b), separators=(",", ":")))
        (OUT / f"{site}_baseline.json").write_text(json.dumps(base, separators=(",", ":")))
        names = ["baseline"]
        for sc in scenarios_for(site):
            rec = record(site, sc.name)
            (OUT / f"{site}_{sc.name}.json").write_text(json.dumps(rec, separators=(",", ":")))
            names.append(sc.name)
        index[site] = names
        print(f"{site}: {len(names)} recordings in {time.perf_counter() - t:.0f}s", flush=True)
    (OUT / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or ["chelsea", "lansing"])
