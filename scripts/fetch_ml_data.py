"""Download every public dataset the team's ML pipeline reads into heat-reuse-data/data/.

    .venv/bin/python -m scripts.fetch_ml_data          # ~1 GB, skips files already present

Then train: .team-venv/bin/python combine_site1.py && .team-venv/bin/python heat_models.py
"""

from pathlib import Path

from scripts.ml_data import nrel_weather, nyc, nystate

OUT = Path(__file__).resolve().parents[1] / "heat-reuse-data" / "data"

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for mod in (nyc, nystate, nrel_weather):
        print(f"== {mod.__name__}")
        for line in mod.fetch(OUT) or []:
            print("  ", line)
