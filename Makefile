PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: dev install seed test sync backfill demo clean

## Run the dashboard (mock data unless LIVE_DATA=1 in .env)
dev: install seed
	.venv/bin/uvicorn app.main:app --reload --port 8000

## Create the virtualenv and install dependencies
install: .venv/.installed

.venv/.installed: requirements.txt
	test -d .venv || python3 -m venv .venv
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r requirements.txt
	touch .venv/.installed

## Load deterministic fixture data into the database (safe to re-run)
seed: install
	$(PY) -m app.fixtures

## Run the metric calculation tests
test: install
	.venv/bin/pytest -q

## Incremental sync of the last 28 days from the live APIs
sync: install
	$(PY) -m app.sync --days 28

## Initial 90-day backfill from the live APIs
backfill: install
	$(PY) -m app.sync --days 90 --backfill

## Export a sanitised static demo to docs/ for public hosting
demo: install
	$(PY) -m app.export

## Export the CLIENT-BRANDED build (real name and benchmarks) to client-build/
## Gitignored on purpose: this must never reach the public Pages site.
client-demo: install
	DEMO_MODE=0 DEMO_OUT=client-build $(PY) -m app.export

## Export and publish the sanitised demo to GitHub Pages (needs `gh auth login`)
publish: install
	./scripts/publish_demo.sh

clean:
	rm -rf .venv data docs __pycache__ app/__pycache__ .pytest_cache
