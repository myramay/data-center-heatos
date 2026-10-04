"""
HeatOS assumptions: EVERY number the model uses lives here.

Each entry is  name: A(value, unit, source, placeholder)
  - source      where the number comes from (or what to replace it with)
  - placeholder True  = my guess, replace it with a real number
                False = taken from a real dataset/standard (still worth double-checking)

To run with different numbers (e.g. Monte Carlo):  get_config({"elec_price_usd_per_kwh": 0.2})
"""


def A(value, unit, source, placeholder=True):
    return {"value": value, "unit": unit, "source": source, "placeholder": placeholder}


ASSUMPTIONS = {
    # ------------------------------------------------------------ Data center
    "dc_heat_mw_th": A(None, "MW thermal (None = derive from the LL84 meter data)",
        "DERIVED by realdata/optimize.dc_supply(): 111 8th Ave grid electricity (LL84 2022-24) minus the "
        "office part, times the capture fraction below. Set a number here to override.", False),
    "dc_nondc_kwh_per_ft2_office": A(16.0, "kWh/ft2/yr",
        "TODO: electricity of the office floors (the rest of the meter is computers + cooling). "
        "Typical NYC office is 14-20; replace with the building's submeter."),
    "dc_heat_capture_fraction": A(0.80, "fraction of data-center electricity",
        "TODO: share of IT+cooling electricity that can be captured as warm water (rest is lost to air, "
        "pumps, leaks). 0.7-0.9 for water-cooled loops; ask the operator."),
    "dc_heat_scale": A(1.0, "multiplier", "Monte Carlo knob on the derived heat supply."),
    "dc_load_swing": A(0.04, "+/- fraction over the day",
        "Computers draw a little more by day than by night (idle power is ~50-60% of peak, so total "
        "power swings far less than CPU use). TODO: derive from Google/Azure traces."),
    "dc_source_temp_c": A(27.0, "deg C",
        "Warm-water loop temperature the heat pumps draw from (team plan: 15-30 C loop). TODO: confirm."),
    "dc_availability": A(0.95, "fraction of hours",
        "TODO: operator uptime. Heat is assumed flat all year (DC load does not follow weather)."),
    "chiller_cop": A(4.5, "kWh cooling per kWh electricity",
        "TODO: 111 8th Ave's cooling plant (chillers + towers + pumps). Heat we take away is heat its chillers "
        "do not have to remove, so this much electricity is freed for servers. Placeholder."),
    "pipe_heat_loss_fraction": A(0.03, "fraction of heat sent",
        "TODO: low-temperature (35 C) loops lose little; confirm with a pipe vendor datasheet."),

    # ------------------------------------------------------------ Heat pump
    "cop_model": A("temperature", "temperature | fixed",
        "temperature = COP from the temperature lift each month (Carnot x efficiency); fixed = use heat_pump_cop."),
    "heat_pump_cop": A(4.5, "kWh heat out per kWh electricity",
        "Used only when cop_model = fixed (and as a sanity check)."),
    "carnot_efficiency": A(0.45, "fraction of ideal", "TODO: vendor data. Good large heat pumps reach 0.45-0.55."),
    "dhw_supply_temp_c": A(60.0, "deg C", "Domestic hot water must be stored/delivered at ~60 C (Legionella rule)."),
    "space_supply_temp_cold_c": A(65.0, "deg C", "Radiator water temperature on the coldest design day. TODO: audit."),
    "space_supply_temp_mild_c": A(42.0, "deg C", "Radiator water temperature on a mild day (reset curve)."),
    "space_reset_cold_outdoor_c": A(-10.0, "deg C", "Outdoor temperature at which supply is hottest."),
    "space_reset_mild_outdoor_c": A(15.0, "deg C", "Outdoor temperature at which supply is mildest."),
    "cop_max": A(7.0, "kWh/kWh", "Cap on COP (real machines never reach the ideal at small lifts)."),
    "direct_use_approach_k": A(5.0, "kelvin",
        "If the source water is at least this much hotter than a building needs, the building takes the heat DIRECTLY "
        "through a heat exchanger, with no heat pump (only pumping electricity)."),
    "cop_direct": A(30.0, "kWh heat per kWh pumping electricity", "Effective COP of direct use (pumping only). Placeholder."),
    "low_temp_space_c": A(40.0, "deg C", "Supply temperature needed by low-temperature heating (greenhouses, radiant floors, new buildings)."),
    "dc_facility_mw": A(None, "MW (facility, incl. cooling)", "New data centers: total facility power. None = use the LL84-based estimate."),
    "dc_pue": A(1.25, "ratio", "Power usage effectiveness (facility power / IT power)."),
    "dc_liquid_capture_fraction": A(0.75, "fraction of IT power captured in the liquid loop",
        "TODO: direct-to-chip liquid cooling captures about 70-80% of server heat; the rest leaves as air. Ask the operator."),
    "home_connection_usd": A(9000, "$ per home",
        "Small heat pump/heat exchanger, meter and service line for one home (neighborhood customers). TODO: vendor quote.", True),
    "social_cost_carbon_usd_per_t": A(120, "$/tCO2e",
        "Value of a tonne of CO2 avoided, used for the community value line only (not charged to anyone). TODO: confirm the current NY DEC value.", True),
    "hp_sizing_options": A([0.5, 0.25], "fractions of building peak heat",
        "Design choice: the optimizer may size the heat pump at 50% or 25% of the building's peak hour; "
        "the old boiler stays as backup. 50% covers roughly 85-90% of yearly heat. Placeholder."),

    # ------------------------------------------------------------ Prices today
    "gas_price_usd_per_mmbtu": A(16.0, "$/MMBtu fuel", "Overridden by the state price table when use_real_data (see realdata.py). TODO: Con Edison commercial gas rate."),
    "oil2_price_usd_per_mmbtu": A(27.0, "$/MMBtu fuel", "TODO: NYC #2 oil ~$3.7/gal / 0.1384 MMBtu/gal."),
    "oil4_price_usd_per_mmbtu": A(22.0, "$/MMBtu fuel", "TODO: NYC #4 oil quote."),
    "oil56_price_usd_per_mmbtu": A(21.0, "$/MMBtu fuel", "TODO: NYC #6 oil quote."),
    "steam_price_usd_per_mmbtu": A(35.0, "$/MMBtu",
        "TODO: Con Edison steam tariff (SC 4), roughly $35-45 per Mlb."),
    "elec_price_usd_per_kwh": A(0.15, "$/kWh",
        "TODO: HeatOS's own bulk electricity price. NYISO N.Y.C. zone day-ahead price in the "
        "file you downloaded (4 Oct 2026) is only $0.025-0.059/kWh for energy; delivery and "
        "taxes are extra. Replace with a yearly average."),

    # ------------------------------------------------------------ Burning fuel today
    "boiler_eff_gas": A(0.80, "fraction", "Typical NYC fleet boiler efficiency. Placeholder."),
    "boiler_eff_oil": A(0.78, "fraction", "Typical NYC fleet boiler efficiency. Placeholder."),
    "steam_hx_eff": A(0.95, "fraction", "Steam-to-hot-water heat exchanger. Placeholder."),
    "boiler_eff_propane": A(0.85, "fraction", "Typical propane furnace/boiler. Placeholder."),
    "elec_heat_efficiency": A(1.0, "fraction", "Electric baseboard/resistance heat (1.0). Existing heat pumps are not modeled. Placeholder."),
    "propane_price_usd_per_mmbtu": A(32.0, "$/MMBtu fuel", "Overridden by the weekly NYS propane price when use_real_data and a site supplies it."),
    "elec_heat_price_usd_per_mmbtu": A(58.6, "$/MMBtu", "Electric heat cost per MMBtu = electricity price / 0.0034121. Recomputed from the electricity price."),
    # LL84 fuel use includes some cooking / process use that is not space or hot-water heat.
    "heat_share_of_fuel": A(0.90, "fraction",
        "Share of gas/oil/steam that is heating or hot water (rest is cooking/process). Placeholder."),

    # ------------------------------------------------------------ Capital costs
    "pipe_main_usd_per_m": A(6000, "$/m",
        "TODO: Manhattan street trench + pre-insulated pipe + restoration. "
        "Published district-heating range is roughly $3,000-10,000/m. Placeholder."),
    "pipe_lateral_usd_per_m": A(4000, "$/m", "TODO: smaller pipe from the street to the building. Placeholder."),
    "service_pipe_usd_per_m": A(1200, "$/m", "Small service pipe from a neighborhood's connection point to each home (Lansing overrides it). Placeholder."),
    "public_cost_weight": A(0.0, "0 to 1",
        "How much a dollar of someone else's money (grant, rate-base) counts in the optimizer, versus a project dollar. "
        "0 = free (Chelsea, as first reviewed); 1 = same as project money. Stops the plan reaching a lone customer 7 km away just because the pipe is subsidized.", True),
    "lateral_routing_factor": A(1.3, "multiplier",
        "Lateral is measured in a straight line from street to building; x1.3 for real routing."),
    "hp_usd_per_kw_th": A(700, "$/kW heat out", "TODO: large heat pump vendor quote ($400-1000/kW)."),
    "tie_in_fixed_usd": A(150000, "$ per building",
        "TODO: heat exchanger, controls, meters, rigging, permits. Placeholder."),
    "tie_in_usd_per_kw_th": A(60, "$/kW", "TODO: HX cost scales with size. Placeholder."),
    "pipe_om_fraction": A(0.01, "fraction of capex per year", "Placeholder."),
    "hp_om_fraction": A(0.03, "fraction of capex per year", "Placeholder."),

    # ------------------------------------------------------------ Finance
    "discount_rate": A(0.07, "per year", "TODO: HeatOS cost of capital."),
    "pipe_life_years": A(40, "years", "Pre-insulated steel/PE pipe design life. Placeholder."),
    "equipment_life_years": A(20, "years", "Heat pump / HX life. Placeholder."),
    "customer_discount": A(0.10, "fraction",
        "Building pays (1 - discount) x what it avoids today (fuel + LL97 fines), so it always saves."),

    # ------------------------------------------------------------ Carbon and LL97 (verify!)
    "grid_co2_t_per_kwh": A(0.000145, "tCO2e/kWh",
        "LL97 electricity coefficient for 2030-2034. TODO: verify against NYC DOB LL97 table.", True),
    "co2_t_per_kbtu": A({"gas": 0.00005311, "oil2": 0.00007421, "oil4": 0.00007529,
                          "oil56": 0.00007529, "steam": 0.00004493, "propane": 0.0000629, "elec_heat": 0.0000425},
        "tCO2e/kBtu",
        "LL97 fuel coefficients. TODO: verify against NYC DOB LL97 table (steam changes by period).", True),
    "ll97_fine_usd_per_tco2e": A(268, "$/tCO2e over the limit", "LL97 penalty rate. Verify.", False),
    "ll97_limit_t_per_ft2_by_group": A({
        "B": 0.00453, "R-2": 0.00407, "R-1": 0.00526, "E": 0.00344, "I-2": 0.01193,
        "M": 0.00403, "default": 0.00453}, "tCO2e/ft2/yr (2030-2034 limits)",
        "TODO: verify every number against the LL97 occupancy-group table. "
        "These are from memory.", True),
    "ll97_group_by_use": A({
        "Multifamily Housing": "R-2", "Residence Hall/Dormitory": "R-2", "Senior Living Community": "R-2",
        "Hotel": "R-1", "Hospital (General Medical & Surgical)": "I-2",
        "K-12 School": "E", "College/University": "E", "Pre-school/Daycare": "E",
        "Retail Store": "M", "Supermarket/Grocery": "M", "Office": "B", "Financial Office": "B",
        "Medical Office": "B"}, "use type -> LL97 group",
        "Simplified mapping from LL84 use type. Everything else = default group.", True),

    # ------------------------------------------------------------ Demand shape
    # Monthly heating degree-hours control how the space-heating part is spread over the year.
    "monthly_hdd": A([1010, 880, 725, 400, 140, 15, 0, 0, 70, 330, 590, 900],
        "deg F-days per month, Jan..Dec",
        "Approximate NYC (Central Park) 1991-2020 normals, base 65 F. TODO: pull NOAA normals.", True),
    "dhw_winter_factor": A(0.15, "extra fraction in coldest month",
        "Hot water demand is higher in winter (colder inlet water). Placeholder."),
    # Per use type: hot-water share of heat demand + which daily shape it follows.
    # dhw_share is what makes summer heat useful: the hot-water part runs all year.
    "use_profiles": A({
        "Multifamily Housing":              {"dhw": 0.25, "heat_shape": "residential", "dhw_shape": "residential"},
        "Residence Hall/Dormitory":         {"dhw": 0.30, "heat_shape": "residential", "dhw_shape": "residential"},
        "Senior Living Community":          {"dhw": 0.30, "heat_shape": "flat",        "dhw_shape": "residential"},
        "Hotel":                            {"dhw": 0.40, "heat_shape": "flat",        "dhw_shape": "flat"},
        "Hospital (General Medical & Surgical)": {"dhw": 0.35, "heat_shape": "flat",   "dhw_shape": "flat"},
        "Fitness Center/Health Club/Gym":   {"dhw": 0.55, "heat_shape": "commercial",  "dhw_shape": "flat"},
        "Office":                           {"dhw": 0.05, "heat_shape": "commercial",  "dhw_shape": "commercial"},
        "K-12 School":                      {"dhw": 0.08, "heat_shape": "commercial",  "dhw_shape": "commercial"},
        "College/University":               {"dhw": 0.12, "heat_shape": "commercial",  "dhw_shape": "commercial"},
        "Food Service":                     {"dhw": 0.35, "heat_shape": "commercial",  "dhw_shape": "commercial"},
        "default":                          {"dhw": 0.12, "heat_shape": "commercial",  "dhw_shape": "commercial"},
    }, "see keys",
        "TODO: calibrate with NYSERDA/CBECS end-use splits. Laundromats and pools are not separate "
        "LL84 use types; add them here if you find them via PLUTO building class.", True),

    # ------------------------------------------------------------ Which buildings are candidates
    "min_heat_fuel_kbtu": A(2.0e6, "kBtu/yr", "Ignore buildings burning less than ~2 GBtu/yr (too small to pipe)."),
    "max_fuel_eui_kbtu_ft2": A(300, "kBtu/ft2/yr",
        "Exclude LL84 rows above this: likely reporting errors (e.g. 313 8th Ave shows 1,175)."),

    # ------------------------------------------------------------ Phasing
    "phase_start_years": A([2027, 2030, 2034], "calendar year", "Phase 1/2/3 start. ~10-year plan."),
    "phase_capex_share": A([0.30, 0.35, 0.35], "fraction of total capex", "Rough capital split per phase."),
    "equity_weight_public_housing": A(1.5, "multiplier",
        "Public housing buildings rank as if their payback were this many times faster."),
    "boiler_life_years": A(30, "years",
        "Boilers assumed replaced every ~30 years since year built (ASHRAE service-life table: "
        "roughly 25-35 for steel boilers). Placeholder.", True),
    "base_year": A(2026, "year", "Used to compute boiler age."),
    "default_year_built": A(1950, "year", "Used when PLUTO year built is missing/invalid."),

    # ------------------------------------------------------------ Use the hackathon data pack
    "use_real_data": A(True, "on/off",
        "Use data/cache/realdata.json (weather, NREL hourly shapes, LL84 monthly, state prices, DAC equity) "
        "instead of the guesses in this file. Anything you pass as an override still wins."),
    "price_inflation_since_data_year": A(1.25, "multiplier",
        "The state price table ends in 2021; scale to today. TODO: use a real escalation (EIA/Con Ed)."),
    "price_sector": A("commercial", "commercial | residential",
        "Which state price sector to read. Most multifamily buildings buy gas on a commercial rate."),
    "dhw_share_cap": A(0.70, "fraction",
        "Cap on the hot-water share found from summer fuel use (steam buildings also run summer chillers)."),
    "equity_weight_dac": A(1.25, "multiplier",
        "Buildings in a NY State disadvantaged-community tract rank as if payback were this much faster."),

    # ------------------------------------------------------------ Service tiers + heat fit (allocate.py)
    "tier_rules": A({"continuity_critical_uses": ["Hospital (General Medical & Surgical)", "Senior Living Community",
                                                  "Medical Office", "Ambulatory Surgical Center"],
                     "critical_area_share": 0.30, "base_hot_water_share": 0.45},
        "see keys", "Tier rules: public housing = PROTECTED; >=30% hospital/senior floor area = FIRM; "
        "hot water >= 45% of heat = BASE; everyone else = FLEX. Placeholders, tune with the stakeholders.", True),
    "tier_price_adjust": A({"PROTECTED": -0.05, "FIRM": 0.05, "BASE": 0.0, "FLEX": -0.05}, "added to (1 - customer_discount)",
        "Price vs the standard (1 - discount) x avoided cost. FIRM pays +5% for the guarantee; PROTECTED and FLEX "
        "get 5% more discount (equity grant / curtailable). Placeholders.", True),
    "fit_weights": A({"temperature": 0.25, "timing": 0.20, "seasonality": 0.30, "capacity": 0.25}, "weights",
        "Weights of the heat-fit score. Seasonality is highest because summer heat is the hard part. Placeholders.", True),
    "fit_capacity_ref_share": A(0.35, "fraction of the data center's heat",
        "A building whose heat pump needs this share of the supply scores 0 on capacity fit."),
    "stress_polar_months": A([11, 0, 1], "month index, Dec Jan Feb", "Polar-vortex stress applies to these months."),
    "stress_polar_demand_multiplier": A(1.35, "x demand", "Polar vortex: winter heat demand rises by this factor."),

    # ------------------------------------------------------------ Buildings with no LL84 data
    "estimate_missing_buildings": A(True, "on/off",
        "Buildings that are not in LL84 get an ESTIMATED fuel use = floor area x typical intensity."),
    "estimated_eui_quantile": A(0.40, "quantile",
        "Typical = 40th percentile of the measured LL84 buildings of the same kind nearby "
        "(a bit below the median, so we do not over-promise)."),
    "estimated_min_samples": A(5, "buildings",
        "Need this many measured buildings of a kind; otherwise use all buildings."),
    "estimated_fuel": A("gas", "fuel", "Assume estimated buildings burn gas (most common). Placeholder."),
    "no_heat_class_letters": A("ABGTUVZ", "PLUTO building class first letters",
        "Never estimate: houses, garages, transit, utilities, vacant, misc."),

    # ------------------------------------------------------------ Risk limits and stress tests
    "insurance_rule": A(False, "on/off",
        "Force diversification: if ANY one customer leaves, the plan keeps at least insurance_min_fraction of "
        "its value. OFF by default because at today's pipe costs the small buildings cannot pay for the "
        "pipes without the anchor customer, so switching it on leaves no viable plan (see ownership_scenarios)."),
    "pipe_subsidy_fraction": A(1.0, "fraction of street-pipe capital paid by someone else",
        "BASE CASE = 1.0: Con Edison builds and owns the street pipes and recovers them through rates "
        "(the team's Chelsea architecture, NY UTEN Act). 0.0 = HeatOS pays for the pipes itself. "
        "The plan also reports 0%, 50% and 100% side by side. Laterals and heat pumps stay with the project."),
    "insurance_min_fraction": A(0.50, "fraction of plan value",
        "After losing any one customer the plan must keep at least this share of its yearly value "
        "(so no customer is worth more than about half the project). Placeholder; tune it."),
    "insurance_min_value_usd": A(0, "$/yr", "Extra floor: minimum yearly value that must remain after losing any one customer."),
    "max_customer_share": A(1.0, "fraction of heat delivered",
        "Optional hard cap on one building's share of delivered heat (1.0 = off). Off by default: a share cap "
        "can be gamed by adding weak buildings to dilute the big one; the insurance rule is stronger."),
    "stress_dc_loss_fraction": A(0.30, "fraction", "Stress test: data center heat output drops by this much."),

    # ------------------------------------------------------------ Monte Carlo (low, most likely, high)
    "mc_runs": A(60, "runs", "How many random scenarios `python optimize.py` runs for the robustness view."),
    "mc_ranges": A({
        "gas_price_usd_per_mmbtu": (7, 10, 17), "oil2_price_usd_per_mmbtu": (16, 21, 32),
        "steam_price_usd_per_mmbtu": (28, 35, 48), "elec_price_usd_per_kwh": (0.14, 0.20, 0.28),
        "heat_pump_cop": (3.4, 4.5, 5.3), "pipe_main_usd_per_m": (4000, 6000, 11000),
        "pipe_lateral_usd_per_m": (2500, 4000, 7000), "hp_usd_per_kw_th": (450, 700, 1000),
        "dc_heat_scale": (0.6, 1.0, 1.4), "discount_rate": (0.05, 0.07, 0.10),
        "customer_discount": (0.05, 0.10, 0.15), "anchor_propane_share": (0.0, 0.5, 1.0),
    }, "(low, mode, high) triangular", "Ranges are guesses. Teammates doing Monte Carlo: replace these.", True),

    # ------------------------------------------------------------ Modeled demand (sites with no benchmarking data)
    "parcel_eui_kbtu_per_ft2_per_1000hdd": A({
        "Single-Family Home": 10.0, "Multifamily Housing": 8.0, "Senior Living Community": 10.0, "K-12 School": 6.0,
        "Office": 4.5, "Retail Store": 5.0, "Worship Facility": 4.0, "Municipal/Emergency": 6.0, "Medical Office": 7.0,
        "Hotel": 9.0, "Food Service": 12.0, "Warehouse/Industrial": 3.0, "default": 5.0},
        "kBtu of FUEL per ft2 per 1,000 heating degree-days (F)",
        "TODO: calibrate with NYSERDA / RECS data for the Southern Tier. Roughly 65 kBtu/ft2/yr for an older home at 6,600 HDD. "
        "Placeholder.", True),
    "greenhouse_fuel_kbtu_per_ft2_year": A(200.0, "kBtu fuel per ft2 of greenhouse per year",
        "TODO: grower data. Heated greenhouses in cold climates use roughly 100-300. Placeholder.", True),
    "era_factor": A([[1950, 1.25], [1980, 1.10], [2000, 0.90], [3000, 0.70]], "[built before year, multiplier]",
        "Newer buildings are better insulated. Placeholder.", True),
    "low_temp_year_built": A(2005, "year", "Buildings built from this year on are assumed to have low-temperature heating (radiant/fan-coil).", True),

    "linear_heat_density_threshold_mwh_per_m": A(1.5, "MWh of heat per metre of pipe per year",
        "Rule of thumb used by district-heating planners: below roughly 1.5 MWh/m/yr a network rarely pays for itself. TODO: confirm for local pipe costs.", True),

    # ------------------------------------------------------------ Network engineering (system.py)
    "loop_return_c": A(17.0, "deg C", "Return temperature of the network water after customers take heat out (supply is the source temperature). Chelsea: ambient loop, 10 K swing. Placeholder.", True),
    "ground_temp_c": A(11.0, "deg C", "Undisturbed ground temperature at pipe depth (used for pipe heat loss). Placeholder.", True),
    "pipe_loss_w_per_m_per_k": A(0.20, "W per metre of pipe per kelvin", "Pre-insulated plastic or steel pipe, each of supply and return. TODO: pipe datasheet.", True),
    "central_network_supply_c": A(80.0, "deg C", "For comparison only: a conventional hot-water district heating network.", True),
    "central_network_return_c": A(50.0, "deg C", "For comparison only.", True),
    "water_cp_kj_per_kg_k": A(4.18, "kJ/kg/K", "Specific heat of water.", False),
    "pipe_sizes_dn": A([25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300, 350, 400, 500, 600], "mm nominal diameter", "Standard pipe sizes.", False),
    "pipe_velocity_limits": A([[50, 1.0], [100, 1.5], [200, 2.0], [1000, 2.5]], "[up to DN, max velocity m/s]", "Rule-of-thumb design velocities for district heating pipes. TODO: hydraulic design.", True),
    "pipe_pressure_drop_pa_per_m": A(150, "Pa per metre", "Design pressure drop along the critical path (common district-heating design value).", True),
    "substation_pressure_drop_kpa": A(60, "kPa", "Pressure drop across a customer's heat exchanger and valves.", True),
    "pump_efficiency": A(0.65, "fraction", "Overall pump + motor efficiency.", True),
    "storage_delta_t_k": A(20.0, "kelvin", "Temperature swing used when sizing the hot-water buffer tank.", True),
    "design_peak_diversity": A(1.0, "factor", "Not all customers peak together; 1.0 is conservative (no diversity credit).", True),

    # ------------------------------------------------------------ Reliability (cooling first, heat continuity)
    "dc_trips_per_year": A(1.5, "events/yr", "How often the data center's heat source drops out (power or cooling-plant trip). TODO: operator history.", True),
    "dc_trip_hours": A(6.0, "hours per event", "Typical duration of such an event. Placeholder.", True),
    "pipe_failures_per_km_year": A(0.10, "failures/km/yr", "Buried pre-insulated district-heating pipe: roughly 0.05-0.2 (published networks). Placeholder.", True),
    "pipe_repair_hours": A(24.0, "hours", "Time to isolate, dig and repair a break. Placeholder.", True),
    "hp_failures_per_year": A(0.3, "failures/building/yr", "Heat pump or heat exchanger outage at one building. Placeholder.", True),
    "hp_repair_hours": A(12.0, "hours", "Placeholder.", True),
    "storage_hours": A(2.0, "hours of average winter load", "Hot-water buffer tank at the network head covers short outages. Placeholder.", True),
    "bypass_response_s": A(30, "seconds", "Cooling-first bypass valve: how fast all heat is re-routed to the data center's own coolers if the network trips. TODO: vendor spec.", True),
    "max_export_fraction": A(0.80, "fraction of the heat the data center makes",
        "Design rule: never export more than this share, so its own cooling plant always carries a base load and stays warm. Placeholder.", True),
    "backup_start_minutes": A(10, "minutes", "A customer's old boiler fires automatically when the network temperature or flow drops. Placeholder.", True),

    # ------------------------------------------------------------ Stakeholder ledger
    "ledger_years": A(25, "years", "Horizon for each party's net present value.", True),
    "dc_export_usd_per_kw_th": A(120, "$ per kW of heat exported", "The data center's side of the connection: plate heat exchanger, pumps, controls, bypass valve. TODO: vendor quote.", True),
    "dc_om_fraction": A(0.03, "fraction of its capex per year", "Placeholder.", True),
    "electricity_price_for_dc_usd_per_kwh": A(0.12, "$/kWh", "What the data center pays for power (large customer). The cooling electricity it no longer needs is valued at this. TODO: its tariff.", True),
    "water_value_usd_per_gallon": A(0.01, "$/gal", "Water + sewer value of cooling water saved. Placeholder.", True),
    "dc_heat_price_usd_per_mwh_th": A(0.0, "$/MWh of heat", "Price the network pays the data center for its heat (0 = free; negative = the data center pays a removal fee).", True),
    "affordability_cap_share_of_income": A(0.06, "fraction of household income", "Energy-affordability yardstick for the community lines. Placeholder.", True),

    # ------------------------------------------------------------ Local context (climate, air, water)
    "climate_warming_f": A(0.0, "deg F warmer than 2015-2024",
        "Run the plan in a warmer climate (space heating shrinks, hot water does not). 0 = today. "
        "See climate_outlook for the dated cases."),
    "climate_outlook": A([[2027, 0.0], [2034, 1.0], [2050, 3.0], [2080, 12.0]], "[year, deg F warmer]",
        "Regional projection: +3 F on average by 2050 and +12 F by 2080 (high case); 2034 is interpolated. "
        "TODO: confirm against a published climate projection for NYC.", False),
    "grid_co2_local_t_per_kwh": A(0.000297, "tCO2e/kWh",
        "654 lb CO2/MWh = the two nearest power plants (cogeneration). TODO: confirm with eGRID. "
        "Used as a harsher check than the LL97 2030 coefficient.", False),
    "nox_lb_per_mmbtu": A({"gas": 0.098, "oil2": 0.144, "oil4": 0.20, "oil56": 0.31, "steam": 0.0, "propane": 0.15, "elec_heat": 0.0}, "lb NOx per MMBtu burned",
        "EPA AP-42 commercial boilers, from memory: VERIFY. Steam is made at Con Ed plants, not in the building, so 0 here.", True),
    "pm25_lb_per_mmbtu": A({"gas": 0.0075, "oil2": 0.024, "oil4": 0.04, "oil56": 0.07, "steam": 0.0, "propane": 0.0075, "elec_heat": 0.0}, "lb PM per MMBtu burned",
        "EPA AP-42 commercial boilers (filterable + condensable), from memory: VERIFY.", True),
    "cooling_water_l_per_kwh_heat": A(1.8, "litres of water per kWh of heat rejected by an evaporative tower",
        "Latent heat of water (about 1.6 L/kWh) plus drift and blowdown. TODO: operator's real water bill.", True),
    "evaporative_cooling_share": A(0.70, "fraction of the data center's heat rejected by evaporative towers",
        "TODO: ask the operator whether 111 8th Ave uses cooling towers. If it is all dry coolers, water saved is 0.", True),

    # ------------------------------------------------------------ Solver
    "solver_time_limit_s": A(120, "seconds", "MILP time limit."),
}


# Values that differ by place. Lansing is rural/suburban: soft ground, cheaper trenching, a data center that is
# not built yet, no LL97, and homes (not big buildings) as most customers.
SITE_OVERRIDES = {
    "lansing": {
        "dc_heat_mw_th": A(None, "MW thermal", "DERIVED from the planned 150 MW (Phase I) campus: facility MW / PUE x liquid capture fraction. "
                           "Phase II (~300 MW) would double it.", False),
        "dc_facility_mw": A(150.0, "MW (facility)", "Phase I: three buildings totaling about 150 MW (developer's planning-board presentation).", False),
        "dc_pue": A(1.25, "ratio", "Developer states PUE of about 1.25.", False),
        "loop_return_c": A(30.0, "deg C", "Warm loop returns at about 30 C after greenhouses and fish tanks take their heat (team plan: ~50 C supply / 30 C return).", True),
        "ground_temp_c": A(9.0, "deg C", "Colder ground in the Finger Lakes. Placeholder.", True),
        "dc_source_temp_c": A(50.0, "deg C",
            "Liquid-cooled return water. TODO: ask the developer; direct-to-chip loops run roughly 40-60 C."),
        "pipe_main_usd_per_m": A(1800, "$/m", "TODO: rural/suburban trench in soft ground with road restoration; roughly a third of Manhattan. Placeholder."),
        "pipe_lateral_usd_per_m": A(1200, "$/m", "TODO: service pipes along rural roads. Placeholder."),
        "tie_in_fixed_usd": A(60000, "$ per building", "Heat exchanger, controls and meters for a school, farm or business. Placeholder."),
        "hp_usd_per_kw_th": A(600, "$/kW", "TODO: heat pump quote (smaller lift, simpler machines)."),
        "ll97_fine_usd_per_tco2e": A(0, "$/tCO2e", "No LL97-style fine in Lansing."),
        "min_heat_fuel_kbtu": A(3.0e5, "kBtu/yr", "Homes are small; neighborhoods are what matter."),
        "max_fuel_eui_kbtu_ft2": A(400, "kBtu/ft2/yr", "Greenhouses use much more heat per ft2."),
        "pipe_subsidy_fraction": A(0.25, "fraction", "BASE CASE: grants carry a quarter of the street pipes, the Town-TeraWulf venture the rest. "
                                   "Chosen by the deal search: the smallest grant share at which every party has at least an 80% chance of winning.", True),
        "public_cost_weight": A(0.5, "0 to 1", "Grant dollars count half as much as venture dollars when choosing customers.", True),
        "service_pipe_usd_per_m": A(400, "$/m", "TODO: small plastic service pipes in soft ground. Placeholder.", True),
        "customer_discount": A(0.10, "fraction", "Households and farms pay 10% less than they avoid today."),
        "boiler_life_years": A(25, "years", "Residential boilers/furnaces last about 20-25 years. Placeholder."),
        "phase_start_years": A([2029, 2032, 2036], "calendar year", "Phase I data center opens first; heat network follows."),
        "equity_weight_public_housing": A(1.5, "multiplier", "Low-income / manufactured-home neighborhoods rank as if payback were this much faster."),
        "proposed_anchors": A([
            {"name": "Greenhouse (proposed, 5 acres)", "use": "Greenhouse", "area_ft2": 217800, "dx_m": 900, "dy_m": -200, "fuel": "gas"},
            {"name": "Indoor fish farm (proposed)", "use": "Greenhouse", "area_ft2": 60000, "dx_m": 1150, "dy_m": 150, "fuel": "gas"}],
            "list of new customers",
            "Not in the data: the team's concept is that cheap local heat creates new year-round customers next to the data center. "
            "Sizes and positions are placeholders (TODO: land agreement, grower interest). Each is compared with heating by natural gas.", True),
        "mc_ranges": A({
            "gas_price_usd_per_mmbtu": (11, 16.7, 24), "oil2_price_usd_per_mmbtu": (28, 38.7, 50), "propane_price_usd_per_mmbtu": (22, 31.2, 40),
            "elec_price_usd_per_kwh": (0.14, 0.20, 0.28), "pipe_main_usd_per_m": (1200, 1800, 3500), "pipe_lateral_usd_per_m": (800, 1200, 2400),
            "service_pipe_usd_per_m": (250, 400, 800), "hp_usd_per_kw_th": (400, 600, 900), "discount_rate": (0.05, 0.07, 0.10),
            "customer_discount": (0.10, 0.20, 0.30), "anchor_propane_share": (0.0, 0.5, 1.0), "home_connection_usd": (5000, 9000, 14000)},
            "(low, mode, high) triangular", "Lansing's ranges, centered on its own prices. Guesses: replace.", True),
        "anchor_propane_share": A(0.5, "fraction", "What a NEW greenhouse or fish farm would burn instead: this share propane, the rest natural gas. "
                                  "Unknown (depends on whether a gas main reaches the site), so the Monte Carlo varies it from 0 to 1.", True),
        "home_max_distance_to_street_m": A(400, "m", "Homes farther than this from a street intersection are not connected."),
        "tier_rules": A({"continuity_critical_uses": ["K-12 School", "Municipal/Emergency", "Senior Living Community", "Medical Office"],
                         "critical_area_share": 0.30, "base_hot_water_share": 0.45},
            "see keys", "Schools, emergency services, senior living and clinics are FIRM; greenhouses and other year-round users are BASE.", True),
        "estimated_eui_quantile": A(0.40, "quantile", "Not used (all Lansing demand is modeled)."),
        "customer_discount": A(0.20, "fraction", "A community-owned venture prices heat 20% below what customers avoid today (heat itself is nearly free).", True),
        "storage_hours": A(6.0, "hours", "A pit or tank store at the site is cheap on open land. Placeholder (the team's Lansing concept includes pit storage).", True),
        "dc_trips_per_year": A(1.0, "events/yr", "New facility with redundant power. Placeholder.", True),
        "hp_sizing_options": A([1.0, 0.5, 0.25], "fractions of building peak heat",
            "A direct-use heat exchanger costs little, so it can be sized for the full peak; heat pumps stay at 50% or 25% of peak. The optimizer picks.", True),
        "electricity_price_for_dc_usd_per_kwh": A(0.07, "$/kWh", "Large industrial customer in NYISO Zone C. Placeholder.", True),
        "dc_export_usd_per_kw_th": A(90, "$ per kW of heat exported", "Liquid loop already exists; a tap-off heat exchanger is cheaper. Placeholder.", True),
        "dc_heat_price_usd_per_mwh_th": A(0.0, "$/MWh", "Heat is free to the venture; TeraWulf's return is its license to operate and a smaller tax abatement. Placeholder.", True),
        "stress_polar_demand_multiplier": A(1.40, "x demand", "Lake-effect cold snap: winter demand rises by this factor."),
        "cooling_water_l_per_kwh_heat": A(1.8, "L/kWh", "Used only on the ~10-20 peak days when the cooling system uses water."),
        "evaporative_cooling_share": A(0.02, "fraction",
            "Developer: mostly dry cooling; water only at peak (10-20 days a year, up to ~700,000 gal/day; typically ~3,000 gal/day). "
            "So very little water is saved by heat reuse.", False),
        "chiller_cop": A(12.0, "kWh heat per kWh fan/pump electricity",
            "Mostly dry coolers: rejecting 1 kWh of heat costs only about 0.08 kWh of fan and pump power. Placeholder.", True),
    },
}


def assumptions_for(site="chelsea"):
    """ASSUMPTIONS with the site's own values swapped in (each entry keeps its unit and source note)."""
    out = dict(ASSUMPTIONS)
    out.update(SITE_OVERRIDES.get(site, {}))
    return out


def get_config(overrides=None, site="chelsea"):
    """Plain name -> value dict used by the model. `overrides` replaces any value; the site's own values come first."""
    cfg = {k: v["value"] for k, v in assumptions_for(site).items()}
    cfg.update(overrides or {})
    return cfg
