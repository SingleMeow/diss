"""Request/response schemas for the simulation API.

Only request bodies are modelled as Pydantic — they need validation. Responses
are plain dicts assembled in `app.core.session.SimulationSession`, which keeps
the simulation-facing code free of web-framework concerns (FastAPI serialises
dicts/dataclasses to JSON natively).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LogisticsConfigIn(BaseModel):
    road_cost_per_ton_km: float = 4.5
    rail_cost_per_ton_km: float = 1.8
    elevator_handling_fee_per_ton: float = 350.0
    rail_min_distance_km: float = 250.0


class GovernmentPolicyIn(BaseModel):
    # Fiscal levers
    direct_tax_rate: float = Field(default=0.06, ge=0.0, le=1.0)              # withheld from each deal's seller revenue
    export_fee_per_ton: dict[str, float] = Field(default_factory=dict)        # crop_id -> RUB/ton charged from the exporter
    subsidy_per_ha: dict[str, float] = Field(default_factory=dict)            # crop_id -> RUB/ha at sowing
    subsidy_per_ton: dict[str, float] = Field(default_factory=dict)           # crop_id -> RUB/ton sold
    # Trade policy / interventions
    export_duty_rate: dict[str, float] = Field(default_factory=dict)
    intervention_floor_price: dict[str, float] = Field(default_factory=dict)
    intervention_ceiling_price: dict[str, float] = Field(default_factory=dict)
    intervention_volume_share: float = 0.15


class CropOverrideIn(BaseModel):
    """Lets the UI tweak an existing crop's parameters or define a brand-new one."""
    id: str
    name: str
    base_yield_t_per_ha: float
    yield_volatility: float = 0.15
    sowing_cost_per_ha: float
    storage_loss_rate_per_month: float = 0.01
    rotation_group: str | None = None
    suitable_zones: list[str] = Field(default_factory=lambda: [
        "south", "black_earth", "volga", "temperate",
        "west_siberia", "east_siberia", "far_east", "north",
    ])
    # Sowing calendar (per crop, not per zone). Winter crops: season="winter",
    # sown in autumn (e.g. 9), harvested next summer (e.g. 7).
    season: str = "spring"
    sowing_month: int = Field(default=5, ge=1, le=12)
    harvest_month: int = Field(default=9, ge=1, le=12)
    # The market this variety sells into (defaults to its own id/name — set this
    # to an existing good, e.g. "wheat", to make a new variety trade in that
    # same market rather than as a separate commodity).
    market_good: str | None = None
    market_good_name: str | None = None


class CustomFarmerIn(BaseModel):
    id: str
    name: str | None = None
    region_id: str
    lat: float | None = None
    lon: float | None = None
    climate_zone: str | None = None
    total_area_ha: float
    storage_capacity_tons: float | None = None
    allowed_crop_ids: list[str]
    cash: float | None = None
    target_margin: float | None = None
    base_sell_fraction: float | None = None
    price_adaptation_speed: float | None = None


class CustomBuyerIn(BaseModel):
    id: str
    name: str | None = None
    region_id: str
    lat: float | None = None
    lon: float | None = None
    buyer_type: str = "trader"
    monthly_consumption: dict[str, float]
    storage_capacity_tons: float | None = None
    output_price: dict[str, float] = Field(default_factory=dict)
    output_price_series: dict[str, list[float]] = Field(default_factory=dict)  # overrides output_price
    processing_margin: float | None = None
    flexibility: float | None = Field(default=None, ge=0.0, le=1.0)
    max_debt: float | None = None
    cash: float | None = None


class CustomExporterIn(BaseModel):
    id: str
    name: str | None = None
    region_id: str
    lat: float | None = None
    lon: float | None = None
    destination_country: str | None = None
    handled_crop_ids: list[str]
    monthly_capacity_tons: dict[str, float]
    flexibility: float | None = Field(default=None, ge=0.0, le=1.0)
    cash: float | None = None


class ScenarioConfigIn(BaseModel):
    seed: int = 42
    start_year: int = 2024
    start_month: int = Field(default=1, ge=1, le=12)

    num_farmers: int = Field(default=150, ge=1, le=5000)
    num_buyers: int = Field(default=45, ge=1, le=2000)

    # market_scale: multiplier on exporter monthly_capacity_tons.
    # None = auto (num_farmers / 10 000); 1.0 = real-world throughput scale.
    market_scale: float | None = Field(default=None, ge=0.0)

    # Maximum debt (RUB) before an agent suspends purchases / planting.
    buyer_max_debt: float = Field(default=50_000_000, ge=0)
    farmer_max_debt: float = Field(default=20_000_000, ge=0)

    # Farm lifecycle.
    # farm_closure_months: consecutive insolvent months before a farm is removed;
    #   0 disables automatic closure.
    # farm_entry_rate_max: maximum probability (0–1) per region per year that a
    #   new farm enters; 0 disables entry.
    # farm_entry_profitability_ha: EMA profitability (RUB/ha/month) at which the
    #   entry probability reaches farm_entry_rate_max (linear below).
    farm_closure_months: int = Field(default=24, ge=0)
    farm_entry_rate_max: float = Field(default=0.20, ge=0.0, le=1.0)
    farm_entry_profitability_ha: float = Field(default=4_000.0, ge=0.0)

    # Buyer lifecycle (symmetric to the farm fields above).
    buyer_closure_months: int = Field(default=24, ge=0)
    buyer_entry_rate_max: float = Field(default=0.15, ge=0.0, le=1.0)
    buyer_entry_profitability: float = Field(default=2_000_000.0, ge=0.0)

    # Annual fixed operating cost per hectare (land rent + depreciation + overhead);
    # charged monthly regardless of activity. 0 disables (variable-cost-only).
    farmer_fixed_cost_per_ha_per_year: float = Field(default=9_000.0, ge=0.0)

    # Exogenous global-market dynamics: the seasonal world-price series is scaled
    # each month by a global commodity shock and the RUB/USD rate, both
    # mean-reverting AR(1) in log space. Set the volatilities to 0 for a purely
    # deterministic world-price feed.
    fx_base: float = Field(default=90.0, gt=0.0)
    fx_volatility: float = Field(default=0.025, ge=0.0)
    fx_reversion: float = Field(default=0.05, ge=0.0, le=1.0)
    world_price_volatility: float = Field(default=0.04, ge=0.0)
    world_price_reversion: float = Field(default=0.10, ge=0.0, le=1.0)

    crop_ids: list[str] | None = None
    region_ids: list[str] | None = None
    crop_overrides: list[CropOverrideIn] = Field(default_factory=list)

    extra_farmers: list[CustomFarmerIn] = Field(default_factory=list)
    extra_buyers: list[CustomBuyerIn] = Field(default_factory=list)
    extra_exporters: list[CustomExporterIn] = Field(default_factory=list)

    logistics: LogisticsConfigIn = Field(default_factory=LogisticsConfigIn)
    government_policy: GovernmentPolicyIn = Field(default_factory=GovernmentPolicyIn)

    # crop_id -> monthly FOB world-price series (RUB/ton); cycles if the run outlasts it.
    # Defaults in ScenarioConfig use a seasonal sine-wave; pass explicit series to override.
    world_prices: dict[str, list[float]] | None = None


class StepRequest(BaseModel):
    n: int = Field(default=1, ge=1, le=240)


# --------------------------------------------------------------------- live "scenario manipulation"
class GovernmentPolicyPatch(BaseModel):
    """Partial government-policy update applied to a *running* simulation.
    Every field is optional; scalars are set when present, dict fields are
    merged key-by-key (so sending one crop leaves the others untouched)."""
    direct_tax_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    export_fee_per_ton: dict[str, float] | None = None
    export_duty_rate: dict[str, float] | None = None
    subsidy_per_ha: dict[str, float] | None = None
    subsidy_per_ton: dict[str, float] | None = None
    intervention_floor_price: dict[str, float] | None = None
    intervention_ceiling_price: dict[str, float] | None = None
    intervention_volume_share: float | None = Field(default=None, ge=0.0, le=1.0)


class CropParamsPatch(BaseModel):
    """Partial crop-parameter update applied to a running simulation."""
    id: str
    base_yield_t_per_ha: float | None = Field(default=None, gt=0.0)
    yield_volatility: float | None = Field(default=None, ge=0.0)
    sowing_cost_per_ha: float | None = Field(default=None, ge=0.0)


class WeatherShockPatch(BaseModel):
    """A deliberate yield shock staged through the manipulation API. The factors
    are multipliers on harvested yield (1.0 = normal, 0.7 = 30 % failure,
    1.2 = 20 % bumper); they persist until changed. `national_factor` scales
    every region; `regional_factors` scales individual regions on top of it."""
    national_factor: float | None = Field(default=None, ge=0.0)
    regional_factors: dict[str, float] | None = None      # region_id -> yield multiplier


class InterveneRequest(BaseModel):
    """A live "shock" applied to the running model. All sections optional —
    only what is supplied is changed; everything else keeps running as-is."""
    government_policy: GovernmentPolicyPatch | None = None
    crops: list[CropParamsPatch] = Field(default_factory=list)
    world_prices: dict[str, float] | None = None          # crop_id -> flat FOB price level (RUB/ton)
    export_volume_factors: dict[str, float] | None = None # crop_id -> multiplier on baseline export capacity
    weather: WeatherShockPatch | None = None              # deliberate national/regional yield shock
