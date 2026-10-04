PY := .venv/bin/python

.PHONY: setup test dev demo api web record ml-data ml-models

setup:
	uv python install 3.11
	uv venv --python 3.11 .venv
	uv pip install -e ".[dev]"
	cd web && npm install

test:
	$(PY) -m pytest -q

api:
	.venv/bin/uvicorn api.server:app --port 8000 --reload

web:
	cd web && npm run dev

# backend + frontend together (Ctrl-C stops both)
dev:
	@trap 'kill 0' INT TERM; .venv/bin/uvicorn api.server:app --port 8000 & (cd web && npm run dev) & wait

# presentation mode: a built copy of the control room (no hot reload) + the API
demo:
	cd web && npm run build
	@trap 'kill 0' INT TERM; .venv/bin/uvicorn api.server:app --port 8000 & (cd web && npx vite preview --port 5173) & wait

record:
	$(PY) -m scripts.record

# public datasets for the team ML pipeline (~1 GB into heat-reuse-data/data, git-ignored)
ml-data:
	$(PY) -m scripts.fetch_ml_data

# retrain the team's demand / supply models (needs .team-venv: Python 3.14 + requirements.txt);
# COMPUTE_TRACES=multi trains the compute-load model on every operator's traces (compute_multi.py)
ml-models:
	.team-venv/bin/python combine_site1.py
	COMPUTE_TRACES=multi .team-venv/bin/python heat_models.py
