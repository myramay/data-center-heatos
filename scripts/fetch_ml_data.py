"""Download every public dataset the team's ML pipeline reads into heat-reuse-data/data/.

    .venv/bin/python -m scripts.fetch_ml_data          # ~2 GB, skips files already present

Then train: make ml-models
"""

from pathlib import Path

from scripts.ml_data import compute_traces, nrel_weather, nyc, nystate

OUT = Path(__file__).resolve().parents[1] / "heat-reuse-data" / "data"

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for mod in (nyc, nystate, nrel_weather):
        print(f"== {mod.__name__}")
        for line in mod.fetch(OUT) or []:
            print("  ", line)
    # other operators' power / GPU / CPU traces for the compute-load model (compute_multi.py)
    print("== scripts.ml_data.compute_traces")
    traces = OUT / "compute_traces"
    traces.mkdir(exist_ok=True)
    compute_traces.fetch(traces)
    compute_traces.harmonize(traces)
