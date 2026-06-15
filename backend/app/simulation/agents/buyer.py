"""Domestic buyer agent.

The brief leaves buyer parameters/logic open ("предложи сам"), so here is the
proposed model: buyers represent the demand side inside Russia — flour mills,
feed plants, food processors and wholesale traders/elevators — seeded at
realistic densities (proportional to regional population/processing capacity)
but fully editable and addable through the UI.

Each buyer:

* has a **fixed monthly demand** per crop (`monthly_consumption`), set once
  at agent creation — this is its processing throughput and does not flex
  with price or stock the way the old "target stock buffer" did;
* has a *target price* derived from the price of the downstream product it
  sells (e.g. flour, feed, processed food) minus its processing margin —
  this is the price it would *like* to pay for the raw crop;
* has a **flexibility coefficient** φ ∈ [0, 1] that turns the target price
  into a hard search ceiling: `ceiling = target_price / (1 − φ)`. φ = 1 means
  "no ceiling — secure the volume this month whatever it costs"; φ = 0 means
  "never pay a kopek over the target price, even if that means going without
  this month and trying again next month." This is the dial between
  must-have-it-now processors and patient, price-disciplined traders;
* actively **searches the whole country for the cheapest delivered raw
  material** (ask price plus transport cost — see `search_market`) rather
  than posting a bid into a regional order book and waiting to be matched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.simulation.agents.base import MarketSnapshot
from app.simulation.market.search_market import DemandRequest


class BuyerType(str, Enum):
    FLOUR_MILL = "flour_mill"
    FEED_PRODUCER = "feed_producer"
    FOOD_PROCESSOR = "food_processor"
    TRADER = "trader"


@dataclass
class Buyer:
    id: str
    name: str
    region_id: str
    lat: float
    lon: float
    buyer_type: BuyerType

    # tons of each crop consumed (processed/resold) per month — can adapt downward
    # if the buyer consistently pays too much (demand contraction mechanism)
    monthly_consumption: dict[str, float]
    storage_capacity_tons: float

    # downstream product price (e.g. RUB/ton of flour) the buyer's margin is computed from.
    # Can be either a scalar float (static price) or overridden per crop by output_price_series
    # (a 12-month cycle, same cycling semantics as world_prices) for time-varying prices.
    output_price: dict[str, float] = field(default_factory=dict)
    output_price_series: dict[str, list[float]] = field(default_factory=dict)  # crop → monthly series
    processing_margin: float = 0.30   # share of output price retained as margin/costs (caps input price)

    # urgency/flexibility coefficient φ ∈ [0, 1]:
    #   φ = 1  → buy the needed volume this month at any price
    #   φ = 0  → never exceed the target price; wait indefinitely otherwise
    flexibility: float = 0.5

    # Maximum debt before the buyer suspends purchases (0 = disabled).
    max_debt: float = 0.0

    cash: float = 0.0
    storage: dict[str, float] = field(default_factory=dict)

    # Lifecycle tracking (symmetric to Farmer): a buyer that stays insolvent for
    # `insolvent_months` consecutive months is closed by the engine's yearly
    # lifecycle phase, and the cash-EMA profitability signal drives where new
    # processors/traders enter.
    cash_ema_alpha: float = 0.12
    insolvent_months: int = 0
    cash_ema: float | None = None

    # --- Price-elastic demand (constant-elasticity, two-sided) ---
    # Each month the buyer rescales its processing throughput from baseline by a
    # constant-elasticity response to the (EMA-smoothed) price it pays relative
    # to its target price — see `update_demand`. Replaces the old step-function
    # contraction/expansion. Parameters set by scenario from
    # agent_parameters.json:buyer_demand_elasticity (+ per-type elasticity from
    # buyer_profiles.demand_elasticity).
    actual_cost_ema: dict[str, float] = field(default_factory=dict)  # crop_id → EMA of price actually paid (the elasticity's price signal)
    price_elastic_demand: bool = True
    demand_elasticity: float = 0.3        # ε in consumption = baseline*(price/target)^(-ε); per-buyer-type
    min_demand_factor: float = 0.3        # consumption floor as a fraction of baseline
    max_demand_factor: float = 1.5        # consumption cap as a fraction of baseline (processing capacity limit)

    # --- Strategic / mean-reversion inventory ---
    # The buyer carries a smoothed price "anchor" it expects price to revert
    # toward; when the current price sits below the anchor it builds extra cover
    # (buy the dip), above it draws cover down — intertemporal arbitrage that
    # smooths prices. See `update_price_expectations` / `request_purchases`.
    expected_price: dict[str, float] = field(default_factory=dict)  # crop_id → smoothed mean-reversion price anchor
    strategic_inventory_enabled: bool = True
    price_anchor_adaptation_speed: float = 0.25   # Nerlove α for the price anchor
    speculation_sensitivity: float = 1.5          # strength of the cover response to (anchor/current − 1)
    cover_multiplier_floor: float = 0.5
    cover_multiplier_ceil: float = 2.0

    # Algorithm constants — set by scenario from agent_parameters.json:buyer_behavior
    fallback_target_markup: float = 1.15
    trader_resale_markup: float = 1.05
    actual_cost_ema_alpha: float = 0.3
    # Desired raw-material cover, in months of consumption. The buyer purchases
    # toward `monthly_consumption * target_inventory_months` (capped by storage
    # capacity) but consumes only one month's worth, so it carries a buffer
    # instead of running at zero stock. 1.0 reproduces the old just-in-time
    # behaviour (buy exactly this month's throughput, end at zero).
    target_inventory_months: float = 2.0

    # Non-init: baseline consumption captured at construction, recovery never exceeds this
    monthly_consumption_baseline: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    # Non-init scratch — set by start_month_snapshot each step
    _cash_at_month_start: float = field(default=0.0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.monthly_consumption_baseline = dict(self.monthly_consumption)

    # ------------------------------------------------------------------ lifecycle
    def start_month_snapshot(self) -> None:
        """Record beginning-of-month cash so end_month_update can compute the delta."""
        self._cash_at_month_start = self.cash

    def end_month_update(self, insolvency_debt_threshold: float) -> None:
        """Update the EMA profitability signal and the insolvency counter, once
        per month after settlement. Mirrors `Farmer.end_month_update`: the EMA
        smooths the buyer's monthly cash change (lumpy purchases vs. steadier
        processing revenue), and the counter tracks how long the buyer has sat
        below the debt threshold so the engine can close chronically loss-making
        processors/traders."""
        delta = self.cash - self._cash_at_month_start
        if self.cash_ema is None:
            self.cash_ema = delta
        else:
            self.cash_ema = (1.0 - self.cash_ema_alpha) * self.cash_ema + self.cash_ema_alpha * delta

        if insolvency_debt_threshold > 0 and self.cash < -insolvency_debt_threshold:
            self.insolvent_months += 1
        else:
            self.insolvent_months = 0

    def _current_output_price(self, crop_id: str, month_index: int) -> float | None:
        """Resolve the downstream product price for this crop at the current month.
        Prefers output_price_series (cycling time series) over the static scalar."""
        series = self.output_price_series.get(crop_id)
        if series:
            return series[month_index % len(series)]
        return self.output_price.get(crop_id)

    def _target_price(self, crop_id: str, snapshot: MarketSnapshot) -> float:
        """The price the buyer would *like* to pay for the raw crop, derived
        from its downstream product price net of margin.

        A trader has no "product" of its own, so it falls back to a multiple of
        a *reference* price. That reference is the buyer's **smoothed price
        anchor** (`expected_price`, the Nerlove mean-reversion belief) rather
        than the instantaneous market price: anchoring on the spot price would
        make the elasticity ratio `price/target` ≈ constant (target tracks
        price 1:1), so the demand response would be inert — exactly the bug that
        made traders, the most price-elastic buyers, never actually flex. With a
        slow anchor, a price spike *above* the anchor raises `price/target` and
        contracts the trader's demand; a dip expands it. Falls back to the spot
        price only until the anchor is seeded (or when strategic inventory, which
        maintains the anchor, is disabled)."""
        out_price = self._current_output_price(crop_id, snapshot.month_index)
        if out_price is not None:
            return out_price * (1.0 - self.processing_margin)
        info = snapshot.info(crop_id)
        reference = self.expected_price.get(crop_id) or info.last_price or info.national_avg_price
        return (reference * self.fallback_target_markup) if reference else 0.0

    def _price_ceiling(self, target_price: float) -> float | None:
        """Turn the target price into a search ceiling using the flexibility
        coefficient: `ceiling = target_price / (1 − φ)`. φ → 1 makes the
        ceiling blow up to "no limit" (the buyer secures its volume whatever
        it costs); φ = 0 collapses the ceiling onto the target price itself
        (the buyer refuses to pay a kopek more and would rather go without)."""
        if self.flexibility >= 0.999:
            return None
        return target_price / max(1.0 - self.flexibility, 1e-6)

    def update_price_expectations(self, snapshot: MarketSnapshot) -> None:
        """Maintain a smoothed price *anchor* per crop — the level the buyer
        expects the market to revert toward — using the same Nerlove adaptive
        scheme the farmers use: `A_t = A_{t-1} + α·(P_obs − A_{t-1})`.

        Because the anchor lags the market, comparing it to the current price
        gives a mean-reversion signal: a price spiking above the anchor is
        "expensive, likely to fall" (destock), one below is "cheap, likely to
        recover" (build cover). `request_purchases` turns that gap into a
        strategic inventory adjustment. Seeded from the first observed price."""
        if not self.strategic_inventory_enabled:
            return
        for crop_id in self.monthly_consumption_baseline:
            info = snapshot.info(crop_id)
            observed = info.last_price if info.last_price is not None else info.national_avg_price
            if observed is None or observed <= 0.0:
                continue
            prior = self.expected_price.get(crop_id, observed)
            self.expected_price[crop_id] = prior + self.price_anchor_adaptation_speed * (observed - prior)

    def _cover_multiplier(self, crop_id: str, snapshot: MarketSnapshot) -> float:
        """Mean-reversion inventory multiplier on the buyer's target cover: >1
        when the current price is below the smoothed anchor (buy the dip), <1
        when above it (avoid stocking up on expensive grain). Returns 1.0 when
        strategic inventory is disabled or there is no usable price signal yet."""
        if not self.strategic_inventory_enabled:
            return 1.0
        anchor = self.expected_price.get(crop_id)
        info = snapshot.info(crop_id)
        current = info.last_price if info.last_price is not None else info.national_avg_price
        if not anchor or not current or current <= 0:
            return 1.0
        raw = 1.0 + self.speculation_sensitivity * (anchor / current - 1.0)
        return max(self.cover_multiplier_floor, min(self.cover_multiplier_ceil, raw))

    def update_demand(self, snapshot: MarketSnapshot) -> None:
        """Price-elastic demand: rescale this month's processing throughput from
        baseline by a constant-elasticity response to the price the buyer pays
        relative to its target price:

            consumption = baseline × (price_ema / target)^(−elasticity)

        clamped to [`min_demand_factor`, `max_demand_factor`]×baseline. This is
        the continuous, two-sided successor to the old step-function contraction:
        sustained overpayment shrinks throughput, cheap input expands it (up to
        capacity), with no counters or ratchets. `price_ema` is an EMA of the
        price actually observed, so the response is smooth rather than jerky."""
        if not self.price_elastic_demand:
            return
        for crop_id, baseline in self.monthly_consumption_baseline.items():
            if baseline <= 0:
                continue
            target_price = self._target_price(crop_id, snapshot)
            if target_price <= 0:
                continue
            info = snapshot.info(crop_id)
            observed = info.last_price if info.last_price is not None else info.national_avg_price
            # A remote region's shadow price can floor at 0 (national avg minus
            # haulage); a non-positive price carries no usable demand signal and
            # would blow up the negative-exponent power, so skip it.
            if observed is None or observed <= 0.0:
                continue

            prior = self.actual_cost_ema.get(crop_id, observed)
            self.actual_cost_ema[crop_id] = (1.0 - self.actual_cost_ema_alpha) * prior + self.actual_cost_ema_alpha * observed
            price_ema = self.actual_cost_ema[crop_id]

            factor = (price_ema / target_price) ** (-self.demand_elasticity)
            factor = max(self.min_demand_factor, min(self.max_demand_factor, factor))
            self.monthly_consumption[crop_id] = baseline * factor

    def request_purchases(self, snapshot: MarketSnapshot) -> list[DemandRequest]:
        """Go shopping: emit one `DemandRequest` per crop for this month's
        fixed demand, net of whatever is already sitting in storage (carried
        over from a month where the search did not fully succeed). The search
        engine (see `search_market.match_supply_and_demand`) ranks every
        seller in the country by delivered cost (ask price + transport) and
        fills from the cheapest first, up to the buyer's price ceiling.

        A buyer that has accumulated debt beyond `max_debt` is suspended for
        the month — it stops buying until its cash recovers, preventing
        unlimited balance-sheet deterioration."""
        if self.max_debt > 0 and self.cash < -self.max_debt:
            return []
        requests: list[DemandRequest] = []
        for crop_id, monthly_need in self.monthly_consumption.items():
            if monthly_need <= 0:
                continue
            current_stock = self.storage.get(crop_id, 0.0)
            # Buy toward a desired inventory cover (months of consumption), not
            # just this month's throughput, so the buyer carries a buffer it can
            # draw down rather than ending every month at zero stock. The cover
            # flexes with the mean-reversion signal: build extra when grain is
            # cheap relative to the price anchor, draw down when it is dear.
            effective_cover = self.target_inventory_months * self._cover_multiplier(crop_id, snapshot)
            desired_stock = monthly_need * effective_cover
            quantity = max(desired_stock - current_stock, 0.0)
            room_left = max(self.storage_capacity_tons - current_stock, 0.0)
            quantity = min(quantity, room_left)
            if quantity <= 1e-6:
                continue

            target_price = self._target_price(crop_id, snapshot)
            if target_price <= 0:
                continue

            # Only an output-derived target is a *fundamental* valuation the
            # search market can bargain toward. When the target was itself
            # derived from the going market price (a trader with no downstream
            # product — see `_target_price`), pass `valuation=None` so the
            # trade prices at the seller's ask: bargaining a buyer up toward a
            # market-referenced value would be circular and compound without
            # bound. The price ceiling still caps which offers are eligible.
            output_anchored = self._current_output_price(crop_id, snapshot.month_index) is not None
            requests.append(DemandRequest(
                buyer_id=self.id, crop_id=crop_id, quantity=quantity,
                price_ceiling=self._price_ceiling(target_price), lat=self.lat, lon=self.lon,
                valuation=target_price if output_anchored else None,
            ))
        return requests

    def consume(self, snapshot: MarketSnapshot) -> None:
        """Monthly throughput: draw down stock and book revenue from selling
        whatever the buyer turns its raw input into.

        Mills/feed plants/food processors sell a configured downstream product
        at `output_price` (or its time-series override), net of their processing
        margin (which also covers costs not modelled explicitly — staff, energy,
        packaging...). Traders have no product of their own: they resell the
        raw commodity at a small markup over the going regional price. Either
        way this closes the loop so a buyer's cash reflects a coherent
        (input cost) → (output revenue) cycle instead of only ever shrinking.
        """
        for crop_id, monthly_need in self.monthly_consumption.items():
            available = self.storage.get(crop_id, 0.0)
            consumed = min(available, monthly_need)
            self.storage[crop_id] = available - consumed
            if consumed <= 0:
                continue

            out_price = self._current_output_price(crop_id, snapshot.month_index)
            if out_price:
                self.cash += consumed * out_price * (1.0 - self.processing_margin)
            else:
                info = snapshot.info(crop_id)
                reference = info.last_price or info.national_avg_price
                if reference:
                    self.cash += consumed * reference * self.trader_resale_markup

    # ------------------------------------------------------------------ settlement callbacks
    def receive_goods(self, crop_id: str, quantity: float) -> None:
        self.storage[crop_id] = self.storage.get(crop_id, 0.0) + quantity

    def pay(self, amount: float) -> None:
        self.cash -= amount
