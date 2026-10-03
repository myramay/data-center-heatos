PY := .venv/bin/python

.PHONY: setup test dev

setup:
	uv python install 3.11
	uv venv --python 3.11 .venv
	uv pip install -e ".[dev]"

test:
	$(PY) -m pytest -q

# Backend + frontend; filled in at build steps 4-5.
dev:
	@echo "make dev arrives with the API (step 4) and web app (step 5)"
