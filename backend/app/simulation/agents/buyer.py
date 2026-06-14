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

    # Demand contraction/expansion: track realized costs for endogenous demand adjustment
    actual_cost_ema: dict[str, float] = field(default_factory=dict)  # crop_id → EMA of price paid
    months_above_ceiling: dict[str, int] = field(default_factory=dict)  # consecutive months of overpayment
    months_below_target: dict[str, int] = field(default_factory=dict)   # consecutive months of underpayment
    demand_contraction_enabled: bool = True
    # Contraction parameters
    demand_contraction_threshold_pct: float = 20.0  # trigger when price > target by this %
    demand_contraction_months: int = 2              # consecutive months before contraction
    demand_contraction_rate: float = 0.15           # reduce consumption by this fraction
    # Expansion parameters — set by scenario from agent_parameters.json:buyer_demand_contraction
    demand_expansion_undershoot_pct: float = 5.0    # price must be this % below target to count
    demand_expansion_months: int = 3                # consecutive months before expansion triggers
    demand_expansion_recovery_rate: float = 0.10    # fraction of gap-to-baseline recovered per trigger

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
        from its downstream product price net of margin. Falls back to a
        multiple of the observed market price when no output price is
        configured, so traders (who have no "product" of their own) still
        have a sensible reference to anchor their search ceiling on."""
        out_price = self._current_output_price(crop_id, snapshot.month_index)
        if out_price is not None:
            return out_price * (1.0 - self.processing_margin)
        info = snapshot.info(crop_id)
        reference = info.last_price or info.national_avg_price
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

    def check_demand_contraction(self, snapshot: MarketSnapshot) -> None:
        """Demand contraction and expansion: endogenous adjustment of monthly
        consumption in response to sustained price pressure in either direction.

        Contraction: when the price EMA stays above target by more than
        `demand_contraction_threshold_pct` for `demand_contraction_months`
        consecutive months, consumption is reduced by `demand_contraction_rate`.

        Expansion: when the price EMA falls below target by more than
        `demand_expansion_undershoot_pct` for `demand_expansion_months`
        consecutive months, consumption recovers by `demand_expansion_recovery_rate`
        of the remaining gap to the original baseline — so recovery is always
        partial and can never overshoot the pre-contraction level.

        The two counters are mutually exclusive: a month is either above ceiling,
        below target, or neutral — at most one counter increments.
        """
        if not self.demand_contraction_enabled:
            return

        for crop_id, monthly_need in list(self.monthly_consumption.items()):
            if monthly_need <= 0:
                continue

            target_price = self._target_price(crop_id, snapshot)
            if target_price <= 0:
                continue

            # Update EMA of actual price paid
            info = snapshot.info(crop_id)
            actual_price = info.last_price or info.national_avg_price
            if actual_price is None:
                continue

            if crop_id not in self.actual_cost_ema:
                self.actual_cost_ema[crop_id] = actual_price
            else:
                self.actual_cost_ema[crop_id] = (
                    (1.0 - self.actual_cost_ema_alpha) * self.actual_cost_ema[crop_id] +
                    self.actual_cost_ema_alpha * actual_price
                )

            price_ema = self.actual_cost_ema[crop_id]
            deviation_pct = (price_ema / target_price - 1.0) * 100

            # --- contraction path ---
            if deviation_pct > self.demand_contraction_threshold_pct:
                self.months_above_ceiling[crop_id] = self.months_above_ceiling.get(crop_id, 0) + 1
                self.months_below_target[crop_id] = 0
                if self.months_above_ceiling[crop_id] >= self.demand_contraction_months:
                    self.monthly_consumption[crop_id] *= (1.0 - self.demand_contraction_rate)
                    self.months_above_ceiling[crop_id] = 0

            # --- expansion path ---
            elif deviation_pct < -self.demand_expansion_undershoot_pct:
                self.months_below_target[crop_id] = self.months_below_target.get(crop_id, 0) + 1
                self.months_above_ceiling[crop_id] = 0
                if self.months_below_target[crop_id] >= self.demand_expansion_months:
                    baseline = self.monthly_consumption_baseline.get(crop_id, monthly_need)
                    gap = baseline - self.monthly_consumption[crop_id]
                    if gap > 1e-6:
                        self.monthly_consumption[crop_id] += gap * self.demand_expansion_recovery_rate
                    self.months_below_target[crop_id] = 0

            # --- neutral: reset both counters ---
            else:
                self.months_above_ceiling[crop_id] = 0
                self.months_below_target[crop_id] = 0

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
            # draw down rather than ending every month at zero stock.
            desired_stock = monthly_need * self.target_inventory_months
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
