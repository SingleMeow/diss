"""Scenario generation: turns configuration + seed reference data into a `World`.

This is the "adaptive platform" knob the brief asks for: the user can change
the number of agents, their distribution, crop parameters, logistics costs,
government policy and world prices entirely through configuration (and, via
the API, through the UI) without touching the simulation code. The defaults
seed a small synthetic population whose regional distribution follows real
proxies — farmland area for farmers, rural population for buyers — but every
count and parameter is overridable, and individual hand-placed agents can be
appended on top for hypothesis testing.

`agent_parameters.json` is the single source of truth for all numeric model
parameters.  `ScenarioConfig` reads its defaults from there at import time so
that editing the JSON file is sufficient to change model behaviour — no code
changes required.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from app.simulation.agents.buyer import Buyer, BuyerType
from app.simulation.agents.exporter import Exporter
from app.simulation.agents.farmer import Farmer
from app.simulation.agents.government import Government, GovernmentPolicy
from app.simulation.crops import CropRegistry
from app.simulation.geo import ClimateZone, Region, RegionRegistry
from app.simulation.logistics import LogisticsConfig
from app.simulation.world import World

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

def _load_agent_parameters() -> dict:
    """Load all tunable agent parameters from JSON config file."""
    with open(DATA_DIR / "agent_parameters.json", encoding="utf-8") as fh:
        return json.load(fh)

AGENT_PARAMETERS = _load_agent_parameters()

# --------------------------------------------------------------------- shortcuts
_fd  = AGENT_PARAMETERS["farmer_defaults"]
_fas = AGENT_PARAMETERS["farmer_area_and_storage"]
_fb  = AGENT_PARAMETERS["farmer_behavior"]
_bd  = AGENT_PARAMETERS["buyer_defaults"]
_bb  = AGENT_PARAMETERS["buyer_behavior"]
_bde = AGENT_PARAMETERS["buyer_demand_elasticity"]
_bsi = AGENT_PARAMETERS["buyer_strategic_inventory"]
_bic = AGENT_PARAMETERS["buyer_initial_cash"]
_ed  = AGENT_PARAMETERS["exporter_defaults"]
_gd  = AGENT_PARAMETERS["government_defaults"]
_wc  = AGENT_PARAMETERS["world_config"]
_sd  = AGENT_PARAMETERS["scenario_defaults"]
_lc  = AGENT_PARAMETERS["farm_lifecycle"]
_wp  = AGENT_PARAMETERS["world_prices"]
_wth = AGENT_PARAMETERS["weather"]
_wd  = AGENT_PARAMETERS["world_dynamics"]
_cz  = AGENT_PARAMETERS["climate_zones"]


def _zone_cfg(zone_value: str) -> dict:
    """Climate-zone config (yield multiplier + farm-size range) for a zone id."""
    return _cz.get(zone_value, {"yield_multiplier": 1.0, "farmer_area_ha_range": [400, 6000]})

# Convert JSON profiles to the old BUYER_PROFILES format for backward compatibility
def _build_buyer_profiles() -> dict[BuyerType, dict]:
    params = AGENT_PARAMETERS["buyer_profiles"]

    def _profile(key: str) -> dict:
        p = params[key]
        return {
            "crops": p["crops"],
            "scale": tuple(p["monthly_consumption_range_tons"]),
            "flexibility": tuple(p["flexibility_range"]),
            "margin": p["processing_margin"],
            "output_price": p["output_prices"],
            "demand_elasticity": p.get("demand_elasticity", _bde["default_elasticity"]),
        }

    return {
        BuyerType.FLOUR_MILL: _profile("flour_mill"),
        BuyerType.FEED_PRODUCER: _profile("feed_producer"),
        BuyerType.FOOD_PROCESSOR: _profile("food_processor"),
        BuyerType.TRADER: _profile("trader"),
    }

BUYER_PROFILES = _build_buyer_profiles()


def _seasonal_prices(base: float, amplitude: float = 0.08, peak_month: int = 5) -> list[float]:
    """12-month price cycle with a sine-wave seasonal component.

    Prices peak in `peak_month` (1-12) and bottom six months later, matching
    the typical pre-harvest scarcity / post-harvest glut pattern.  amplitude
    is the half-range as a fraction of the base (e.g. 0.08 → ±8 %).
    """
    return [
        round(base * (1.0 + amplitude * math.sin(2 * math.pi * (m - peak_month) / 12)))
        for m in range(1, 13)
    ]


def load_json(filename: str) -> list[dict]:
    with open(DATA_DIR / filename, encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class ScenarioConfig:
    seed: int = _sd["seed"]
    start_year: int = 2024
    start_month: int = 1

    num_farmers: int = _sd["num_farmers"]
    num_buyers: int = _sd["num_buyers"]

    # market_scale: multiplier applied to all exporter monthly_capacity_tons.
    # None  → auto-computed as num_farmers / market_scale_reference_farmers
    # 1.0   → use the raw JSON values (real-world throughput scale)
    market_scale: float | None = None

    # insolvency thresholds: agents with |cash| > threshold stop buying/planting
    # to prevent unlimited debt accumulation; 0 disables the check.
    buyer_max_debt: float  = _lc["buyer_max_debt"]
    farmer_max_debt: float = _lc["farmer_max_debt"]

    # Farm lifecycle parameters
    farm_closure_months: int        = _lc["farm_closure_months"]
    farm_entry_rate_max: float      = _lc["farm_entry_rate_max"]
    farm_entry_profitability_ha: float = _lc["farm_entry_profitability_ha"]

    # Buyer lifecycle parameters (symmetric to the farm ones)
    buyer_closure_months: int       = _lc["buyer_closure_months"]
    buyer_entry_rate_max: float     = _lc["buyer_entry_rate_max"]
    buyer_entry_profitability: float = _lc["buyer_entry_profitability"]

    # Annual fixed operating cost per hectare (land rent + depreciation + overhead)
    farmer_fixed_cost_per_ha_per_year: float = _fd["fixed_cost_per_ha_per_year"]

    # Exogenous global-market dynamics (stochastic world price + FX channel)
    fx_base: float                  = _wd["fx_base"]
    fx_volatility: float            = _wd["fx_volatility"]
    fx_reversion: float             = _wd["fx_reversion"]
    world_price_volatility: float   = _wd["world_price_volatility"]
    world_price_reversion: float    = _wd["world_price_reversion"]

    crop_ids: list[str] | None = None          # None => use every crop in crops.json
    region_ids: list[str] | None = None        # None => use every region in regions.json
    crop_overrides: list[dict] = field(default_factory=list)
    extra_farmers: list[dict] = field(default_factory=list)
    extra_buyers: list[dict] = field(default_factory=list)
    extra_exporters: list[dict] = field(default_factory=list)

    logistics: LogisticsConfig = field(default_factory=LogisticsConfig)

    # Default policy sources intervention_volume_share from JSON; callers can
    # pass a fully custom GovernmentPolicy to override any or all fields.
    government_policy: GovernmentPolicy = field(
        default_factory=lambda: GovernmentPolicy(
            intervention_volume_share=_gd["intervention_volume_share"],
            direct_tax_rate=_gd["direct_tax_rate"],
        )
    )

    # crop_id -> 12-month FOB world-price cycle (RUB/ton); repeats if run is longer.
    # Built from agent_parameters.json:world_prices (base, amplitude, peak_month per crop).
    world_prices: dict[str, list[float]] = field(
        default_factory=lambda: {
            crop_id: _seasonal_prices(p["base"], p["amplitude"], p["peak_month"])
            for crop_id, p in _wp.items()
            if not crop_id.startswith("comment")
        }
    )


def build_world(config: ScenarioConfig | None = None) -> World:
    config = config or ScenarioConfig()
    rng = random.Random(config.seed)

    region_dicts = load_json("regions.json")
    if config.region_ids is not None:
        region_dicts = [r for r in region_dicts if r["id"] in config.region_ids]
    regions = RegionRegistry.from_dicts(region_dicts)

    crop_dicts = load_json("crops.json")
    if config.crop_overrides:
        by_id = {c["id"]: c for c in crop_dicts}
        for override in config.crop_overrides:
            by_id[override["id"]] = override
        crop_dicts = list(by_id.values())
    if config.crop_ids is not None:
        crop_dicts = [c for c in crop_dicts if c["id"] in config.crop_ids]
    crops = CropRegistry.from_dicts(crop_dicts)

    market_scale = (
        config.market_scale
        if config.market_scale is not None
        else config.num_farmers / _sd["market_scale_reference_farmers"]
    )
    exporters = _load_exporters(regions, crops, market_scale)
    exporters += [_exporter_from_dict(d, regions) for d in config.extra_exporters]

    government = Government(policy=config.government_policy)

    world = World(
        regions=regions,
        crops=crops,
        logistics=config.logistics,
        farmers=[],
        buyers=[],
        exporters=exporters,
        government=government,
        world_prices={cid: list(series) for cid, series in config.world_prices.items()},
        year=config.start_year,
        month=config.start_month,
        seed=config.seed,
        farm_closure_months=config.farm_closure_months,
        farm_entry_rate_max=config.farm_entry_rate_max,
        farm_entry_profitability_ha=config.farm_entry_profitability_ha,
        farmer_max_debt=config.farmer_max_debt,
        farmer_fixed_cost_per_ha_per_year=config.farmer_fixed_cost_per_ha_per_year,
        buyer_closure_months=config.buyer_closure_months,
        buyer_entry_rate_max=config.buyer_entry_rate_max,
        buyer_entry_profitability=config.buyer_entry_profitability,
        buyer_max_debt=config.buyer_max_debt,
        price_history_window=_wc["price_history_window_months"],
        demand_center_lat=_wc["demand_center_lat"],
        demand_center_lon=_wc["demand_center_lon"],
        demand_price_premium=_fb["demand_price_premium"],
        demand_pressure_smoothing=_fb["demand_pressure_smoothing"],
        surplus_bargaining_power=_fb["surplus_bargaining_power"],
        weather_national_weight=_wth["national_weight"],
        weather_regional_weight=_wth["regional_weight"],
        weather_idiosyncratic_weight=_wth["idiosyncratic_weight"],
        fx_base=config.fx_base,
        fx_rate=config.fx_base,
        fx_volatility=config.fx_volatility,
        fx_reversion=config.fx_reversion,
        world_price_volatility=config.world_price_volatility,
        world_price_reversion=config.world_price_reversion,
    )

    # Consumption (processing/trading) is seeded by total population, excluding
    # the export hubs (border points are pure outflow nodes, not domestic demand
    # — consistent with the lifecycle entry rule that also skips them).
    populated_regions = [r for r in regions if not r.is_border and r.population > 0]
    buyers = _generate_buyers(config, rng, populated_regions, crops, world)
    buyers += [_buyer_from_dict(d, regions) for d in config.extra_buyers]
    world.buyers = buyers
    for b in buyers:
        world.register_agent(b)

    farmland_regions = [r for r in regions if not r.is_border and r.sown_area_ha > 0]
    farmers = _generate_farmers(config, rng, farmland_regions, crops, world)
    farmers += [_farmer_from_dict(d, regions, rng) for d in config.extra_farmers]
    world.farmers = farmers
    for f in farmers:
        world.register_agent(f)
    return world


# ---------------------------------------------------------------------------- farmers
def _jitter(rng: random.Random, lat: float, lon: float, spread_deg: float) -> tuple[float, float]:
    return lat + rng.uniform(-spread_deg, spread_deg), lon + rng.uniform(-spread_deg, spread_deg)


def _weighted_sample(rng: random.Random, items: list, weights: list[float], k: int) -> list:
    """Weighted sampling *without replacement* of `k` items (Efraimidis–Spirakis
    A-Res: key = u^(1/w), take the k largest keys). Dependency-free; deterministic
    from `rng`. Used to draw a farm's crop menu weighted by prevalence."""
    if k <= 0 or not items:
        return []
    keyed = [(rng.random() ** (1.0 / max(w, 1e-9)), i) for i, w in enumerate(weights)]
    keyed.sort(reverse=True)
    return [items[i] for _, i in keyed[:k]]


def _farmer_behavior_kwargs() -> dict:
    """Return the farmer_behavior section of agent_parameters.json as Farmer constructor kwargs."""
    return {
        "cash_ema_alpha":                  _fb["cash_ema_alpha"],
        "sale_expectation_smoothing":      _fb["sale_expectation_smoothing"],
        "acreage_inertia":                 _fb["acreage_inertia"],
        "storage_fill_pressure_multiplier": _fb["storage_fill_pressure_multiplier"],
        "storage_fill_threshold":          _fb["storage_fill_threshold"],
        "max_storage_discount":            _fb["max_storage_discount"],
        "discount_activation_threshold":   _fb["discount_activation_threshold"],
        "discount_ramp_rate":              _fb["discount_ramp_rate"],
        "low_fill_pressure_multiplier":    _fb["low_fill_pressure_multiplier"],
    }


def _spawn_single_farmer(region: Region, crops: CropRegistry, world: "World", rng: random.Random) -> Farmer:
    """Create one new entrant farm for `region` and register it with `world`.

    Used both during initial generation (via `_generate_farmers`) and at runtime
    when the lifecycle engine decides that regional profitability warrants entry.
    The farm counter on `world` is incremented so IDs stay unique across the run.

    All parameters come from agent_parameters.json — edit that file to tune behavior.
    """
    world._farm_counter += 1
    counter = world._farm_counter
    zone_crops = [c for c in crops if region.climate_zone.value in c.suitable_zones]
    zone_crop_ids = [c.id for c in zone_crops]
    lat, lon = _jitter(rng, region.lat, region.lon, _sd["farmer_location_jitter_deg"])

    zone_cfg = _zone_cfg(region.climate_zone.value)
    area = rng.uniform(*zone_cfg["farmer_area_ha_range"])

    # Draw the farm's crop menu weighted by prevalence (national area share), so
    # major crops are near-universal and niche crops rare — see CropType.prevalence.
    crops_min, crops_max = _sd["crops_per_farmer_range"]
    k = min(len(zone_crops), rng.randint(crops_min, crops_max))
    allowed = [c.id for c in _weighted_sample(rng, zone_crops, [c.prevalence for c in zone_crops], k)] or zone_crop_ids[:1]

    # Force the staple market good(s) (wheat) into every farm's crop list — wheat
    # is grown on almost all Russian grain farms and is the high-cap residual
    # filler in the planting allocation. Add a zone-suitable variety if absent.
    for good in _sd.get("staple_market_goods", []):
        if any(crops.get(cid).market_good == good for cid in allowed):
            continue
        staple_ids = [cid for cid in zone_crop_ids if crops.get(cid).market_good == good]
        if staple_ids:
            allowed.append(rng.choice(staple_ids))

    return Farmer(
        id=f"farmer-{counter:04d}",
        name=f"Хозяйство №{counter} ({region.name})",
        region_id=region.id,
        lat=lat, lon=lon,
        climate_zone=region.climate_zone,
        zone_yield_multiplier=zone_cfg["yield_multiplier"],
        zone_cost_multiplier=zone_cfg.get("cost_multiplier", 1.0),
        total_area_ha=area,
        storage_capacity_tons=area * rng.uniform(*_fas["storage_capacity_multiplier_range"]),
        allowed_crop_ids=allowed,
        cash=area * rng.uniform(*_fas["initial_cash_per_ha_range"]),
        target_margin=rng.uniform(*_fd["target_margin_range"]),
        base_sell_fraction=rng.uniform(*_fd["base_sell_fraction_range"]),
        price_adaptation_speed=rng.uniform(*_fd["price_adaptation_speed_range"]),
        credit_rate_per_month=_fd["credit_rate_per_month"],
        fixed_cost_per_ha_per_year=world.farmer_fixed_cost_per_ha_per_year,
        max_debt=max(world.farmer_max_debt, area * _sd["max_debt_per_ha_multiplier"]),
        rng=random.Random(rng.randint(0, 2**31)),
        **_farmer_behavior_kwargs(),
    )


def _entry_probability(
    regional_farmers: list[Farmer],
    max_rate: float,
    target_profit_ha: float,
) -> float:
    """Compute the annual probability of a new farm entering a region.

    The signal is the average cash-EMA per hectare across existing farmers.
    At zero or negative profitability → no entry; at `target_profit_ha`
    RUB/ha/month → `max_rate`.  An empty region gets a small baseline so
    abandoned farmland can be recolonised after favourable price shifts.
    The baseline fraction is sourced from
    agent_parameters.json:scenario_defaults.empty_region_entry_baseline_fraction.
    """
    if not regional_farmers:
        return max_rate * _sd["empty_region_entry_baseline_fraction"]
    valid = [f for f in regional_farmers if f.cash_ema is not None]
    if not valid:
        return 0.0
    avg_per_ha = sum(f.cash_ema / max(f.total_area_ha, 1.0) for f in valid) / len(valid)
    t = max(0.0, min(1.0, avg_per_ha / max(target_profit_ha, 1.0)))
    return t * max_rate


def _apportion(weights: list[float], total: int) -> list[int]:
    """Distribute `total` agents across regions proportionally to `weights`
    using the largest-remainder (Hamilton) method.

    Unlike the old `max(1, round(total * share))` rule — which forced *every*
    region to at least one agent, scattering spurious farms across regions with
    negligible sown area (Murmansk, Chukotka, …) and inflating the realised
    count well past `total` — this:

      * gives a region with zero weight exactly zero agents (no farms where
        nothing is grown), and
      * makes the realised counts sum to *exactly* `total`, so `num_farmers` /
        `num_buyers` mean what they say.
    """
    n = len(weights)
    sw = sum(weights)
    if n == 0 or sw <= 0 or total <= 0:
        return [0] * n
    quotas = [total * w / sw for w in weights]
    base = [int(q) for q in quotas]
    remaining = total - sum(base)
    # Hand out the leftover seats to the largest fractional remainders, but only
    # to regions that actually have weight (so zero-weight regions stay at zero).
    order = sorted(
        (i for i in range(n) if weights[i] > 0),
        key=lambda i: quotas[i] - base[i],
        reverse=True,
    )
    for k in range(min(remaining, len(order))):
        base[order[k]] += 1
    return base


def _generate_farmers(config: ScenarioConfig, rng: random.Random, regions: list[Region], crops: CropRegistry, world: "World") -> list[Farmer]:
    if not regions:
        return []
    counts = _apportion([r.sown_area_ha for r in regions], config.num_farmers)
    farmers: list[Farmer] = []
    for region, n in zip(regions, counts):
        for _ in range(n):
            farmers.append(_spawn_single_farmer(region, crops, world, rng))
    return farmers


def _farmer_from_dict(d: dict, regions: RegionRegistry, rng: random.Random) -> Farmer:
    region = regions.get(d["region_id"])
    zone_value = d.get("climate_zone", region.climate_zone.value)
    return Farmer(
        id=d["id"], name=d.get("name", d["id"]), region_id=region.id,
        lat=d.get("lat", region.lat), lon=d.get("lon", region.lon),
        climate_zone=ClimateZone(zone_value),
        zone_yield_multiplier=_zone_cfg(zone_value)["yield_multiplier"],
        zone_cost_multiplier=_zone_cfg(zone_value).get("cost_multiplier", 1.0),
        total_area_ha=d["total_area_ha"],
        storage_capacity_tons=d.get("storage_capacity_tons", d["total_area_ha"]),
        allowed_crop_ids=d["allowed_crop_ids"],
        cash=d.get("cash", d["total_area_ha"] * _sd["farmer_fallback_cash_per_ha"]),
        target_margin=d.get("target_margin", _fd["target_margin"]),
        base_sell_fraction=d.get("base_sell_fraction", _fd["base_sell_fraction"]),
        price_adaptation_speed=d.get("price_adaptation_speed", _fd["price_adaptation_speed"]),
        credit_rate_per_month=_fd["credit_rate_per_month"],
        fixed_cost_per_ha_per_year=d.get("fixed_cost_per_ha_per_year", _fd["fixed_cost_per_ha_per_year"]),
        max_debt=d.get("max_debt", max(_lc["farmer_max_debt"], d["total_area_ha"] * _sd["max_debt_per_ha_multiplier"])),
        rng=random.Random(rng.randint(0, 2**31)),
        **_farmer_behavior_kwargs(),
    )


# ---------------------------------------------------------------------------- buyers
def _buyer_behavior_kwargs() -> dict:
    """Return the buyer_behavior section of agent_parameters.json as Buyer constructor kwargs."""
    return {
        "fallback_target_markup":  _bb["fallback_target_price_markup"],
        "trader_resale_markup":    _bb["trader_resale_markup"],
        "actual_cost_ema_alpha":   _bb["actual_cost_ema_alpha"],
        "target_inventory_months": _bb["target_inventory_months"],
        "cash_ema_alpha":          _fb["cash_ema_alpha"],
        # Price-elastic demand (type-independent bounds; per-type elasticity is
        # passed separately from the buyer profile).
        "price_elastic_demand":    _bde["enabled"],
        "min_demand_factor":       _bde["min_demand_factor"],
        "max_demand_factor":       _bde["max_demand_factor"],
        # Strategic / mean-reversion inventory.
        "strategic_inventory_enabled":   _bsi["enabled"],
        "price_anchor_adaptation_speed": _bsi["anchor_adaptation_speed"],
        "speculation_sensitivity":       _bsi["sensitivity"],
        "cover_multiplier_floor":        _bsi["cover_multiplier_floor"],
        "cover_multiplier_ceil":         _bsi["cover_multiplier_ceil"],
    }


def _spawn_single_buyer(region: Region, crops: CropRegistry, world: "World", rng: random.Random) -> Buyer | None:
    """Create one new entrant buyer (processor/trader) for `region` and register
    its id counter on `world`. Used both during initial generation and at
    runtime when the lifecycle engine decides regional processing profitability
    warrants entry. Returns None if the randomly-drawn buyer type handles no
    crop that exists in this scenario.

    All parameters come from agent_parameters.json — edit that file to tune behavior.
    """
    available_goods = set(crops.market_goods())   # buyers trade market goods, not varieties
    buyer_type = rng.choice(list(BUYER_PROFILES.keys()))
    profile = BUYER_PROFILES[buyer_type]
    crop_ids = [c for c in profile["crops"] if c in available_goods]
    if not crop_ids:
        return None

    crops_min, crops_max = _sd["crops_per_buyer_range"]
    chosen = rng.sample(crop_ids, k=min(len(crop_ids), rng.randint(crops_min, crops_max)))
    scale_lo, scale_hi = profile["scale"]
    consumption = {cid: rng.uniform(scale_lo, scale_hi) for cid in chosen}
    flex_lo, flex_hi = profile["flexibility"]
    lat, lon = _jitter(rng, region.lat, region.lon, _sd["buyer_location_jitter_deg"])

    world._buyer_counter += 1
    counter = world._buyer_counter
    return Buyer(
        id=f"buyer-{counter:04d}",
        name=f"{_buyer_type_label(buyer_type)} №{counter} ({region.name})",
        region_id=region.id,
        lat=lat, lon=lon,
        buyer_type=buyer_type,
        monthly_consumption=consumption,
        storage_capacity_tons=sum(consumption.values()) * rng.uniform(*_bic["storage_capacity_multiplier"]),
        output_price={cid: profile["output_price"][cid] for cid in chosen if cid in profile["output_price"]},
        processing_margin=profile["margin"],
        flexibility=rng.uniform(flex_lo, flex_hi),
        cash=rng.uniform(*_bic["range_rubles"]),
        max_debt=world.buyer_max_debt,
        demand_elasticity=profile["demand_elasticity"],
        **_buyer_behavior_kwargs(),
    )


def _buyer_entry_probability(
    regional_buyers: list[Buyer],
    max_rate: float,
    target_profit: float,
) -> float:
    """Annual probability of a new buyer entering a region — symmetric to
    `_entry_probability` for farms, but the profitability signal is the average
    cash-EMA per buyer (RUB/month, not per hectare). At zero/negative
    profitability → no entry; at `target_profit` → `max_rate`. An empty region
    gets the same small recolonisation baseline used for farms."""
    if not regional_buyers:
        return max_rate * _sd["empty_region_entry_baseline_fraction"]
    valid = [b for b in regional_buyers if b.cash_ema is not None]
    if not valid:
        return 0.0
    avg = sum(b.cash_ema for b in valid) / len(valid)
    t = max(0.0, min(1.0, avg / max(target_profit, 1.0)))
    return t * max_rate


def _generate_buyers(config: ScenarioConfig, rng: random.Random, regions: list[Region],
                     crops: CropRegistry, world: "World") -> list[Buyer]:
    if not regions:
        return []
    counts = _apportion([r.population for r in regions], config.num_buyers)
    buyers: list[Buyer] = []
    for region, n in zip(regions, counts):
        for _ in range(n):
            buyer = _spawn_single_buyer(region, crops, world, rng)
            if buyer is not None:
                buyers.append(buyer)
    return buyers


def _buyer_type_label(buyer_type: BuyerType) -> str:
    return {
        BuyerType.FLOUR_MILL: "Мукомольный комбинат",
        BuyerType.FEED_PRODUCER: "Комбикормовый завод",
        BuyerType.FOOD_PROCESSOR: "Пищевой комбинат",
        BuyerType.TRADER: "Торговый дом / элеватор",
    }[buyer_type]


def _buyer_from_dict(d: dict, regions: RegionRegistry) -> Buyer:
    region = regions.get(d["region_id"])
    return Buyer(
        id=d["id"], name=d.get("name", d["id"]), region_id=region.id,
        lat=d.get("lat", region.lat), lon=d.get("lon", region.lon),
        buyer_type=BuyerType(d.get("buyer_type", BuyerType.TRADER.value)),
        monthly_consumption=d["monthly_consumption"],
        storage_capacity_tons=d.get("storage_capacity_tons", sum(d["monthly_consumption"].values()) * 3),
        output_price=d.get("output_price", {}),
        output_price_series=d.get("output_price_series", {}),
        processing_margin=d.get("processing_margin", _bd["processing_margin"]),
        flexibility=d.get("flexibility", _bd["flexibility"]),
        max_debt=d.get("max_debt", _bd["max_debt"]),
        cash=d.get("cash", _sd["buyer_fallback_cash_rubles"]),
        demand_elasticity=d.get("demand_elasticity", _bde["default_elasticity"]),
        **_buyer_behavior_kwargs(),
    )


# ---------------------------------------------------------------------------- exporters
def _load_exporters(regions: RegionRegistry, crops: CropRegistry,
                    market_scale: float = 1.0) -> list[Exporter]:
    available_goods = set(crops.market_goods())   # exporters ship market goods, not varieties
    exporters = []
    for d in load_json("border_points.json"):
        try:
            region = regions.get(d["region_id"])
        except KeyError:
            continue
        handled = [c for c in d["handled_crop_ids"] if c in available_goods]
        if not handled:
            continue
        exporters.append(Exporter(
            id=d["id"], name=d["name"], region_id=region.id,
            lat=region.lat, lon=region.lon,
            destination_country=d["destination_country"],
            handled_crop_ids=handled,
            monthly_capacity_tons={
                cid: qty * market_scale
                for cid, qty in d["monthly_capacity_tons"].items()
                if cid in available_goods
            },
            flexibility=d.get("flexibility", _ed["flexibility"]),
            storage_capacity_tons=_ed["storage_capacity_tons"],
            cash=_ed["initial_cash_rubles"],
            **_exporter_volume_kwargs(),
        ))
    return exporters


def _exporter_volume_kwargs() -> dict:
    """Price-responsive export-volume parameters from agent_parameters.json."""
    return {
        "volume_elasticity": _ed["volume_elasticity"],
        "reference_margin":  _ed["reference_margin"],
        "min_volume_factor": _ed["min_volume_factor"],
        "max_volume_factor": _ed["max_volume_factor"],
    }


def _exporter_from_dict(d: dict, regions: RegionRegistry) -> Exporter:
    region = regions.get(d["region_id"])
    return Exporter(
        id=d["id"], name=d.get("name", d["id"]), region_id=region.id,
        lat=d.get("lat", region.lat), lon=d.get("lon", region.lon),
        destination_country=d.get("destination_country", "—"),
        handled_crop_ids=d["handled_crop_ids"],
        monthly_capacity_tons=d["monthly_capacity_tons"],
        flexibility=d.get("flexibility", _ed["flexibility"]),
        storage_capacity_tons=d.get("storage_capacity_tons", _ed["storage_capacity_tons"]),
        cash=d.get("cash", _ed["initial_cash_rubles"]),
        **_exporter_volume_kwargs(),
    )
