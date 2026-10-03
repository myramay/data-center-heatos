// Mirror of engine/contracts.py. tests/test_contracts.py::test_typescript_parity
// fails if a field is added or removed on one side only.
// ML_TEAM_INTEGRATION: shapes agreed with the ML team. Datetimes are ISO 8601 strings.

export type SiteId = "chelsea" | "lansing";
export type UseType =
  | "residential" | "public_housing" | "office" | "retail" | "food" | "school"
  | "greenhouse" | "aquaculture" | "clinic" | "home";
export type HeatingSystem = "steam" | "gas_boiler" | "oil" | "propane" | "electric" | "unknown";
export type WeatherScenario =
  | "typical" | "cold_year" | "warm_year" | "polar_vortex" | "heat_wave" | "lake_effect";
export type SupplyScenario = "base" | "tenant_leaves" | "server_outage" | "flex_off";
export type Playbook =
  | "draw_storage" | "start_steam_hp" | "start_backup" | "shift_compute" | "curtail_cooling_export";
export type Verdict = "ACT" | "REVIEW" | "ESCALATE";
export type ConnectionOption =
  | "direct_link" | "loop_hp" | "steam_hp" | "direct_use" | "booster" | "not_connected";

export interface Building {
  id: string;
  name: string;
  site: SiteId;
  lat: number;
  lon: number;
  x_m: number; // local metres east (crosstown) of the data center
  y_m: number; // local metres north (uptown) of the data center
  street_distance_m: number;
  height_m: number;
  footprint_m2: number;
  floor_area_m2: number;
  use_type: UseType;
  year_built: number | null;
  heating_system: HeatingSystem;
  annual_heat_mwh: number;
  current_heat_cost_usd_per_mwh: number;
  required_supply_temp_c: number;
  boiler_age_years: number | null;
  equity_score: number; // 0-1
  is_estimated: boolean;
  notes: string;
}

export interface WeatherSeries {
  hours: string[];
  t_out_c: number[];
  scenario: WeatherScenario;
}

export interface DemandForecast {
  building_id: string;
  hours: string[];
  p05: number[]; // kW thermal
  p50: number[];
  p95: number[];
}

export interface SupplyForecast {
  hours: string[];
  p05: number[]; // kW thermal
  p50: number[];
  p95: number[];
  supply_temp_c: number[];
}

export interface PlanItem {
  building_id: string;
  option: ConnectionOption;
  connect: boolean;
  npv_usd: number;
  phase: 1 | 2 | 3 | null;
  guaranteed: boolean;
  design_capacity_kw: number;
  reason_codes: string[];
}

export interface Plan {
  site: SiteId;
  items: PlanItem[];
  created_by: string;
}

export interface SimState {
  site: SiteId;
  time: string;
  hour_index: number;
  horizon_hours: number;
  t_out_c: number;
  supply_kw: number;
  demand_kw: number;
  storage_soc_mwh: number;
  storage_capacity_mwh: number;
  unmet_kw: number;
  backup_kw: number;
  electricity_price_usd_per_mwh: number;
  flexible_compute_available: boolean;
  active_scenarios: string[];
  weather_scenario: WeatherScenario;
  supply_scenario: SupplyScenario;
  run_id: string | null; // live run; futures branch from its inputs
}

export interface FutureInputs {
  seed: number;
  cop_eta: number;
  demand_mult: number;
  supply_mult: number;
  electricity_price_mult: number;
  fuel_price_mult: number;
}

export interface FutureOutcome {
  unmet_hours_by_building: Record<string, number>;
  unmet_mwh: number;
  refunds_usd_by_building: Record<string, number>;
  party_net_usd: Record<string, number>;
  system_cost_usd: number;
}

export interface UncertaintyDriver {
  name: string;
  share: number;
}

export interface ConfidenceResult {
  p_all_warm: number;
  p_each_party_ahead: Record<string, number>;
  expected_unmet_hours: number;
  guarantee_prices: Record<string, number>; // USD premium over the evaluated horizon
  p_guarantee_kept: Record<string, number>; // P(no missed hour over the horizon)
  top_uncertainty_drivers: UncertaintyDriver[];
  n_futures: number;
  horizon_hours: number;
  method: string;
}

export interface JevOpinion {
  playbook: Playbook;
  playbook_probability: number;
  p_supply_meets_guarantees: number;
  latency_ms: number;
  available: boolean; // false -> UI hides Jev, shows Monte Carlo only
}

export interface ModelCard {
  name: string;
  version: string;
  method: string;
  datasets: string[];
  assumptions: string[];
  metrics: Record<string, number>;
  is_mock: boolean;
}
