"""Farmer agent.

Each farmer is a point on the map with a fixed land endowment (hectares) and
a capacity-limited storage (tons). Every month it must:

1. **Decide what to plant** — in each crop's own sowing month (spring crops in
   ~May, winter crops in ~September; the climate zone only gates *which* crops
   are available, not the timing). The decision is driven by expected
   profitability per crop (expected price × expected yield − sowing cost),
   allocated over the land *currently free* (winter and spring crops compete
   for the same hectares because a winter crop sown in autumn is still standing
   when the spring crops go in), subject to a crop-rotation constraint: a crop
   cannot be re-sown on the share of land it occupied in the same window last
   year.
2. **Harvest** — whenever a sown crop reaches its harvest month (winter crops
   the following summer, spring crops the same autumn), converting planted area
   into stored tonnage (with a correlated weather shock), capped by the physical
   storage limit (anything that would overflow is dumped on the market
   immediately as a forced sale). A farm growing both winter and spring crops
   therefore reaps twice a year.
3. **Form a price expectation** — every month, before deciding what to plant
   or sell, a farmer updates an adaptive price expectation per crop using the
   Nerlove (partial-adjustment) scheme: this month's expectation is last
   month's expectation nudged toward the price actually observed in the
   market, `E_t = E_{t-1} + β·(P_observed − E_{t-1})`. This expectation drives
   *both* the planting decision (expected margin) and the asking price posted
   for sale (reservation price), so a farmer that has repeatedly seen high
   prices gradually raises its asking price, and vice versa.
4. **Decide how much to sell** — posting a `SupplyOffer` at the farmer's own
   location, priced from its price expectation (discounted when storage is
   uncomfortably full — a farmer running out of space is a more eager seller
   and will accept less to move grain before it spoils or must be dumped).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from app.simulation.agents.base import MarketSnapshot
from app.simulation.calendar import harvest_year
from app.simulation.crops import CropRegistry, CropType
from app.simulation.geo import ClimateZone
from app.simulation.market.search_market import SupplyOffer
from app.simulation.weather import WeatherModel


@dataclass
class PendingHarvest:
    crop_id: str
    area_ha: float
    harvest_year: int
    harvest_month: int


@dataclass
class Farmer:
    id: str
    name: str
    region_id: str
    lat: float
    lon: float
    climate_zone: ClimateZone
    total_area_ha: float
    storage_capacity_tons: float
    allowed_crop_ids: list[str]
    # Per-zone yield multiplier (south > black_earth > … > north): scales every
    # crop's base yield at this farm, so the same crop yields less in Siberia /
    # the Far East than in the south, shaping both harvests and planting margins.
    zone_yield_multiplier: float = 1.0
    # Per-zone cost multiplier: scales sowing + fixed costs (land rent, inputs,
    # labour). Lower in Siberia/north, so low-yield zones survive on cheaper land
    # rather than all going bankrupt — the south stays most profitable, not the
    # only viable zone.
    zone_cost_multiplier: float = 1.0

    cash: float = 0.0
    target_margin: float = 0.20          # set by scenario from agent_parameters.json:farmer_defaults.target_margin_range
    base_sell_fraction: float = 0.25      # set by scenario from agent_parameters.json:farmer_defaults.base_sell_fraction_range
    price_adaptation_speed: float = 0.70  # set by scenario from agent_parameters.json:farmer_defaults.price_adaptation_speed_range [0.5, 0.9]

    # Debt controls
    max_debt: float = 0.0                # max |cash| before planting is suspended (0 = disabled)
    credit_rate_per_month: float = 0.008 # monthly interest rate on negative cash (~10 %/year)

    # Fixed operating cost — land rent, machinery depreciation, admin/overhead —
    # charged every month as total_area_ha * fixed_cost_per_ha_per_year / 12,
    # independent of whether the farm sows or sells. This is the realistic
    # margin pressure that makes profitability (and therefore insolvency, exit
    # and entry) meaningful; sowing_cost_per_ha alone leaves margins permanently
    # fat. 0 disables it (reproduces the old "variable-cost-only" behaviour).
    fixed_cost_per_ha_per_year: float = 0.0

    # Algorithm constants — set by scenario from agent_parameters.json:farmer_behavior
    cash_ema_alpha: float = 0.12
    # Adaptation speed for the *trading* (reservation) price expectation, kept
    # independent of price_adaptation_speed (the planting expectation) so the
    # two timescales can be tuned separately: the planting belief stays slow
    # (good for inter-year stability) without forcing the monthly ask to lag —
    # a laggy ask actually *worsens* within-year oscillation, so this is kept
    # responsive. (The dominant within-year driver is surplus_bargaining_power,
    # tuned in the search market, not this.)
    sale_expectation_smoothing: float = 0.5
    acreage_inertia: float = 0.5              # Nerlovian partial adjustment on the sowing mix (0 = jump to margin-optimal each year)
    storage_fill_pressure_multiplier: float = 0.5
    storage_fill_threshold: float = 0.5
    max_storage_discount: float = 0.35
    discount_activation_threshold: float = 0.3
    discount_ramp_rate: float = 0.6
    low_fill_pressure_multiplier: float = 0.6

    # Lifecycle tracking
    insolvent_months: int = 0            # consecutive months with cash < -max_debt (for closure)
    cash_ema: float | None = None        # EMA of monthly cash change — profitability signal for entry decisions

    storage: dict[str, float] = field(default_factory=dict)              # market_good -> tons held (grain is fungible once harvested)
    # sowing_month -> {crop_id: ha} sown in that month's planting window last time
    # round. Keyed by the sowing month (e.g. 5 = spring planting, 9 = winter
    # planting), so each window's rotation memory is independent.
    planted_area: dict[int, dict[str, float]] = field(default_factory=dict)
    pending_harvests: list[PendingHarvest] = field(default_factory=list)
    expected_price: dict[str, float] = field(default_factory=dict)       # market_good -> RUB/ton — PLANTING belief (Nerlove E[P], drives sowing)
    reservation_price: dict[str, float] = field(default_factory=dict)    # market_good -> RUB/ton — TRADING belief (smoothed, prices monthly sale offers)

    rng: random.Random = field(default_factory=random.Random, repr=False, compare=False)

    # Non-init scratch field — set by start_month_snapshot each step
    _cash_at_month_start: float = field(default=0.0, init=False, repr=False, compare=False)

    # ------------------------------------------------------------------ price expectations (Nerlove)
    def _production_cost_per_ton(self, crop_id: str, crops: CropRegistry) -> float:
        crop = crops.get(crop_id)
        return crop.sowing_cost_per_ha / max(crop.base_yield_t_per_ha, 1e-6)

    def _market_goods(self, crops: CropRegistry) -> list[str]:
        """The distinct market goods this farm can sell (one per group of its
        allowed varieties — winter_wheat & spring_wheat collapse to "wheat")."""
        seen: dict[str, None] = {}
        for cid in self.allowed_crop_ids:
            seen.setdefault(crops.get(cid).market_good, None)
        return list(seen.keys())

    def update_price_expectations(self, snapshot: MarketSnapshot, crops: CropRegistry) -> None:
        """Maintain two *separate* adaptive price expectations per crop, both of
        the Nerlove partial-adjustment form `E_t = E_{t-1} + α·(P_obs − E_{t-1})`
        but with different speeds and different jobs:

        * **Planting belief** (`expected_price`, α = `price_adaptation_speed`):
          the medium-term price the farmer expects when deciding what to sow
          once a year. Drives `_decide_planting`.
        * **Trading belief** (`reservation_price`, α = `sale_expectation_smoothing`):
          the reservation level used to price monthly sale offers. Drives
          `decide_sales`.

        Keeping them apart lets each timescale be tuned on its own — the
        planting belief slow for inter-year stability, the trading belief
        responsive so the monthly ask does not lag the market — while leaving
        the annual supply response intact. `P_obs` is the most recent
        clearing price the farmer can see (its home region's, falling back to
        the national average); with no market history yet both are seeded at
        production cost plus the farmer's target margin.

        Expectations are kept per **market good** (not per variety): winter and
        spring wheat sell into the same "wheat" market at one price, so a farmer
        forms one wheat-price belief that both varieties' planting margins read.
        """
        for good in self._market_goods(crops):
            info = snapshot.info(good)
            observed = info.last_price or info.national_avg_price
            seed = crops.production_cost_per_ton(good) * (1.0 + self.target_margin)

            plant_prior = self.expected_price.get(good, seed)
            sale_prior = self.reservation_price.get(good, seed)
            if observed is not None:
                self.expected_price[good] = plant_prior + self.price_adaptation_speed * (observed - plant_prior)
                self.reservation_price[good] = sale_prior + self.sale_expectation_smoothing * (observed - sale_prior)
            else:
                self.expected_price[good] = plant_prior
                self.reservation_price[good] = sale_prior

    # ------------------------------------------------------------------ planting
    def maybe_plant(self, year: int, month: int, snapshot: MarketSnapshot, crops: CropRegistry) -> int | None:
        """Sow whatever crops have their sowing window *this* month (and suit the
        farm's climate zone). Returns the sowing month as a slot key (so the
        engine can pay per-hectare subsidies on the area just planted), or None
        if nothing is sown this month. With a crop-driven calendar this fires in
        spring (spring crops) and again in autumn (winter crops)."""
        candidates = [
            crop for cid in self.allowed_crop_ids
            if (crop := crops.get(cid)).sowing_month == month
            and self.climate_zone.value in crop.suitable_zones
        ]
        if not candidates:
            return None
        # A deeply indebted farmer cannot afford seeds: suspend planting until
        # cash recovers.  Existing pending harvests and selling still proceed.
        if self.max_debt > 0 and self.cash < -self.max_debt:
            return None
        self._decide_planting(candidates, year, month, snapshot, crops)
        return month

    def apply_credit_charges(self) -> None:
        """Charge monthly interest on negative cash (implicit operating credit).
        With the default rate of 0.8 %/month this compounds to ~10 %/year,
        making persistent debt increasingly costly and nudging insolvent agents
        toward suspension sooner than a flat threshold alone would."""
        if self.cash < 0:
            self.cash *= (1.0 + self.credit_rate_per_month)

    def apply_fixed_costs(self) -> None:
        """Charge one month's share of the annual fixed operating cost (land
        rent, depreciation, overhead) on the whole farmed area, regardless of
        sowing or sales activity. This is what turns the lumpy sow-cost /
        harvest-revenue cycle into a realistic net margin and lets unprofitable
        farms actually drift into insolvency."""
        if self.fixed_cost_per_ha_per_year > 0:
            self.cash -= self.total_area_ha * self.fixed_cost_per_ha_per_year / 12.0 * self.zone_cost_multiplier

    # ------------------------------------------------------------------ lifecycle
    def start_month_snapshot(self) -> None:
        """Record beginning-of-month cash so end_month_update can compute the delta."""
        self._cash_at_month_start = self.cash

    def end_month_update(self) -> None:
        """Update the EMA profitability signal and insolvency counter.

        Called once per month at the end of the step, after all settlement.
        The EMA smooths the lumpy cash pattern of Russian grain agriculture:
        large negative sowing cost spikes in May offset by large positive
        post-harvest sales in Sept–Nov. Alpha sourced from
        agent_parameters.json:farmer_behavior.cash_ema_alpha.
        """
        delta = self.cash - self._cash_at_month_start
        if self.cash_ema is None:
            self.cash_ema = delta
        else:
            self.cash_ema = (1.0 - self.cash_ema_alpha) * self.cash_ema + self.cash_ema_alpha * delta

        if self.max_debt > 0 and self.cash < -self.max_debt:
            self.insolvent_months += 1
        else:
            self.insolvent_months = 0

    def _decide_planting(self, candidates: list[CropType], year: int, month: int,
                         snapshot: MarketSnapshot, crops: CropRegistry) -> None:
        previous = self.planted_area.get(month, {})

        # Land actually free to sow *now*: total area minus whatever is still
        # standing in the ground (winter crops sown last autumn are not yet
        # reaped when spring crops go in, and vice versa). This is what makes
        # winter and spring plantings compete for the same finite land.
        committed = sum(h.area_ha for h in self.pending_harvests)
        available = max(self.total_area_ha - committed, 0.0)
        if available <= 1e-6:
            self.planted_area[month] = {c.id: 0.0 for c in candidates}
            return

        # Expected margin per hectare = expected_price * yield - sowing_cost,
        # using the farmer's own Nerlove-adapted price expectation (updated
        # once per month in `update_price_expectations`, so it already blends
        # the latest observed price with the farmer's prior belief).
        margins: dict[str, float] = {}
        for crop in candidates:
            # The price the variety will fetch is its *market good's* price (one
            # wheat price for both winter and spring wheat); the differing yields
            # and costs are what make one variety more profitable to sow.
            expected_price = self.expected_price.get(crop.market_good) or (
                crop.sowing_cost_per_ha / max(crop.base_yield_t_per_ha, 1e-6) * (1.0 + self.target_margin)
            )
            # Zone-adjusted expected yield: a crop that yields less in this zone
            # earns a lower margin here, so e.g. sunflower is far less attractive
            # in Siberia than in the south even at the same market price.
            expected_revenue_per_ha = expected_price * crop.base_yield_t_per_ha * self.zone_yield_multiplier
            margins[crop.id] = expected_revenue_per_ha - crop.sowing_cost_per_ha * self.zone_cost_multiplier

        # Agronomic rotation limit: no crop may occupy more than its share of the
        # farm (`max_area_share` — sunflower ~15 %, cereals much higher). This is
        # what stops a high-margin oilseed from claiming the whole farm and lets
        # cereals dominate, the way real rotations work.
        cap = {c.id: c.max_area_share * self.total_area_ha for c in candidates}

        # Margin-driven *target* mix by greedy water-filling: take the most
        # profitable crops first, each up to its agronomic cap, until the free
        # land runs out. High-margin oilseeds fill their small caps first; the
        # residual land falls to the high-cap cereals — which is exactly why
        # cereals end up dominating the mix even though their per-hectare margin
        # is lower. (Proportional splitting would instead spread land evenly and
        # under-weight cereals.)
        target: dict[str, float] = {}
        remaining = available
        ranked = sorted(candidates, key=lambda c: margins[c.id], reverse=True)
        for c in ranked:
            if margins[c.id] <= 0 or remaining <= 1e-6:
                continue
            a = min(cap[c.id], remaining)
            if a > 1e-6:
                target[c.id] = a
                remaining -= a
        if not target:
            # Nothing looks profitable: spread the free land evenly within caps.
            even = available / len(candidates)
            for c in candidates:
                target[c.id] = min(even, cap[c.id])

        # Acreage inertia (Nerlovian partial adjustment on ACREAGE, not just price):
        # a farm does not jump to the margin-optimal mix each year — sunk
        # equipment, agronomic familiarity, contracts and rotation make last
        # year's mix sticky, so new = inertia*last_year + (1-inertia)*target.
        # The first time this window is sown there is no anchor, so use the
        # target directly (avoids systematically under-sowing in year one); the
        # blend has fixed point = target, so the mix still converges to it.
        prev_total = sum(previous.values())
        lam = self.acreage_inertia if prev_total > 1e-6 else 0.0
        allocation: dict[str, float] = {}
        for c in candidates:
            blended = lam * previous.get(c.id, 0.0) + (1.0 - lam) * target.get(c.id, 0.0)
            blended = min(blended, cap[c.id])
            if blended > 1e-6:
                allocation[c.id] = blended

        # Never sow more than the free land: scale the whole mix down if needed.
        total_alloc = sum(allocation.values())
        if total_alloc > available and total_alloc > 0:
            scale = available / total_alloc
            allocation = {cid: a * scale for cid, a in allocation.items()}

        # Record the full window allocation (zeros included) so next year's
        # inertia anchor for this window is complete.
        self.planted_area[month] = {c.id: allocation.get(c.id, 0.0) for c in candidates}
        for crop_id, area in allocation.items():
            if area <= 1e-6:
                continue
            crop = crops.get(crop_id)
            self.cash -= crop.sowing_cost_per_ha * area * self.zone_cost_multiplier
            self.pending_harvests.append(
                PendingHarvest(crop_id=crop_id, area_ha=area,
                               harvest_year=harvest_year(crop, year), harvest_month=crop.harvest_month)
            )

    # ------------------------------------------------------------------ harvest
    def maybe_harvest(self, year: int, month: int, crops: CropRegistry,
                      weather: WeatherModel | None = None) -> dict[str, float]:
        """Realise any pending harvests due this month. Returns tons forced onto
        the market immediately because storage capacity was exceeded, keyed by crop.

        The per-crop weather shock is a multiplicative draw centred on 1 with
        standard deviation `yield_volatility`. When a `weather` model is given,
        the shock is built from shared national + regional factors plus the
        farm's own idiosyncratic draw (see `app.simulation.weather`), so farms
        in the same region/country move together — the realistic source of
        aggregate supply shocks. With no model it falls back to a fully
        independent draw (used by isolated unit tests)."""
        due = [h for h in self.pending_harvests if h.harvest_year == year and h.harvest_month == month]
        if not due:
            return {}
        self.pending_harvests = [h for h in self.pending_harvests if h not in due]

        forced_sales: dict[str, float] = {}
        for h in due:
            crop = crops.get(h.crop_id)
            if weather is not None:
                z = (weather.common_factor(self.region_id, h.crop_id, year, month)
                     + math.sqrt(weather.idiosyncratic_weight) * self.rng.gauss(0.0, 1.0))
                # Stochastic shock, then any deliberate national/regional override
                # staged through the manipulation API (1.0 when none is set).
                weather_shock = max(1.0 + crop.yield_volatility * z, 0.0) * weather.manual_factor(self.region_id)
            else:
                weather_shock = max(self.rng.gauss(1.0, crop.yield_volatility), 0.0)
            harvested = h.area_ha * crop.base_yield_t_per_ha * weather_shock * self.zone_yield_multiplier
            # Harvested grain enters storage as its fungible market good (so
            # winter and spring wheat pool into one "wheat" stock to sell).
            self.storage[crop.market_good] = self.storage.get(crop.market_good, 0.0) + harvested

        total_stock = sum(self.storage.values())
        if total_stock > self.storage_capacity_tons:
            overflow = total_stock - self.storage_capacity_tons
            # Dump the overflow proportionally across held crops — the farmer
            # cannot afford to let grain rot in the open, so it is sold at any price.
            # Each crop's share of the original total is what determines its
            # share of the overflow — both ratios must be computed against the
            # *same* fixed total so the shares sum to exactly `overflow`.
            for crop_id, qty in list(self.storage.items()):
                take = min(qty, overflow * (qty / total_stock))
                self.storage[crop_id] = qty - take
                forced_sales[crop_id] = forced_sales.get(crop_id, 0.0) + take
        return forced_sales

    # ------------------------------------------------------------------ spoilage
    def apply_storage_losses(self, crops: CropRegistry) -> None:
        # storage is keyed by market good, so the loss rate comes from the good.
        for good, qty in list(self.storage.items()):
            self.storage[good] = qty * (1.0 - crops.storage_loss_rate(good))

    # ------------------------------------------------------------------ selling
    def decide_sales(self, snapshot: MarketSnapshot, crops: CropRegistry,
                     forced_quantities: dict[str, float] | None = None) -> list[SupplyOffer]:
        """Post standing offers at the farmer's own location (point-to-point —
        the buyer works out delivery cost itself, see `search_market`).

        The asking price is the farmer's Nerlove price expectation, discounted
        when storage is uncomfortably full (a farmer sitting on a near-full
        warehouse needs to move grain to make room for the next harvest and
        will accept less rather than risk an even-more-forced dump later) —
        but never below production cost. Forced (overflow) tonnage is folded
        into the same offer at the same discounted price: it is not priced
        differently, it simply adds to the quantity the farmer is willing to
        let go this month.
        """
        offers: list[SupplyOffer] = []
        forced_quantities = forced_quantities or {}
        fill_ratio = sum(self.storage.values()) / max(self.storage_capacity_tons, 1e-6)
        pressure_fraction = self.base_sell_fraction + self.storage_fill_pressure_multiplier * max(fill_ratio - self.storage_fill_threshold, 0.0)
        storage_discount = min(self.max_storage_discount, max(fill_ratio - self.discount_activation_threshold, 0.0) * self.discount_ramp_rate)

        # storage and forced quantities are keyed by market good — one offer per good.
        for good in set(self.storage) | set(forced_quantities):
            held = self.storage.get(good, 0.0)
            forced = forced_quantities.get(good, 0.0)
            if held <= 1e-9 and forced <= 1e-9:
                continue

            production_cost_per_ton = crops.production_cost_per_ton(good)
            # Ask from the smoothed *trading* reservation price, not the planting
            # expectation, so the monthly offer price does not chase last month.
            reservation = self.reservation_price.get(good) or production_cost_per_ton * (1.0 + self.target_margin)
            ask_price = max(production_cost_per_ton, reservation * (1.0 - storage_discount))

            voluntary_fraction = pressure_fraction if fill_ratio > self.storage_fill_threshold else pressure_fraction * self.low_fill_pressure_multiplier
            voluntary_qty = held * min(1.0, voluntary_fraction)
            total_qty = voluntary_qty + forced
            if total_qty <= 1e-6:
                continue

            offers.append(SupplyOffer(
                seller_id=self.id, crop_id=good, quantity=total_qty,
                ask_price=ask_price, lat=self.lat, lon=self.lon,
            ))
            self.storage[good] = max(held - voluntary_qty, 0.0)
        return offers

    # ------------------------------------------------------------------ settlement callbacks
    def receive_payment(self, amount: float) -> None:
        self.cash += amount

    def return_unsold(self, crop_id: str, quantity: float) -> float:
        """Put unsold offered grain back into storage, capped by free space.
        Returns the tonnage actually re-stored; any shortfall (quantity minus
        the return value) is grain that no longer fits and is lost — see
        `SimulationEngine._return_unsold_offers`."""
        if quantity <= 0:
            return 0.0
        current = self.storage.get(crop_id, 0.0)
        total_other = sum(v for k, v in self.storage.items() if k != crop_id)
        space = max(self.storage_capacity_tons - total_other - current, 0.0)
        stored = min(quantity, space)
        self.storage[crop_id] = current + stored
        return stored
