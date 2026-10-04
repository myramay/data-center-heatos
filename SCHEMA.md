# `out/plan.json` and `out/plan_lansing.json` format (schema_version 0.1)

One file per site, same format. `python optimize.py chelsea` or `python optimize.py lansing` writes it.

Written by `optimize.py`, read by the map page and the offer pages. Units are in the field names.
All money is US dollars per year unless the name says `capex`. Coordinates are `[lon, lat]`.

```
site            { id, name, subtitle, dc_name, dc_status (commissioned|proposed), network_owner, camera{lon,lat,zoom,bearing,pitch}, estimated_note }
meta            { schema_version, generated_at, note, solver{status, seconds, ...} }
assumptions     { name: {value, unit, source, placeholder} }   copy of config.py
datacenter      { name, bbl, lat, lon, heat_mw_th, usable_mw_th,
                  monthly_available_mwh[12] }                  Jan..Dec
datacenter.supply_estimate  how the heat supply was derived from 111 8th Ave's LL84 electricity (grid GWh, office part, data-center MW, capture fraction)
datacenter_benefits { heat_removed_gwh_year, cooling_electricity_saved_gwh_year, average_freed_power_mw, peak_freed_power_mw }
ownership_scenarios[] { label, pipe_subsidy_fraction, n_buildings, annual_value_usd, capex_project_usd, pipe_m,
                  heat_delivered_gwh, co2_avoided_t, dc_utilization(_summer), top_customer,
                  value_kept_if_top_customer_leaves, n_public_housing, n_dac }   who pays for the street pipes
service_tiers[] { tier (PROTECTED|FIRM|BASE|FLEX), priority, note, price_adjust, n_buildings }   the priority ladder
waterfall       { "1"|"2"|"3" (build-out stage): { normal | polar_vortex | dc_down: { label, supply_kw[12][24],
                  served_kw{tier:[12][24]}, unserved_kw{tier:[12][24]}, share_served{tier}, heat_asked_gwh,
                  heat_served_gwh, heat_unused_gwh } } }   who gets heat first, hour by hour, when supply is short
site_context    { chelsea|lansing: { site, nearest_natural_reference, items[{cat, metric, value}] } }   local conditions for each site
climate_outlook[] { year, warming_f, n_buildings, annual_value_usd, heat_delivered_gwh, heating_demand_vs_today_pct,
                  hot_water_share_of_delivered, dc_utilization, dc_utilization_summer }   plan re-solved in a warmer climate
regenerative_scorecard[] { category, headline, site_says, plan_does }   air, carbon, water, climate, equity, noise
reliability    { cooling_firewall{rules[], peak_export_mw, peak_export_share_of_heat, annual_export_share_of_heat, own_cooling_margin,
                  max_export_fraction, rule_met, bypass_response_s},
                  continuity{network_availability_pct, storage_hours, backup_start_minutes, assumptions{failure rates and repair hours}} }
match_scorecard[] { axis (Temperature|Capacity|Timing|Seasonality|Continuity), headline, numbers{...}, how }   the five matches the brief asks for
ledger          { parties[{id, party, role, pays_upfront_usd, pays_annual_usd, gets_annual_usd, net_annual_usd, simple_payback_years, npv_usd,
                  wins_in_pct_of_scenarios, risk, responsibility, ...}], threshold_pct, parties_below_threshold[], balanced }
next_in_line[]  customers NOT in the plan: { bbl, address, type, units, heat_mwh_year, new_pipe_m, heat_density_mwh_per_m, meets_density_rule,
                  yearly_gap_usd, grant_needed_usd, grant_per_unit_usd, viable_at_full_cost }
robustness.party_win_pct   { party id: % of Monte Carlo scenarios in which that party's NPV is above zero }
robustness.deal_search     { levels[{pipe_subsidy_fraction, party_win_pct, weakest_party_win_pct, n_buildings_median}], recommended_share, clears_bar }
phases[3]       { phase, start_year, label, n_buildings, n_public_housing,
                  main_pipe_m, lateral_pipe_m, capex_usd,
                  heat_delivered_gwh_year, co2_avoided_t_year,
                  fines_avoided_usd_year }                     what is NEW in that phase
totals          { n_buildings, pipe_m, lateral_m, capex_usd, annual_net_cash_usd,
                  annual_value_usd, simple_payback_years, heat_delivered_gwh,
                  dc_heat_used_gwh, dc_utilization, dc_utilization_summer,
                  dc_utilization_winter, co2_avoided_t, fines_avoided_usd,
                  peak_dc_draw_mw, monthly_dc_draw_mwh[12] }   whole plan, all 3 phases
comparison      { heatos_plan{...}, no_insurance_rule{...}, biggest_buildings_first{...} }   same fields as totals
stress_tests    { lose_largest_customer{building, value_usd, change_pct},
                  dc_output_drops{fraction, heat_lost_pct, value_usd, change_pct} }
robustness      { n_runs, seed, value_usd_p10_p50_p90[3], capex_usd_p10_p50_p90[3],
                  co2_avoided_t_p10_p50_p90[3], n_buildings_p10_p50_p90[3],
                  frequency{bbl: share of scenarios where it was chosen} }   per-scenario rows are in out/montecarlo.json
buildings[]     every candidate the model looked at (see below)
pipes[]         { kind: dc_link | main | lateral, phase, serves[bbl] and heat_share (main pipes: customers downstream of it), length_m, capex_usd,
                  peak_kw_th, coords[[lon,lat],...] }          coords run away from the data center
footprints      GeoJSON FeatureCollection; properties { bbl, height_m, phase (0 = not chosen),
                  is_dc }
```

## `buildings[]`

Always present: `bbl, address, lat, lon, property_type, year_built, floor_area_ft2, height_m,
public_housing, chosen, data_quality` ("measured" from LL84, or "estimated" from floor area), `selection_frequency`.

Only when `chosen` is true:

| field | meaning |
|---|---|
| `phase`, `join_year`, `join_order` | when it connects |
| `location{direction, distance_m, west_of_dc}`, `nox_avoided_lb_year`, `pm25_avoided_lb_year` | where it is relative to the data center; on-site pollution avoided |
| `reliability{availability_pct, backup_hours_year, path_km}`, `temperature_match{source_c, direct_use_share, average_cop, space_heat_needs_c, hot_water_needs_c}`, `proposed` | continuity and temperature match for this customer; `proposed` = a new customer that does not exist yet |
| `tier`, `tier_note`, `tier_price_adjust` | service tier (see service_tiers) |
| `fit{score,temperature,timing,seasonality,capacity,avg_cop}` | heat-fit score 0-100 and its four axes (0-1) |
| `cop_month[12]`, `hot_water_share`, `demand_source` | heat pump COP by month (from the temperature lift), share of its heat that is hot water, and whether its months are "measured monthly bills" or "modeled from weather" |
| `dac`, `dac_percentile` | NY State disadvantaged-community tract flag (always present) |
| `boiler_years_left` | years until its boiler is due for replacement (estimated from year built) |
| `equipment` | heat pump kW, heat exchanger kW, lateral pipe m, and the capex of each |
| `heat_demand_mwh_year` | useful heat it needs today |
| `heat_delivered_mwh_year`, `heat_delivered_mwh_month[12]` | heat the heat pump supplies |
| `dc_heat_mwh_month[12]` | heat drawn from the data center to supply it (less than delivered; the rest is heat pump electricity) |
| `price_now_usd_per_mmbtu` | fuel only, per MMBtu of heat delivered |
| `price_now_all_in_usd_per_mmbtu` | fuel plus LL97 fines |
| `price_heatos_usd_per_mmbtu` | what HeatOS charges |
| `fuel_avoided_usd_year`, `ll97_fines_before/after/avoided_usd_year` | savings pieces |
| `heatos_charge_usd_year`, `yearly_savings_usd` | charge and net saving to the building |
| `capex_attributed_usd`, `payback_years` | capital for this building plus its share of shared pipe; payback = that capital / yearly cash margin |
| `co2_avoided_t_year`, `emissions_before_t`, `emissions_after_t`, `ll97_limit_t` | carbon |

## Hooks for teammates

* **Demand forecasts:** replace `build_demand()` in `demand.py`. It must return an array
  `[n_buildings, 12, 24]` (kW of heat, 12 typical days, 24 hours). Nothing else changes.
* **Monte Carlo:** `from optimize import run_plan; run_plan({"gas_price_usd_per_mmbtu": 20}, out=None)`
  returns the results as Python objects and takes ~10 seconds. Any name in `config.ASSUMPTIONS` can be
  overridden.

* **Monte Carlo output:** `out/montecarlo.json` has one row per scenario (`inputs`, results, chosen `buildings`).
  Ranges live in `config.ASSUMPTIONS["mc_ranges"]`. `python optimize.py` runs 60 scenarios (~75 s).
