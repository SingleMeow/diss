// Mirrors the JSON shapes returned by the FastAPI backend
// (see backend/app/core/session.py and backend/app/schemas/simulation.py).

export interface Region {
  id: string;
  name: string;
  lat: number;
  lon: number;
  climate_zone:
    | "south" | "black_earth" | "volga" | "temperate"
    | "west_siberia" | "east_siberia" | "far_east" | "north";
  sown_area_ha: number;
  population: number;
  is_border: boolean;
}

export interface Crop {
  id: string;
  name: string;
  market_good: string;
  market_good_name: string;
  base_yield_t_per_ha: number;
  yield_volatility: number;
  sowing_cost_per_ha: number;
  storage_loss_rate_per_month: number;
  rotation_group: string | null;
  suitable_zones: string[];
  season: "winter" | "spring";
  sowing_month: number;
  harvest_month: number;
  max_area_share: number;
  prevalence: number;
}

export interface BorderPoint {
  id: string;
  name: string;
  region_id: string;
  destination_country: string;
  handled_crop_ids: string[];
  monthly_capacity_tons: Record<string, number>;
}

export interface AgentBase {
  id: string;
  name: string;
  lat: number;
  lon: number;
  region_id: string;
  cash: number;
}

export interface FarmerAgent extends AgentBase {
  type: "farmer";
  climate_zone: string;
  total_area_ha: number;
  storage_capacity_tons: number;
  storage_tons: number;
  storage_by_crop: Record<string, number>;
  planted_area: Record<string, Record<string, number>>;
  allowed_crop_ids: string[];
  expected_price: Record<string, number>;
  insolvent_months: number;
  cash_ema: number | null;
}

export interface BuyerAgent extends AgentBase {
  type: "buyer";
  buyer_type: string;
  monthly_consumption: Record<string, number>;          // current (price-elastic) throughput
  monthly_consumption_baseline: Record<string, number>; // pre-shock baseline throughput
  demand_elasticity: number;                             // price-elasticity of demand
  target_inventory_months: number;
  expected_price: Record<string, number>;                // smoothed mean-reversion price anchor
  storage_capacity_tons: number;
  storage_tons: number;
  storage_by_crop: Record<string, number>;
  flexibility: number;
  insolvent_months: number;
  cash_ema: number | null;
}

export interface ExporterAgent extends AgentBase {
  type: "exporter";
  destination_country: string;
  handled_crop_ids: string[];
  monthly_capacity_tons: Record<string, number>; // base contract volume per crop
  ship_target: Record<string, number>;           // current margin-flexed volume target
  volume_elasticity: number;
  reference_margin: number;
  storage_tons: number;
  shipped_total: Record<string, number>;
  flexibility: number;
}

export type Agent = FarmerAgent | BuyerAgent | ExporterAgent;

export interface GovernmentSummary {
  cash: number;
  reserves: Record<string, number>;
  subsidies_paid: Record<string, number>;       // per-hectare (sowing) subsidies
  sale_subsidies_paid: Record<string, number>;  // per-ton (sale) subsidies
  taxes_collected: number;                       // cumulative direct tax from deals
  export_fees_collected: Record<string, number>; // cumulative per-ton export fees
}

export interface MarketSummary {
  fx_rate: number;          // current RUB/USD
  fx_base: number;          // reference RUB/USD the base world-price series is quoted at
  world_price_shock: number; // current global commodity-price multiplier
}

export interface SimulationState {
  run_id: number | null;
  year: number;
  month: number;
  step_index: number;
  counts: {
    regions: number;
    crops: number;
    farmers: number;
    buyers: number;
    exporters: number;
  };
  government: GovernmentSummary;
  market: MarketSummary;
  last_step: StepRecord | null;
}

export interface StepRecord {
  year: number;
  month: number;
  national_prices: Record<string, number>;
  traded_volumes: Record<string, number>;
  traded_value: Record<string, number>;
  government_cash: number;
  government_reserves: Record<string, number>;
  taxes_collected: number;
  export_fees_collected: Record<string, number>;
  subsidies_paid: Record<string, number>;
  sale_subsidies_paid: Record<string, number>;
  farmer_count: number;
  buyer_count: number;
  farms_closed: number;
  farms_spawned: number;
  buyers_closed: number;
  buyers_spawned: number;
  fx_rate: number;
  world_price_shock: number;
  total_farmer_storage: number;
  total_buyer_storage: number;
  total_exporter_storage: number;
  total_government_reserves: number;
  total_grain_in_system: number;
  // Per-month grain flows (mass balance): ΔTotal grain ==
  // harvested − consumed − spoiled − exported − dumped.
  harvested_tons: number;
  consumed_tons: number;
  spoiled_tons: number;
  exported_tons: number;
  dumped_tons: number;
}

export interface StepResponse {
  steps: StepRecord[];
  state: SimulationState;
}

// ----------------------------------------------------------- stored runs (DB-backed history)

/** One row of GET /simulation/runs — the run catalogue / picker. */
export interface RunSummary {
  id: number;
  created_at: string;
  config: Partial<ScenarioConfigIn>;
  step_count: number;
  last_year: number | null;
  last_month: number | null;
  updated_at: string | null;
}

/** GET /simulation/runs/{id} — a fully reloaded run, in the same shapes the
 *  live endpoints return, so a past run can be rendered read-only. */
export interface LoadedRun {
  run_id: number;
  created_at: string;
  config: Partial<ScenarioConfigIn>;
  step_index: number;
  updated_at: string | null;
  history: StepRecord[];
  state: SimulationState | null;
  agents: Agent[];
  exports: ExportRecord[];
  market: MarketHistory;
}

export interface MarketSeries {
  months: number[];  // absolute month indices (monotonic since epoch), parallel to prices
  prices: number[];
}

// crop_id -> region_id -> series
export type MarketHistory = Record<string, Record<string, MarketSeries>>;

export interface ExportRecord {
  year: number;
  month: number;
  crop_id: string;
  exporter_id: string;
  destination: string;
  quantity_tons: number;
  revenue_rub: number;
  duty_rub: number;
  fee_rub: number;
}

// ----------------------------------------------------------- scenario manipulation (live levers)

export interface LeverGovernmentPolicy {
  direct_tax_rate: number;
  intervention_volume_share: number;
  export_fee_per_ton: Record<string, number>;
  export_duty_rate: Record<string, number>;
  subsidy_per_ha: Record<string, number>;
  subsidy_per_ton: Record<string, number>;
  intervention_floor_price: Record<string, number>;
  intervention_ceiling_price: Record<string, number>;
}

export interface LeverCrop {
  id: string;
  name: string;
  market_good: string;
  market_good_name: string;
  base_yield_t_per_ha: number;
  yield_volatility: number;
  sowing_cost_per_ha: number;
}

export interface LeverExportVolume {
  factor: number;
  capacity_tons: number;
}

export interface LeverWeather {
  national_factor: number;                    // yield multiplier, 1.0 = normal
  regional_factors: Record<string, number>;   // region_id -> yield multiplier
}

export interface Levers {
  government_policy: LeverGovernmentPolicy;
  crops: LeverCrop[];
  world_prices: Record<string, number | null>;
  export_volumes: Record<string, LeverExportVolume>;
  weather: LeverWeather;
}

export interface GovernmentPolicyPatch {
  direct_tax_rate?: number;
  export_fee_per_ton?: Record<string, number>;
  export_duty_rate?: Record<string, number>;
  subsidy_per_ha?: Record<string, number>;
  subsidy_per_ton?: Record<string, number>;
  intervention_floor_price?: Record<string, number>;
  intervention_ceiling_price?: Record<string, number>;
  intervention_volume_share?: number;
}

export interface CropParamsPatch {
  id: string;
  base_yield_t_per_ha?: number;
  yield_volatility?: number;
  sowing_cost_per_ha?: number;
}

export interface WeatherShockPatch {
  national_factor?: number;
  regional_factors?: Record<string, number>;
}

export interface InterveneRequest {
  government_policy?: GovernmentPolicyPatch;
  crops?: CropParamsPatch[];
  world_prices?: Record<string, number>;
  export_volume_factors?: Record<string, number>;
  weather?: WeatherShockPatch;
}

// ----------------------------------------------------------- add agent (live)

export interface CustomFarmerIn {
  id: string;
  name?: string;
  region_id: string;
  total_area_ha: number;
  storage_capacity_tons?: number;
  allowed_crop_ids: string[];
  cash?: number;
}

export interface CustomBuyerIn {
  id: string;
  name?: string;
  region_id: string;
  buyer_type: string;
  monthly_consumption: Record<string, number>;
  storage_capacity_tons?: number;
  processing_margin?: number;
  flexibility?: number;
  demand_elasticity?: number;
  cash?: number;
}

export interface CustomExporterIn {
  id: string;
  name?: string;
  region_id: string;
  destination_country?: string;
  handled_crop_ids: string[];
  monthly_capacity_tons: Record<string, number>;
  flexibility?: number;
  cash?: number;
}

export interface AddAgentRequest {
  kind: "farmer" | "buyer" | "exporter";
  farmer?: CustomFarmerIn;
  buyer?: CustomBuyerIn;
  exporter?: CustomExporterIn;
}

// ---------------------------------------------------------------------- requests

export interface LogisticsConfigIn {
  road_cost_per_ton_km: number;
  rail_cost_per_ton_km: number;
  elevator_handling_fee_per_ton: number;
  rail_min_distance_km: number;
}

export interface GovernmentPolicyIn {
  direct_tax_rate: number;
  export_fee_per_ton: Record<string, number>;
  subsidy_per_ha: Record<string, number>;
  subsidy_per_ton: Record<string, number>;
  export_duty_rate: Record<string, number>;
  intervention_floor_price: Record<string, number>;
  intervention_ceiling_price: Record<string, number>;
  intervention_volume_share: number;
}

export interface ScenarioConfigIn {
  seed: number;
  start_year: number;
  start_month: number;
  num_farmers: number;
  num_buyers: number;
  market_scale?: number | null;
  buyer_max_debt?: number;
  farmer_max_debt?: number;
  farm_closure_months?: number;
  farm_entry_rate_max?: number;
  farm_entry_profitability_ha?: number;
  buyer_closure_months?: number;
  buyer_entry_rate_max?: number;
  buyer_entry_profitability?: number;
  farmer_fixed_cost_per_ha_per_year?: number;
  fx_base?: number;
  fx_volatility?: number;
  fx_reversion?: number;
  world_price_volatility?: number;
  world_price_reversion?: number;
  crop_ids?: string[] | null;
  region_ids?: string[] | null;
  logistics?: Partial<LogisticsConfigIn>;
  government_policy?: Partial<GovernmentPolicyIn>;
  world_prices?: Record<string, number[]> | null;
}
