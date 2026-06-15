"""World: the mutable state container for one simulation run.

Holds the registries (regions, crops), all agents, the exogenous world-price
feed, the government, the run's shared RNG (so the decentralised market's
buyer-ordering stays reproducible from the scenario seed), and the rolling
history that both agents (for "decisions based on market data") and the
API/UI (for charts) read from.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from app.simulation.agents.base import CropMarketInfo, MarketSnapshot
from app.simulation.agents.buyer import Buyer
from app.simulation.agents.exporter import Exporter
from app.simulation.agents.farmer import Farmer
from app.simulation.agents.government import Government
from app.simulation.crops import CropRegistry
from app.simulation.geo import RegionRegistry, haversine_km
from app.simulation.logistics import LogisticsConfig, transport_cost_per_ton
from app.simulation.weather import WeatherModel

# These constants are kept as module-level defaults only so that code importing
# the symbol still compiles; the authoritative values are the World fields
# price_history_window, demand_center_lat, demand_center_lon (sourced from
# agent_parameters.json:world_config by scenario.build_world).
_PRICE_HISTORY_WINDOW_DEFAULT = 12
_DEMAND_CENTER_LAT_DEFAULT = 53.0
_DEMAND_CENTER_LON_DEFAULT = 50.0


@dataclass
class RegionCropStats:
    prices: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)

    def last_price(self) -> float | None:
        return self.prices[-1] if self.prices else None

    def last_volume(self) -> float:
        return self.volumes[-1] if self.volumes else 0.0


@dataclass
class MonthlyExportRecord:
    year: int
    month: int
    crop_id: str
    exporter_id: str
    destination: str
    quantity_tons: float
    revenue_rub: float
    duty_rub: float
    fee_rub: float = 0.0   # per-ton export fee paid to the government on this shipment


@dataclass
class World:
    regions: RegionRegistry
    crops: CropRegistry
    logistics: LogisticsConfig
    farmers: list[Farmer]
    buyers: list[Buyer]
    exporters: list[Exporter]
    government: Government
    world_prices: dict[str, list[float]]   # crop_id -> monthly FOB price series (RUB/ton, cycles if shorter than the run)

    year: int = 2024
    month: int = 1   # 1..12; the run starts at the *end* of this month and advances forward
    seed: int = 0

    # Farm lifecycle parameters (set by scenario.build_world from ScenarioConfig)
    farm_closure_months: int = 24          # consecutive insolvent months before a farm is closed
    farm_entry_rate_max: float = 0.20      # max probability of a new farm entering a region per year
    farm_entry_profitability_ha: float = 4_000.0  # RUB/ha/month EMA at which entry prob reaches the maximum
    farmer_max_debt: float = 20_000_000    # used when spawning new farms at runtime
    farmer_fixed_cost_per_ha_per_year: float = 9_000.0  # used when spawning new farms at runtime

    # Buyer lifecycle parameters (symmetric to the farm ones above)
    buyer_closure_months: int = 24            # consecutive insolvent months before a buyer is closed
    buyer_entry_rate_max: float = 0.15        # max probability of a new buyer entering a region per year
    buyer_entry_profitability: float = 2_000_000.0  # RUB/month EMA at which buyer entry prob reaches the maximum
    buyer_max_debt: float = 50_000_000        # debt threshold used when spawning new buyers at runtime

    # --- Stochastic world-market dynamics (exogenous global price + FX channel) ---
    # The base world_prices series is deterministic/seasonal; each month it is
    # scaled by two slow-moving stochastic AR(1) factors so global market
    # conditions actually fluctuate and transmit into the domestic market via
    # exporter netbacks: a common commodity-price shock and the RUB/USD rate.
    fx_base: float = 90.0          # reference RUB/USD the base RUB price series is quoted at
    fx_rate: float = 90.0          # current RUB/USD (evolves each step)
    fx_volatility: float = 0.0     # monthly stdev of log FX changes (0 = fixed FX)
    fx_reversion: float = 0.05     # AR(1) pull of FX back toward fx_base
    world_price_shock: float = 1.0       # current global commodity multiplier (evolves each step)
    world_price_volatility: float = 0.0  # monthly stdev of log world-price shock (0 = deterministic)
    world_price_reversion: float = 0.10  # AR(1) pull of the shock back toward 1.0

    # World-config parameters — sourced from agent_parameters.json:world_config by scenario.build_world
    price_history_window: int = _PRICE_HISTORY_WINDOW_DEFAULT
    demand_center_lat: float = _DEMAND_CENTER_LAT_DEFAULT
    demand_center_lon: float = _DEMAND_CENTER_LON_DEFAULT

    # Weather-correlation weights (variance decomposition of the yield shock —
    # see app.simulation.weather). national+regional+idiosyncratic must sum to 1.
    weather_national_weight: float = 0.40
    weather_regional_weight: float = 0.35
    weather_idiosyncratic_weight: float = 0.25

    # Demand-signal parameters — sourced from agent_parameters.json:farmer_behavior.
    # The clearing price is scaled by demand_price_premium times the *deviation* of
    # the current national supply/demand imbalance from its smoothed baseline (see
    # engine._settle): the structural bias (farmers withhold stock, so requested
    # demand persistently exceeds offered supply) is absorbed into the baseline, so
    # only transient tightness/glut — the spikes we damp, and the swings endogenous
    # demand produces — actually move the price.
    demand_price_premium: float = 0.03
    demand_pressure_smoothing: float = 0.1   # EMA α for the per-crop imbalance baseline
    # Seller's share of the buyer-surplus in the search market's surplus-split
    # pricing — how strongly the demand side lifts the executed price above the
    # seller's ask toward the buyer's valuation. 0 = old "pay the ask" behaviour.
    surplus_bargaining_power: float = 0.0

    rng: random.Random = field(init=False, repr=False, compare=False)
    _stats: dict[tuple[str, str], RegionCropStats] = field(default_factory=dict, init=False)
    # Untrimmed, month-indexed archive of every regional clearing price for the
    # whole run (the `_stats` above is trimmed to price_history_window for the
    # agents' rolling view; this one feeds the full-timeline regional charts).
    # (region_id, crop_id) -> {"months": [month_index...], "prices": [...]}
    _market_archive: dict[tuple[str, str], dict[str, list[float]]] = field(default_factory=dict, init=False)
    _national_price_history: dict[str, list[float]] = field(default_factory=dict, init=False)
    # Smoothed per-crop supply/demand imbalance baseline (EMA), so the price
    # premium reacts to deviations from "normal" tightness, not the structural
    # bias. crop_id -> EMA of (demand - supply)/max(demand, supply).
    _imbalance_ema: dict[str, float] = field(default_factory=dict, init=False)
    export_history: list[MonthlyExportRecord] = field(default_factory=list, init=False)
    step_log: list[dict] = field(default_factory=list, init=False)
    _farm_counter: int = field(default=0, init=False, repr=False)
    _buyer_counter: int = field(default=0, init=False, repr=False)
    weather: WeatherModel = field(init=False, repr=False, compare=False)
    _market_rng: random.Random = field(init=False, repr=False, compare=False)

    # Per-step memoised market snapshots. `build_snapshot` is a pure function of
    # the current market state, but the engine calls it once per agent and many
    # agents share a region — without caching the *identical* snapshot is rebuilt
    # thousands of times a step (a haversine + a loop over every market good
    # each time). A generation counter, bumped whenever the underlying price
    # state changes (a new month, a recorded clearing, a national-price
    # finalisation), invalidates the cache, so phases that run *after* settlement
    # (consumption, demand contraction) still observe fresh post-clearing data.
    _market_gen: int = field(default=0, init=False, repr=False, compare=False)
    _snapshot_cache: dict[str, MarketSnapshot] = field(default_factory=dict, init=False, repr=False, compare=False)
    _snapshot_cache_gen: int = field(default=-1, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        # Dedicated, separately-seeded streams so weather and FX/price dynamics
        # stay reproducible and independent of the market's buyer-shuffling rng.
        self.weather = WeatherModel(
            seed=self.seed,
            national_weight=self.weather_national_weight,
            regional_weight=self.weather_regional_weight,
            idiosyncratic_weight=self.weather_idiosyncratic_weight,
        )
        self._market_rng = random.Random(self.seed + 777)
        self._agents_by_id: dict[str, object] = {self.government.__class__.__name__: self.government}
        self._agents_by_id["government"] = self.government
        for collection in (self.farmers, self.buyers, self.exporters):
            for agent in collection:
                self._agents_by_id[agent.id] = agent
        # Baseline export capacities per exporter, captured at build time, so the
        # live "scenario manipulation" API can scale export volumes by a factor
        # relative to the scenario's original contract sizes (idempotently —
        # re-applying a factor never compounds). crop_id -> factor (default 1.0).
        self._export_capacity_baseline: dict[str, dict[str, float]] = {
            e.id: dict(e.monthly_capacity_tons) for e in self.exporters
        }
        self.export_volume_factors: dict[str, float] = {}
        # Crops whose world price was set to an explicit flat level via the live
        # manipulation API: those bypass the stochastic shock/FX multipliers so a
        # deliberate researcher override stays exactly the value that was set.
        self._pinned_world_crops: set[str] = set()

    # ------------------------------------------------------------------ live "scenario manipulation" levers
    def set_world_price(self, crop_id: str, price: float) -> None:
        """Override a crop's FOB world price with a flat level from now on
        (replaces the seasonal series with a constant). Models a deliberate
        price shock — pinned so the stochastic shock/FX multipliers no longer
        apply and the effective price stays exactly `price`."""
        self.world_prices[crop_id] = [float(price)]
        self._pinned_world_crops.add(crop_id)

    def set_export_volume_factor(self, crop_id: str, factor: float) -> None:
        """Scale every exporter's monthly contract volume for `crop_id` to
        `factor` × its baseline (e.g. 0.0 = export ban, 1.5 = demand surge)."""
        factor = max(0.0, float(factor))
        self.export_volume_factors[crop_id] = factor
        for exporter in self.exporters:
            base = self._export_capacity_baseline.get(exporter.id, {})
            if crop_id in base:
                exporter.monthly_capacity_tons[crop_id] = base[crop_id] * factor

    def export_volume_factor(self, crop_id: str) -> float:
        return self.export_volume_factors.get(crop_id, 1.0)

    def export_capacity_total(self, crop_id: str) -> float:
        """Current effective monthly export capacity for a crop across all exporters."""
        return sum(e.monthly_capacity_tons.get(crop_id, 0.0) for e in self.exporters)

    # ------------------------------------------------------------------ lookups / registry
    def find_agent(self, agent_id: str):
        return self._agents_by_id.get(agent_id)

    def register_agent(self, agent) -> None:
        self._agents_by_id[agent.id] = agent

    def unregister_agent(self, agent_id: str) -> None:
        self._agents_by_id.pop(agent_id, None)

    def world_price_for(self, crop_id: str, month_index: int) -> float | None:
        """Effective FOB world price (RUB/ton) = deterministic seasonal base ×
        the current global commodity shock × the current FX ratio. The two
        multipliers default to 1 (set their volatilities to 0 for a purely
        deterministic series), so this is backward-compatible."""
        series = self.world_prices.get(crop_id)
        if not series:
            return None
        base = series[month_index % len(series)]
        if crop_id in self._pinned_world_crops:
            return base
        return base * self.world_price_shock * (self.fx_rate / self.fx_base)

    def advance_market_dynamics(self) -> None:
        """Step the exogenous global-market state one month forward.

        Both the global commodity-price shock and the RUB/USD rate follow a
        mean-reverting AR(1) in log space (an Ornstein–Uhlenbeck discretisation):
        the shock reverts toward 1.0, the FX rate toward `fx_base`. With zero
        volatility both stay pinned at their reference, recovering the old
        deterministic world-price feed. Drawn from a dedicated RNG so the path
        is reproducible and independent of agent ordering."""
        if self.world_price_volatility > 0:
            log_s = math.log(self.world_price_shock)
            log_s += self.world_price_reversion * (0.0 - log_s) + self.world_price_volatility * self._market_rng.gauss(0.0, 1.0)
            self.world_price_shock = math.exp(log_s)
        if self.fx_volatility > 0:
            log_fx = math.log(self.fx_rate)
            log_fx += self.fx_reversion * (math.log(self.fx_base) - log_fx) + self.fx_volatility * self._market_rng.gauss(0.0, 1.0)
            self.fx_rate = math.exp(log_fx)

    def world_prices_now(self, month_index: int) -> dict[str, float]:
        return {cid: p for cid in self.world_prices if (p := self.world_price_for(cid, month_index)) is not None}

    # ------------------------------------------------------------------ time
    def advance_month(self) -> None:
        if self.month == 12:
            self.month = 1
            self.year += 1
        else:
            self.month += 1
        self._market_gen += 1   # new month → snapshots (month_index, prices) are stale

    @property
    def month_index(self) -> int:
        """Monotonic month counter since simulation epoch (used to index world-price series)."""
        return (self.year - 2024) * 12 + (self.month - 1)

    # ------------------------------------------------------------------ market history
    def _stats_for(self, region_id: str, crop_id: str) -> RegionCropStats:
        key = (region_id, crop_id)
        if key not in self._stats:
            self._stats[key] = RegionCropStats()
        return self._stats[key]

    def record_clearing(self, region_id: str, crop_id: str, price: float | None, volume: float) -> None:
        self._market_gen += 1   # observed prices changed → invalidate cached snapshots
        stats = self._stats_for(region_id, crop_id)
        if price is not None:
            stats.prices.append(price)
            stats.prices[:] = stats.prices[-self.price_history_window:]
            # Untrimmed archive, tagged with the absolute month so the full
            # timeline can be reconstructed even though regions trade sparsely.
            archive = self._market_archive.setdefault((region_id, crop_id), {"months": [], "prices": []})
            archive["months"].append(self.month_index)
            archive["prices"].append(price)
        stats.volumes.append(volume)
        stats.volumes[:] = stats.volumes[-self.price_history_window:]

    def regional_price_archive(self) -> dict[tuple[str, str], dict[str, list[float]]]:
        """Untrimmed, month-indexed regional clearing prices for the whole run —
        the raw material for the full-timeline cross-region spread chart."""
        return self._market_archive

    def finalize_national_prices(self) -> dict[str, float]:
        """Volume-weighted national average price per market good for the month
        just cleared (one price per traded commodity, e.g. one "wheat" price)."""
        self._market_gen += 1   # national averages changed → invalidate cached snapshots
        national: dict[str, float] = {}
        for good in self.crops.market_goods():
            total_value, total_volume = 0.0, 0.0
            for region in self.regions:
                stats = self._stats.get((region.id, good))
                if not stats or not stats.prices:
                    continue
                price, volume = stats.prices[-1], stats.volumes[-1]
                if volume > 0:
                    total_value += price * volume
                    total_volume += volume
            if total_volume > 0:
                avg = total_value / total_volume
                national[good] = avg
                hist = self._national_price_history.setdefault(good, [])
                hist.append(avg)
                hist[:] = hist[-self.price_history_window:]
        return national

    def build_snapshot(self, region_id: str) -> MarketSnapshot:
        """Return a (memoised) market snapshot for a region.

        The snapshot is a pure function of the current market state, so within a
        single generation (no new month / clearing / national-price update) all
        agents in the same region get the *same* cached instance instead of
        rebuilding it. `MarketSnapshot` is read-only by contract, so sharing one
        instance between agents is safe. See `_market_gen`.
        """
        if self._snapshot_cache_gen != self._market_gen:
            self._snapshot_cache.clear()
            self._snapshot_cache_gen = self._market_gen
        cached = self._snapshot_cache.get(region_id)
        if cached is not None:
            return cached
        snapshot = self._compute_snapshot(region_id)
        self._snapshot_cache[region_id] = snapshot
        return snapshot

    def _compute_snapshot(self, region_id: str) -> MarketSnapshot:
        """Build the market snapshot for a region.

        When a region has no local trading history, agents fall back to the
        national average price.  For remote regions this would be misleading:
        the national average reflects prices at well-connected hubs, but a
        buyer sending grain FROM a remote region pays extra transport — so
        the effective farm-gate price the buyer will offer is national_avg
        minus the haulage from that region to the demand centre.  We apply
        this transport discount to `national_avg_price` so that Nerlove
        expectations in remote regions converge toward realistic farm-gate
        values rather than the overly optimistic hub price.
        """
        region = self.regions.get(region_id)
        dist_to_centre = haversine_km(region.lat, region.lon,
                                      self.demand_center_lat, self.demand_center_lon)
        transport_discount = transport_cost_per_ton(dist_to_centre, self.logistics)

        crops_info = {}
        for good in self.crops.market_goods():
            stats = self._stats.get((region_id, good))
            national_hist = self._national_price_history.get(good, [])
            national_avg = national_hist[-1] if national_hist else None
            # Apply distance-based shadow price: a remote farmer should expect
            # to net national_avg minus the cost of getting their grain to market.
            shadow_avg = (
                max(national_avg - transport_discount, 0.0)
                if national_avg is not None else None
            )
            crops_info[good] = CropMarketInfo(
                crop_id=good,
                last_price=stats.last_price() if stats else None,
                price_history=list(stats.prices) if stats else [],
                national_avg_price=shadow_avg,
            )
        return MarketSnapshot(year=self.year, month=self.month,
                              month_index=self.month_index, crops=crops_info)

    def last_national_price(self, good: str) -> float | None:
        """Most recent volume-weighted national average price for a market good
        (the domestic sourcing-cost reference exporters flex their volume on).
        None until the good has cleared at least once."""
        hist = self._national_price_history.get(good)
        return hist[-1] if hist else None

    def last_price(self, region_id: str, crop_id: str) -> float | None:
        stats = self._stats.get((region_id, crop_id))
        return stats.last_price() if stats else None

    def last_volume(self, region_id: str, crop_id: str) -> float:
        stats = self._stats.get((region_id, crop_id))
        return stats.last_volume() if stats else 0.0

    def regional_stats(self) -> dict[tuple[str, str], RegionCropStats]:
        """Read-only view of every (region, crop) price/volume series — the
        raw material for per-region market charts in the UI."""
        return dict(self._stats)
