// Shapes of the WebSocket frames and the per-site bundle (api/live.py).
import type { Building, ConfidenceResult, JevOpinion, Plan, SiteId, Verdict } from "./types";

export type Mode = "network" | "storage" | "mixed" | "backup" | "off";

export interface FrameBuilding {
  id: string;
  delivered_kw: number;
  unmet_kw: number;
  temp_c: number | null;
  mode: Mode;
  backup_on: boolean;
}

export interface SimEvent {
  hour_index: number;
  time: string;
  kind: "scenario" | "ops" | "recovery";
  severity: "info" | "warn" | "alert" | "ok";
  text: string;
}

export interface MoneyLink { source: string; target: string; label: string; value_usd: number }

export interface GuaranteeRow {
  building_id: string;
  name: string;
  premium_usd_per_yr: number;
  p_kept: number | null;
  missed_hours: number;
  refunds_owed_usd: number;
  on_backup: boolean;
}

export interface Frame {
  type: "frame";
  run_id: string;
  site: SiteId;
  time: string;
  hour_index: number;
  hours_total: number;
  speed: number;
  paused: boolean;
  autopilot: string;
  active_scenarios: string[];
  weather: { t_out_c: number };
  buildings: FrameBuilding[];
  data_center: { offered_kw: number; used_kw: number; fallback_kw: number; temp_c: number; freed_mw: number; shift_kw: number };
  storage: { id: string; type: string; soc_mwh: number; soc_frac: number; in_kw: number; out_kw: number }[];
  loop: { flow_m3h: number; supply_temp_c: number; return_temp_c: number; pump_kw: number };
  cooling_sold_kw: number;
  steam_hp_on: boolean;
  demand_kw: number;
  network_kw: number;
  backup_kw: number;
  events: SimEvent[];
  money: {
    hour: MoneyLink[];
    party_net_hour: Record<string, number>;
    party_totals: Record<string, number>;
    sankey_24h: { nodes: { id: string; name: string }[]; links: MoneyLink[] };
  };
  confidence: ConfidenceResult | null;
  confidence_hour: number | null;
  jev: JevOpinion | null;
  verdict: Verdict | null;
  guarantees: GuaranteeRow[];
  margin: { supply_margin_pct: number; storage_cover_h: number; storage_usable_mwh: number };
  impact_running: { heat_mwh: number; co2_t: number; water_m3: number; backup_mwh: number; cooling_mwh: number };
  physics?: Physics;
}

export interface Physics {
  dc_used_kw: number; dc_fallback_kw: number; cooling_in_kw: number;
  storage_out_kw: number; storage_in_kw: number; hp_elec_kw: number; backup_kw: number;
  delivered_kw: number; demand_kw: number; pipe_loss_kw: number; pump_kw: number; cop_avg: number | null;
  loop_supply_c: number; loop_return_c: number; dc_supply_c: number; t_out_c: number; flow_m3h: number;
  balance_in_kw: number; balance_out_kw: number; balance_error: number;
}

export interface Alternative {
  key: string; label: string; description: string; buildings: number; heat_mwh: number; network_share: number;
  backup_hours: number; system_cost_usd_per_yr: number; co2_t_per_yr: number; co2_avoided_t_per_yr: number;
  party_npv_usd: Record<string, number>; everyone_ahead: boolean; min_party: string | null;
  carbon_waterfall: { step: string; t: number; total?: boolean }[]; notes: string[];
}

export interface Alternatives {
  site: string; carbon_price_usd_per_t: number; universe_buildings: number; alternatives: Alternative[];
  social_cost_usd_per_yr: Record<string, number>; best_social: string;
  verdict: { heatos_everyone_ahead: boolean; heatos_lowest_social_cost: boolean; summary: string };
}

export interface TreeNode {
  id: number;
  samples: number;
  distribution: Record<string, number>;
  majority: string;
  majority_label: string;
  purity: number;
  leaf: boolean;
  question?: string;
  feature?: string;
  threshold?: number;
  yes?: TreeNode;
  no?: TreeNode;
}

export interface PathNode extends Omit<TreeNode, "yes" | "no"> {
  answer: "yes" | "no" | null;
  value?: number | null;
  confidence: number;
}

export interface OptionEval {
  option: string;
  capex_usd: number;
  pipe_m: number;
  pipe_usd: number;
  cop: number | null;
  hp_electricity_mwh: number;
  annual_savings_usd: number;
  annual_co2_avoided_t: number;
  npv_financial_usd: number;
  npv_value_usd: number;
  note: string;
}

export interface Explanation {
  building_id: string;
  path: PathNode[];
  tree_choice: string;
  tree_choice_label: string;
  recommendation: Plan["items"][number];
  recommendation_label: string;
  options: OptionEval[];
  agrees: boolean;
  note: string | null;
}

export interface PartyEconomics {
  id: string;
  name: string;
  role: string;
  upfront_usd: number;
  financed_usd: number;
  annual_operating_usd: number;
  annual_financing_usd: number;
  npv_usd: number;
  payback_year: number | null;
  discount_rate: number;
  horizon_years: number;
  p_ahead: number | null;
  cumulative: number[];
}

export interface ScoreRow { key: string; label: string; weight: number; chelsea: number; lansing: number; rationale: string }

export interface Bundle {
  site: SiteId;
  config: {
    name: string; address: string; owner: string; recommended: boolean;
    data_center: { name: string; capacity_mw_th: number; capture_fraction: number; liquid_cooled: boolean;
                   compute_follows_heat: boolean; flexible_compute_share: number };
    loop: { type: string; supply_temp_c: number; return_temp_c: number; sells_cooling: boolean };
    storage: { id: string; type: string; capacity_mwh: number }[];
    parties: { id: string; name: string; role: string }[];
    electricity_usd_per_mwh: number;
    carbon_price_usd_per_t: number | null;
  };
  buildings: Building[];
  plan: Plan;
  plan_summary: { budget_kw: number; used_kw: number; order: string[] };
  pipes: { from: string; to: string; length_m: number; points: [number, number][] }[];
  tree: { root: TreeNode; train_accuracy: number; n_samples: number; labels: Record<string, string>; classes: string[] };
  explanations: Record<string, Explanation>;
  scenarios: { name: string; label: string; hours: number | null; severity: string }[];
  site_scores: {
    criteria: ScoreRow[];
    totals: { chelsea: number; lansing: number };
    robustness: { n_draws: number; p_chelsea_wins: number; difference_histogram: { edges: number[]; counts: number[] } };
    recommended: string;
  };
  deal: {
    cleared: boolean; p_ahead: Record<string, number>; median_npv_usd: Record<string, number>;
    premiums_usd: Record<string, number>; changes: string[]; binding_parties: string[];
    gap_usd_per_yr: Record<string, number>; n_futures: number; candidates_tried: number;
  };
  annual: {
    summary: Record<string, number>;
    impact: {
      heat_delivered_mwh: number;
      net_co2_avoided_t: { central: number; low: number; high: number };
      net_co2_t_per_mw_yr: { central: number; low: number; high: number };
      erf: number; ere: number;
      water: { net_water_saved_m3: number; tower_water_avoided_m3: number; indirect_water_added_m3: number } | null;
      scorecard: { category: string; baseline: number; with_heatos: number; baseline_basis: string; change_basis: string }[];
    };
    parties: PartyEconomics[];
  };
  report_md?: string;
  alternatives?: Alternatives | null;
  autopilot_compare?: Record<string, { delta_mpc_minus_rules: Record<string, number>; rules: Record<string, number>; mpc: Record<string, number> }>;
}
