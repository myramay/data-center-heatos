# Methodology: heat transport options, optimization and confidence

Scripts, run in this order: `combine_site1.py` → `heat_models.py` → `transport_optimization.py`.
Results are in `outputs/transport/options_summary.csv`. Every Monte Carlo draw is in `mc_draws.csv`.

## 1. Inventory of heat transport options

| | Site 1: 111 8th Ave, Manhattan | Site 2: Lake Hawkeye, Lansing |
|---|---|---|
| **New networks** | 4th-gen hot water (65–70 °C), with a central heat pump at the data center | Same, to surrounding homes and schools |
| | 5th-gen ambient loop (~25 °C), with a heat pump in every building | Same, with home heat pumps |
| **Third-party / existing** | **Con Ed steam system:** 271 customers and 19% of heat within 2.2 km. Evaluated and rejected as a route (below). Kept as incumbent and backup. | **Cornell University central plant** (~21 km straight line, ~30 km route): transmission main to a third-party anchor |
| | **Campus central plants:** 10 campuses that each need one connection, e.g. NYCHA Fulton (286 m), Penn South cogeneration (814 m), Elliott-Chelsea, NYU and Hudson Yards cogeneration | **NYSEG gas, plus trucked oil and propane:** the incumbents (33% of homes on gas, 47% on oil or propane) |
| **Non-pipe** | Not viable in Manhattan (trucks) | **Mobile heat-battery containers** trucked to distant schools and businesses |
| **New anchor** | — | **Greenhouse or aquaculture** on the adjacent agricultural district, heated either through a heat pump or **directly with ~45 °C liquid-cooling water** |

**Why Con Ed steam is rejected as a route:** steam distribution needs about 180 °C. Lifting 27 °C data center heat that far gives a heat-pump COP of only about 1.3, which means about $37/MMBtu in electricity alone, before any capital cost. Connecting to the steam system is also a matter of Con Ed's regulated tariff.

## 2. Inputs (from earlier steps)
- **Hourly demand for every building:** ML quantile model (`heat_models.py`) × LL84 measured or parcel-estimated annual heat, under 10 weather years (2015–2024).
- **Hourly supply:**
  - Site 1: regression on the building's real monthly electricity, with hourly variation learned from Google's 2019 power traces. 200 runs.
  - Site 2: Phase I (150 MW) scenarios for AI training, AI inference and bitcoin mining. 200 runs each.
- **Buyer's current cost of heat (X):** NYS energy prices ÷ appliance efficiency. Con Ed steam priced at $28–45/MMBtu (an assumption).
- **Carbon:** LL97 emission factors (NYC), EPA factors (Site 2), and grid factors as ranges.

## 3. Design: greedy network optimization
For each option, candidate buildings are ranked by *annual heat × current cost ÷ route distance from the site*. Each one is tested in turn:

```
incremental pipe = straight-line distance to the nearest already-connected node × street detour factor
marginal DC heat = Σ_hours min(available heat − pipe losses, connected demand + this building) − previous total
value            = marginal DC heat × (buyer's current cost + carbon price × emission factor)
cost             = annuitised (pipe + connection + in-building retrofit [+ building heat pump])
                   + O&M + heat-pump and pumping electricity
connect if value > cost
```

- Pipe losses are physical: W/m × route length, in every hour, so long routes cost supply as well as money.
- The design runs twice: **cost-optimal** (carbon at $0/t) and **carbon-weighted** (carbon at $190/t, the EPA social cost of carbon). Comparing them shows where carbon goals and cost pull in different directions.
- **Backup / peak boiler:** sized to carry the worst weather year's peak with the data center off (N-1), plus 10%. This is standard district-energy practice.

## 4. Who pays whom (applied in every draw)

| Party | Gains | Pays |
|---|---|---|
| **Heat buyer** | Discount (5–20%) × current cost × data center heat received; avoided LL97 penalties (NYC) | In-building retrofit |
| **Network operator** (utility, ESCO or co-op) | Tariff = (1 − discount) × buyer's current cost, on data center heat only | Pipes, heat pumps, connections, backup boiler, O&M, electricity, payment to the data center |
| **Data center** | $0–2 per MMBtu of source heat; cooling energy avoided | Heat-exchanger tie-in, sized to the heat actually exported |

Backup heat is passed through at the buyer's own cost and carbon. Every dollar and tonne in the results therefore comes from reused data center heat, not from swapping one fuel for another.

## 5. Monte Carlo (1,000 draws per option and objective)
Each draw independently samples:
- a weather year and a data center supply run (and a compute type at Site 2);
- demand error (±10% for measured buildings, ±40% for estimated ones);
- fuel and electricity prices, grid carbon factor, all capital and O&M costs, discount rate (4–8%), tariff discount and data center heat price;
- pipe failures: Poisson rate per km-year, each halving the data center heat delivered for 12–48 hours.

Each draw then re-dispatches the network for all 8,760 hours and records two outcomes:
- **P(everyone stays warm):** share of draws in which no connected building is short of heat in any hour (data center heat + backup ≥ demand).
- **P(every party profits):** share of draws in which the data center > 0, the operator > 0 **and every individual buyer** > 0, in annualized net terms.

All cost ranges are listed at the top of `transport_optimization.py`. They are planning-level and should be checked against the Danish Energy Agency technology catalogue, HDR cost data and utility tariffs. The Monte Carlo spreads any error across the plausible range rather than hiding it.

## 6. Results (cost-optimal design, P50 unless stated)

| Site / option | Users | Pipe | Capex | Cost vs tariff ($/MMBtu) | CO₂ cut | P(warm) | P(all profit) |
|---|---|---|---|---|---|---|---|
| **S1 4th-gen hot water** | 111 8th itself, NYCHA Fulton, Stuyvesant Town, a large healthcare steam user | 4.1 km | $62M | 28.9 vs 36.3 | 11,100 t/yr | 100% | **79%** |
| S1 campus anchor | 111 8th itself + NYCHA Fulton | 0.4 km | $17M | 27.6 vs 36.6 | 3,700 t/yr | 100% | **86%** |
| S1 5th-gen ambient | 3 | 2.9 km | $76M | 41.0 vs 37.0 | 8,200 t/yr | 100% | 34% |
| S2 greenhouse, direct 45 °C | new anchor | 0.6 km | $13M | 8.6 vs 15.8 | 13,500 t/yr | 95% | **45%** (≈65% with AI compute, 0% with bitcoin mining) |
| S2 greenhouse, via heat pump | new anchor | 0.6 km | $51M | 30.5 vs 15.9 | 13,300 t/yr | 94% | 0% |
| S2 trucked heat batteries | 6 schools and businesses | — | $6M | 65.5 vs 19.1 | 1,400 t/yr | 99% | 0% |
| S2 Cornell transmission | none at $0/t; at $190/t: 30 km | — | — | 24.6 vs 6.4 | 35,000 t/yr | 99% | 0% |
| S2 local 4th- or 5th-gen | none worth connecting (rural density too low) | — | — | — | — | — | — |

**What the results say**

**Site 1** works because it is limited by supply, not demand, and nearby buyers on Con Ed steam pay about $40/MMBtu. A short first phase is the most robust: 111 8th's own steam load plus NYCHA Fulton, 400 m of pipe, an 86% chance every party profits. NYCHA Fulton sits in a state-designated Disadvantaged Community. Extending about 2 km to Stuyvesant Town and the healthcare campus triples the carbon cut, but lowers P(all profit) to 79%. A 5th-gen loop costs more, because every building needs its own heat pump.

**Site 2** is limited by demand. Pipes to scattered homes never pay back. Cornell has the largest carbon potential (35,000 t/yr), but Cornell's own cogeneration heat is cheap (about $5–9/MMBtu), so no tariff covers 30 km of pipe. Only carbon policy or grants could change that. The only option that works on its own economics is to **co-locate a greenhouse that uses the AI liquid-cooling water directly**, and it depends on compute type. If TeraWulf runs air-cooled bitcoin mining, the heat is too cool (about 32 °C) and the case fails.

**Carbon versus cost:** the carbon-weighted design changes little at Site 1 and doesn't rescue Site 2. At Site 1, the carbon price pushes in Penn South (cheap gas heat), which cuts more CO₂ but makes the operator lose money (P(all profit) 0%). Where carbon and cost diverge, a subsidy or LL97 credit would have to bridge the gap.

## 7. Heat allocation and the 85% reuse target (`energy_distribution.py`)

**How much heat the data center produces.** All the electricity a data center uses ends up as heat. The heat it has to get rid of is:
- IT load,
- plus UPS and power-distribution losses,
- plus fans, pumps and lighting inside the data halls.

Chiller and dry-cooler compressor power is not counted, because when heat is recovered the heat pump does that cooling job instead.

**How much can realistically be taken.** Each step below is a range that is sampled in the Monte Carlo.

| Step | Site 1 (carrier-hotel retrofit) | Site 2 (new build) |
|---|---|---|
| Heat that reaches a water loop | Only halls on central chilled-water air handlers (CRAHs) count: 50–80% of the heat, × 90–97% picked up by the coils. Tenants' own refrigerant (DX) units can't be tapped. Electrical rooms: 30–60%. | AI: liquid cooling takes 70–85% of IT heat (95–99% recovered), rear-door coils 60–85% of the rest. Bitcoin: air-cooled, 40–70% captured with hot-aisle containment. |
| Source temperature | 18–24 °C chilled-water return (tenant agreements keep it cold) | Liquid 40–50 °C. Miner exhaust = outdoor air + 12–18 K. |
| Heat pumps | COP = 0.5 × Carnot on refrigerant temperatures (5 K and 3 K heat-exchanger approaches). Supply water 80 → 65 °C for steam-era buildings. | Same; supply water 70 → 55 °C. |
| Plant size and uptime | 4 units, sized to the smaller of (a) the 90th-percentile recoverable heat and (b) what users can take at their 99th-percentile hour. Each unit has 10 days of maintenance a year plus 1–3 forced outages of 1–5 days. | Same |
| Network | Pipe losses scale with water temperature, so they are higher in winter. Hot water can replace only 70% of a hospital's heat (sterilization and humidification need steam) and 90% of a clinic's. | Same |
| Storage | What fits in Manhattan: a 300–1,000 m³ tank, about 22 MWh or 2.4 h of output | Rural land: a 5,000–20,000 m³ tank, about 360–420 MWh |

Storage capacity = volume × 1.163 kWh/m³K × (supply − return temperature) × 90% (for mixing in the tank). It takes at least 4 hours to fill or empty, and loses 0.3–2% of its heat per day.

**Reuse rate** = data center heat that actually heats a building ÷ all heat the data center produced. This is the Energy Reuse Factor (ERF) in the international standard ISO/IEC 30134-6.
- For scale, Germany's Energy Efficiency Act (2023) requires new data centers to reach an ERF of 10% from 2026, rising to 20% from 2028.
- Buildings are connected in priority order until the network can absorb 88% (the 85% target plus a 3% margin). If the steps above cap reuse lower, the model connects up to the realistic maximum and reports the gap.
- A building is not connected if its own pipe would lose more than 25% of the heat it receives.

**Priority score** = 0.45 × social value + 0.30 × people served (log-scaled) + 0.15 × equity (state-designated Disadvantaged Community or public housing) + 0.10 × proximity.
- **Social value:** hospitals and care homes 1.0, schools 0.95, public housing 0.95, homes 0.8–0.85, offices 0.45, retail 0.4, nightlife and gyms 0.2, parking and storage 0.1–0.15.
- **People served:** residents (housing units × household size) or daily users (floor area ÷ floor area per person).

**Hourly allocation.**
- Heat goes to the four tiers in strict order:
  1. Critical (health, care, schools, public housing)
  2. Homes and civic buildings
  3. Commercial
  4. Discretionary
- When a tier can't be fully served, its buildings share the heat by weighted water-filling: each building gets min(1, θ × its score) of its need. Higher-priority buildings get a larger share, and nobody in the tier is cut to zero.
- Any shortfall is covered by the building's existing boiler or steam.

**Results** (40 Monte Carlo runs; median, with the P10–P90 range in brackets).

| | Site 1 | Site 2 AI training | Site 2 AI inference | Site 2 bitcoin |
|---|---|---|---|---|
| Heat produced | 95 GWh/yr (10.9 MW) | 1,088 GWh/yr | 954 GWh/yr | 1,108 GWh/yr |
| Reaches a water loop | 61% | 87% | 88% | 51% |
| Ceiling if every recovered MWh found a user | 58% | 85% | 86% | 52% |
| **Reuse achieved** | **52% (49–57%)** | **21% (20–23%)** | **24% (22–26%)** | **18% (17–20%)** |
| Who uses it | 19 buildings, about 53,000 people | 40 ha greenhouse + 15 MW of process heat | same | same |

- **Site 1 cannot reach 85% as a retrofit.** About 40% of the heat sits in tenant-owned refrigerant units, electrical rooms and air leakage that no water loop reaches. To get there, 111 8th would have to move its halls onto a central chilled-water or liquid loop.
  - The critical tier gets about 37% of its yearly heat need, and about 25% in winter.
  - The homes tier gets heat only from April to October.
  - The thermal store matters little, because heat supply is always below demand.
- **Site 2 can capture 85% technically (with AI liquid cooling), but nobody nearby can use it.** No existing building passes the pipe-loss rule. Even a 40 ha greenhouse (among the largest in North America) plus 15 MW of year-round process heat reuses only about 20%. Reaching 85% would need about 100–130 MW of continuous heat demand next to the site, which means a large industrial user.
- **Bitcoin mining roughly halves what can be recovered,** because air-cooled miners exhaust low-grade heat at variable temperatures.

## 8. Limitations
- The greedy design is a heuristic; the order buildings are tested in affects the result. A mixed-integer optimization or a street-network routing model would refine it.
- Pipe routes use straight-line distance × a detour factor. There is no street-level routing or underground-utility conflict check.
- Buyers are assumed to accept a discount-to-current-cost tariff. LL97 penalty avoidance is credited only to the share of NYC buyers sampled as being over their limit.
- The Cornell and greenhouse demand sizes and costs are assumption ranges. Verify them with Cornell Facilities and a grower.
- No thermal storage was modelled. Daily storage would raise the share of heat coming from the data center, and is the next lever to test.
