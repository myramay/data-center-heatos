# HeatOS: accuracy check, deliverable check, and how to win

All numbers below come from `out/plan.json` (run `python optimize.py`). Costs are still placeholders.

## 1. Is the model and map accurate?

| Piece | Status | Source |
|---|---|---|
| Building footprints and heights | Accurate | NYC Building Footprints |
| Street network, street distances | Accurate | OpenStreetMap (OSMnx) |
| Year built, class, floor area, owner | Accurate | MapPLUTO 26v2 |
| Yearly fuel use per building | Self-reported, mostly good. A few outliers are excluded (3 buildings above 300 kBtu/ft²) | LL84, calendar 2024 |
| Month-by-month heat use | **Measured** for 91% of chosen buildings | LL84 monthly bills (2024) |
| Hot water share (summer baseload) | **Measured** from July/August bills; NREL profile when no bills | LL84 monthly, NREL |
| Hours of the day | Real NYC end-use shapes | NREL ComStock / ResStock |
| Weather (degree-days, temperatures) | Real, 2015-2024 average | NOAA Central Park |
| Fuel and electricity prices | State averages for 2021, scaled x1.25. **Stale and statewide** | data.ny.gov |
| Heat pump efficiency | Computed each month from the temperature lift (27 C source, 60 C hot water, reset curve for radiators) | physics, Carnot x 0.45 |
| Heat supply | **Derived**: 141 GWh/yr grid electricity -> 13.2 MW in the computers -> 10.6 MW of captured heat | 111 8th Ave LL84 |
| Equity | Public housing (owner name) + NY State disadvantaged-community tract | PLUTO, DAC 2023 |
| Pipe cost, heat pump cost, tie-in cost | **Placeholders** | config.py |
| LL97 limits and coefficients | **From memory. Verify** | config.py |
| Buildings with no LL84 data | Estimated, 0 chosen | estimate.py |
| Pipe layout | Shortest-path tree on streets. Not the optimal (Steiner) network | network.py |
| Lateral pipes | Straight line to nearest intersection x1.3 | network.py |

Biggest weak spots, in order:
1. **430 W 17th St is 81% of the plan's value.** Its own monthly bills were not in the data, so its seasonal shape is modeled. LL84 floor area (1.25M ft²) is 4x the PLUTO lot area, so it may bundle several buildings. Verify this one building first.
2. **The data center supply is an estimate.** It assumes 16 kWh/ft² for the office floors and an 80% capture fraction. The real answer needs the operator, or a submeter.
3. **Prices are old and statewide.** Gas at about $9.6/MMBtu is low for Con Ed. Cheap gas shrinks the savings for gas buildings, so the project leans on LL97 fines and oil/steam users.
4. **Pipe cost.** A $6,000/m placeholder sets the whole economics (see section 3).

## 2. Is this the right deliverable? Gaps against the brief

The brief wants a **concise, evidence-based system proposal** for **one** site. A map plus offers is only part of it.

| Brief requirement | Status |
|---|---|
| Select ONE site and justify | **Decide.** Our brainstorm covers Chelsea and Lansing. The brief says select one. I recommend Chelsea as the full answer (we have the data and the model) and Lansing as a single "transferability" slide at most. |
| Network architecture | Street pipe tree with building heat pumps (matches the 15-30 C loop idea). Needs a clean diagram. |
| Heat source: capture point, temperature, capacity | Capacity derived (10.6 MW). **Temperature and capture point need the operator or a stated assumption.** |
| Match by temperature, capacity, timing, seasonality, continuity | Capacity (every hour, 12 typical days), timing, seasonality: done. Temperature: COP by lift only. **Add a per-building fit table** (required supply temperature vs loop). Continuity: not yet. |
| Cooling reliability protected | **Missing.** Needs one slide and a rule: the data center keeps its own chillers and dry coolers; heat export is a second sink on the return water; if the network trips, rejection falls back automatically; no export is allowed to change the cooling set-point. |
| Backup heat | Every building keeps its boiler (in the offer). Needs a failure test (hour-by-hour). |
| Quantified value: data center | New: 55.9 GWh/yr of heat removed, 12.4 GWh/yr of cooling electricity freed, about 1.4 MW average and 2.2 MW at peak. |
| Quantified value: heat users | Done (offers). |
| Quantified value: community | CO2 (9,100 t/yr), LL97 fines, equity flags. **Add resident bill impact for public housing.** |
| Costs, risks, ownership shared across stakeholders | Ownership scenarios are in (below). **Per-party ledger (who pays, who gains, probability they win) is still to build.** |
| Carbon, efficiency, resource optimization | CO2 done. Grid carbon by hour is available in the NYISO fuel mix files but not wired in. |
| Lansing | Not started. Only needed if you choose both sites. |

## 3. What makes ours different (the "unique" angle)

Most teams will draw a heat network and list benefits. The finding most teams will not have:

> **Chelsea's heat network is an anchor-customer project, and who pays for the street pipes decides whether it is a 4-building or an 11-building network.**

| Who pays for the street pipes | Buildings | Yearly value | Heat used |
|---|---|---|---|
| HeatOS pays all | 4 | $4.6M | 63% |
| 50% grant | 7 | $4.9M | 68% |
| Utility rate-base (Con Ed under the UTEN Act) | 11 | $5.6M | 70% |

- Without the anchor customer the small buildings cannot pay for pipes. If it leaves, 81% of the value goes.
- So the sponsor's question "ownership, risks and responsibilities" has a numeric answer.
- Pair this with: year-round hot-water customers chosen from measured summer bills (3 of the 4 chosen buildings have a 39-70% hot-water share, against a median of 28% for all buildings nearby), COP computed from temperature lift, a Monte Carlo (60 scenarios: $3.4M-$6.1M/yr, 3-7 buildings) and stress tests.

Things from your brainstorm worth building next because nobody else will have them:
1. **Heat guarantee priced by confidence** (uses our Monte Carlo).
2. **"Everyone wins?" ledger**: per-party cash flow and probability of coming out ahead (data center, building, utility, state).
3. **Self-writing report** from `plan.json` (every number traceable).
4. **Cooling-reliability firewall** with an hour-by-hour failure test.

## 4. Claims in the brainstorm to verify before presenting

- "Chelsea 4.00 vs Lansing 3.35, robust in 97% of runs": whose model and what scale?
- "CBS found this cuts losses 3-4x": cite the source.
- "Google (data center)": LL84 calls the property "111 Eighth Avenue" and the brief calls it a multi-tenant carrier hotel. Do not present it as only Google's.
- "$150B of projects blocked or delayed in 2025 (Grundfos)": cite the slide.
- Steam heat pump: high-temperature heat pumps exist, but making building-scale steam is early-stage. Present it as a pilot, not a given. Only 19 of the 685 benchmarked lots near the site use district steam.
- UTEN Act: real (NY Utility Thermal Energy Network and Jobs Act). Check what it actually lets Con Ed rate-base.

## 5. The repositories you linked

I have **not** downloaded them. Use:
- **asi-trace-power / Google cluster-data / Azure dataset**: for how much the data center's power (and so heat) varies by hour. For a multi-tenant carrier hotel the swing is small (idle servers still draw about half of peak power), so expect +/-5%. I currently use +/-4% (`dc_load_swing`).
- **AWS data center cooling dataset**: for the supply temperature of the cooling loop.
- Rachel and Annie own this per your task split. The model has one hook, `dc_load_swing` and `dc_heat_capture_fraction` in `config.py`, to plug their result into.

## 6. Suggested next steps (in order)
1. Verify 430 W 17th St (LL84 area, monthly bills).
2. Ask the operator (or make a clear assumption) for the capture point and temperature of the heat.
3. Replace placeholder pipe and heat pump costs with quotes.
4. Build the per-party ledger and cooling-reliability slide.
5. Pick one site.

## 7. Update: how the heat is given out (the unique part)

Not "connect everyone". HeatOS gives heat out on a **priority ladder** with a **fit score**:

| Tier | Who | Cut when heat is short? | Price vs standard |
|---|---|---|---|
| PROTECTED | public housing | never | 5% cheaper (equity) |
| FIRM | hospitals, senior living | never | 5% more (guarantee premium) |
| BASE | year-round hot-water users | after FLEX | standard |
| FLEX | space-heating users | first | 5% cheaper (curtailable) |

Base case is now **Con Ed owns the street pipes** (UTEN model, the team's Chelsea architecture): 9 buildings, 4.1 km of pipe, 73 GWh/yr delivered. Value is $5.2M/yr before the network cost and $3.1M/yr after paying for the pipes in full. HeatOS-pays-all gives 4 buildings and 9,100 t CO2; the rate-base case gives 9 buildings and 10,700 t.
If the data center's output drops 30%, PROTECTED and FIRM keep 100% of their heat, BASE keeps about 68% and FLEX about 32% (their boilers take over).
The 'Who gets heat first' panel on the map shows this hour by hour, for Normal, Polar vortex and Data center -30%.
