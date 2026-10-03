# HeatOS

> Data center heat that neighbors can count on, with a price for that certainty.

Decision support and live control room for reusing data center waste heat
(NYU Grundfos/HDR "Data Center Heat Reuse" challenge). Two sites: **Chelsea**
(111 8th Ave, ambient loop, recommended) and **Lansing** (Lake Hawkeye, warm
loop).

## Status

Build step 1 of 10 is complete: interface contracts, mock ML providers, and
site configs.

## Run

```bash
make setup   # Python 3.11 venv via uv
make test
```

## Layout

```
engine/contracts.py   pydantic interface with the ML team (mirrored in web/src/types.ts)
engine/providers.py   registry: the ONLY place mock vs real providers are chosen
engine/mocks.py       synthetic buildings, weather, demand, supply, confidence, Jev
engine/config.py      YAML site config -> typed SiteConfig (every number has a source)
engine/sites/*.yaml   Chelsea, Lansing
engine/verdict.py     ACT / REVIEW / ESCALATE gate
ml/README.md          integration guide for the ML team
```

ML team: see [ml/README.md](ml/README.md). Every hook is tagged `ML_TEAM_INTEGRATION`.
