PY := .venv/bin/python

.PHONY: setup test dev demo api web record

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
