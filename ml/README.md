# ml/ — ML team integration guide (Rachel, Annie)

HeatOS runs today on mock providers (`engine/mocks.py`). Your modules replace
them one at a time. **The only engine file you edit is `engine/providers.py`**
(the `REGISTRY` block). Search the repo for `ML_TEAM_INTEGRATION` to see
every hook.

## How to swap one in

```python
# engine/providers.py, REGISTRY block
from ml.demand import DemandModel
DEMAND: DemandProvider = DemandModel(buildings=BUILDINGS, weather=WEATHER)
```

Then run `make test`. `tests/test_contracts.py` checks shapes, bands,
determinism and protocol conformance against whatever is registered.

## Contracts

All models live in `engine/contracts.py` (pydantic v2, `extra="forbid"`), and
`web/src/types.ts` mirrors them. A test fails if the two drift apart. Times
are tz-aware hourly `datetime`s in the site's timezone (`America/New_York`).
Power series are **kW thermal**. Each provider also implements
`model_card() -> ModelCard`, which feeds report section 2.

| Provider | Method | Returns |
|---|---|---|
| Buildings | `get_buildings(site: "chelsea"\|"lansing")` | `list[Building]` |
| Weather | `get_weather(site, start: datetime, hours: int, scenario="typical")` | `WeatherSeries` |
| Demand | `get_demand_forecast(site, building_ids: Sequence[str], start, hours, weather_scenario="typical")` | `list[DemandForecast]` (same order as `building_ids`) |
| Supply | `get_supply_forecast(site, start, hours, scenario="base")` | `SupplyForecast` |
| Confidence | `get_confidence(site, plan: Plan, state: SimState, n_futures=100)` | `ConfidenceResult` |
| Jev | `get_jev_opinion(state: SimState)` | `JevOpinion` |

### Data shapes

```text
Building          id, name, site, lat, lon, x_m, y_m (m from data center; Chelsea axes follow
                  the Manhattan grid: x crosstown east, y uptown), street_distance_m, height_m,
                  footprint_m2, floor_area_m2, use_type, year_built?, heating_system,
                  annual_heat_mwh, current_heat_cost_usd_per_mwh, required_supply_temp_c,
                  boiler_age_years?, equity_score [0,1], is_estimated, notes
WeatherSeries     hours[H], t_out_c[H], scenario
DemandForecast    building_id, hours[H], p05[H] <= p50[H] <= p95[H]   (kW, >= 0; validated)
SupplyForecast    hours[H], p05[H] <= p50[H] <= p95[H] (kW), supply_temp_c[H]
ConfidenceResult  p_all_warm, p_each_party_ahead{party_id: p}, expected_unmet_hours,
                  guarantee_prices{building_id: USD over the horizon},
                  p_guarantee_kept{building_id: P(no missed hour)}, top_uncertainty_drivers
                  [{name, share}] (shares sum to 1), n_futures, horizon_hours, method
JevOpinion        playbook, playbook_probability, p_supply_meets_guarantees, latency_ms, available
ModelCard         name, version, method, datasets[], assumptions[], metrics{}, is_mock
```

Weather scenarios: `typical | cold_year | warm_year | polar_vortex | heat_wave | lake_effect`.
Supply scenarios: `base | tenant_leaves | server_outage | flex_off`.
Party ids come from `engine/sites/<site>.yaml` → `parties[].id`
(Chelsea: `google, con_ed, commercial, public_housing, guarantee_buyers`;
Lansing: `terawulf, joint_venture, ag_schools, homes_coop`).

### Definitions the engine relies on

- **p_all_warm**: share of futures in which every connected building is fully
  served by network plus storage, with no backup boiler, for the whole
  horizon. A building-hour counts as "unmet" when more than 1% of its demand
  goes unserved.
- **expected_unmet_hours**: mean over futures of the worst building's unmet hours.
- **guarantee_prices**: per guaranteed building, the premium for the horizon:
  `E[refunds] + (CVaR95[refunds] − E[refunds])`. Refund per missed hour = 3 × that hour's heat bill.
- **p_each_party_ahead**: P(party's net cash over the horizon > 0). Capex is
  allocated by each building's share of annual heat. `FutureOutcome.party_net_usd`
  already contains exactly this per future (it comes from engine/ledger.py).
- **p_guarantee_kept**: per guaranteed building, the share of futures with no missed hour.

`SimState.run_id`: when set, it names a live run. Futures then branch from
that run's current hour, with any stress tests already applied. Pass the
`state` you were given straight through to the simulator.

## Calling the engine's simulator (Monte Carlo)

Don't re-implement the physics. Sample `FutureInputs` and call the engine:

```python
from engine import providers
outcome: FutureOutcome = providers.SIMULATOR.simulate_future(plan, state, inputs)
# vectorized, if available:
outcomes = providers.SIMULATOR.simulate_futures(plan, state, list_of_inputs)
```

```text
FutureInputs   seed, cop_eta (0,1], demand_mult, supply_mult, electricity_price_mult, fuel_price_mult
FutureOutcome  unmet_hours_by_building{id: int}, unmet_mwh, refunds_usd_by_building{id: USD},
               party_net_usd{party: USD}, system_cost_usd
```

The mock samples uniform jitter (COP eta U(0.5, 0.8), demand ×U(0.85, 1.15),
supply ×U(0.8, 1.2), prices ×U(0.8, 1.2)) over about 100 futures. Your version
is planned to use 1,000-run Latin hypercube sampling plus a GEV cold snap.
**Please expose the GEV 1-in-50-year low** (Chelsea polar vortex, Lansing
lake-effect). The placeholders are `POLAR_VORTEX_LOW_C = -18` and
`LAKE_EFFECT_LOW_C = -22` in `engine/mocks.py`.

## Jev

If Jev is down in production, return `available=False`. The UI then hides Jev
and gates on Monte Carlo alone. The ACT/REVIEW/ESCALATE rule lives in
`engine/verdict.py` (engine-owned).

## Performance budget

Live UI: one `get_confidence` per simulated hour at 48 h horizon, ideally < 200 ms.
Report/guarantee pricing: `horizon_hours=8760`, < 10 s is fine.
