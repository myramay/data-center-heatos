# HeatOS

> Data center heat that neighbors can count on, with a price for that certainty.

Decision support and live control room for reusing data center waste heat
(NYU Grundfos/HDR "Data Center Heat Reuse" challenge). HeatOS does not design
new hardware: it chooses the most cost- and carbon-efficient way to deliver
heat using existing, commercially available equipment (heat exchangers, heat
pumps, thermal storage, each building's existing boiler as backup), then
operates and prices that network. Two sites: **Chelsea** (111 8th Ave,
ambient loop, recommended) and **Lansing** (Lake Hawkeye, warm loop).

## Status

- [x] Step 1: interface contracts, mock ML providers, site configs
- [x] Step 2: physics, rules autopilot, simulator (a full year in about 0.1 s; energy balance checked every hour)
- [x] Step 3: stress tests + live narration, who-pays ledger, guarantees + "Everyone wins?" deal search, impact, site scoring (4.00 vs 3.35, Chelsea wins ~97%)
- [ ] Steps 4-10: API, front end, recommendation, MPC, Lansing polish, report

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
engine/physics.py     COPs, pipe losses, storage, pumping, hourly energy balance check
engine/autopilot.py   dispatch policies (rules now, MPC in step 7)
engine/sim.py         orchestrator: plan -> hour-by-hour autopilot + physics -> results / frames
engine/recommend.py   plan builder (capacity-aware placeholder until step 6)
engine/scenarios.py   stress tests (edit inputs from "now") + event narrator
engine/ledger.py      hourly money flows, capex by party, NPV / payback, Sankey data
engine/guarantees.py  guarantee premiums (E + CVaR95 margin), "Everyone wins?" lever search
engine/futures.py     engine FutureSimulator: one sampled future -> physics + ledger
engine/impact.py      CO2 / water / ERF / ERE ranges + HDR regenerative scorecard
engine/site_scoring.py  Deliverable 1 weighted criteria + Dirichlet robustness
ml/README.md          integration guide for the ML team
```

ML team: see [ml/README.md](ml/README.md). Every hook is tagged `ML_TEAM_INTEGRATION`.
