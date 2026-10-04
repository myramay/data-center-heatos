# HeatOS model

Two sites, one engine. Plain Python for the model, plain HTML/JS for the pages, one JSON file per site between them.

```bash
cd heatos
.venv/bin/python data_load.py        # Chelsea: real buildings within 1 km of 111 8th Ave (cached)
.venv/bin/python realdata.py         # weather, hourly load shapes, prices, equity, monthly bills (Chelsea)
.venv/bin/python realdata.py lansing # same for Lansing
.venv/bin/python lansing_data.py     # Lansing: parcels + OpenStreetMap -> candidate customers
.venv/bin/python optimize.py chelsea # writes out/plan.json (about 8 minutes: Monte Carlo + scenarios + deal search)
.venv/bin/python optimize.py lansing # writes out/plan_lansing.json
.venv/bin/python -m http.server 8765 # then open http://localhost:8765/web/index.html  (?site=lansing for the other site)
```

The page: `web/index.html` (3D map and controls), `web/proposal.html?site=chelsea|lansing` (the system proposal, writes itself from the JSON; Download PDF prints it),
`web/offer.html` (one-page offers). `python optimize.py chelsea lansing --refresh` re-exports both plans in seconds from their saved Monte Carlo results.

Where things live: `config.py` (every assumption, with a source note; `SITE_OVERRIDES` for Lansing), `sites.py` (place definitions),
`demand.py` (hourly demand), `network.py` (pipe tree on real streets), `optimize.py` (the plan), `allocate.py` (tiers + waterfall),
`system.py` (flow, pipe sizes, pumping, losses, bill of materials, monthly operation, energy balance), `reliability.py` (cooling protection, continuity, five-way match), `ledger.py` (who pays, who gains), `montecarlo.py`, `SCHEMA.md` (the JSON format).

## Data

`data/raw/` (NYC datasets) and `data/hackathon/` (the hackathon data pack) are too large for git and are ignored. `data/cache/` holds the small cleaned tables so the model runs without them.
To rebuild from scratch, put the raw files in `data/raw/` and the data pack in `data/hackathon/data/`, then run the commands above. Install with `python -m venv .venv && .venv/bin/pip install -r requirements.txt`.
