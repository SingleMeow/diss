"""Exporter agent.

Exporters are modelled as fixed points at Russia's border crossings / sea
ports — wherever the relevant trade actually flows out (e.g. a grain terminal
at Novorossiysk for Mediterranean/African destinations, Vladivostok for
Pacific destinations, dry-border crossings for direct neighbours such as
Kazakhstan or China). Each one has a **fixed monthly contract volume** per
crop (`monthly_capacity_tons`, set at creation — its shipping schedule does
not change month to month), searches the *entire country* for the cheapest
delivered grain (ask price plus transport — see `search_market`) to fill that
contract, pays to ship the grain to its own location, and resells it abroad
at the world (FOB) price net of the government's export duty.

World prices are exogenous monthly inputs (a configurable time series per
crop/destination) — they are *the* channel through which global market
conditions reach the domestic market in this model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.simulation.market.search_market import DemandRequest


@dataclass
class Exporter:
    id: str
    name: str
    region_id: str           # the exporter's home hub (port/border crossing)
    lat: float
    lon: float
    destination_country: str
    handled_crop_ids: list[str]

    monthly_capacity_tons: dict[str, float]    # fixed monthly contract volume per crop, set at creation
    storage_capacity_tons: float = 1_000_000.0

    # urgency/flexibility coefficient φ ∈ [0, 1], same convention as `Buyer`:
    #   φ = 1  → fill the contract this month at any price
    #   φ = 0  → never pay above the netback target; wait otherwise
    flexibility: float = 0.5

    # --- Price-responsive export volume ---
    # The monthly contract is a *baseline*; the volume actually pursued flexes
    # with the export margin (netback vs the domestic price the grain must be
    # sourced at) — see `request_purchases`. This is the quantity channel that
    # transmits world-market conditions (price, FX, duty) into domestic prices.
    # Parameters set by scenario from agent_parameters.json:exporter_defaults.
    volume_elasticity: float = 0.0      # 0 = fixed contract (old binary-gate behaviour)
    reference_margin: float = 0.2       # the "normal" margin ratio the base contract is sized for
    min_volume_factor: float = 0.4      # floor on volume as a fraction of the base contract
    max_volume_factor: float = 1.8      # cap on volume as a fraction of the base contract

    cash: float = 0.0
    storage: dict[str, float] = field(default_factory=dict)
    shipped_total: dict[str, float] = field(default_factory=dict)
    # Volume target (tons/crop) computed each month from the margin response and
    # used by `ship_out` so buying and shipping flex together. Not init state.
    _ship_target: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def _price_ceiling(self, netback: float) -> float:
        """Maximum domestic price the exporter will pay.

        Unlike domestic buyers (where `flexibility` allows paying *above* a
        desired target for urgency), for an exporter the netback is the hard
        rational ceiling — paying more guarantees a loss on resale. Here
        `flexibility` sets the minimum required gross margin as a fraction of
        the netback:

          ceiling = netback × flexibility

        flexibility = 1.0 → buy at breakeven (no margin floor, just fill the
                            contract)
        flexibility = 0.95 → require at least 5 % gross margin before buying
        flexibility = 0.80 → require at least 20 % gross margin

        The result is always ≤ netback, so the exporter never willingly loses
        money on export.
        """
        return netback * max(0.0, min(1.0, self.flexibility))

    def _volume_factor(self, netback: float, domestic_ref: float | None) -> float:
        """Margin-driven multiplier on the base contract volume. The export
        margin ratio is `(netback − domestic_ref) / netback`; volume responds
        linearly to its deviation from `reference_margin`:

            factor = clamp(1 + volume_elasticity·(margin_ratio − reference_margin),
                           min_volume_factor, max_volume_factor)

        A wide margin (high world price / weak rouble / cheap domestic grain)
        lifts the volume above the contract; a thin one cuts it. Returns 1.0 (the
        fixed-contract baseline) when elasticity is off or no domestic price
        reference exists yet."""
        if self.volume_elasticity <= 0 or not domestic_ref or netback <= 0:
            return 1.0
        margin_ratio = (netback - domestic_ref) / netback
        factor = 1.0 + self.volume_elasticity * (margin_ratio - self.reference_margin)
        return max(self.min_volume_factor, min(self.max_volume_factor, factor))

    def request_purchases(self, world_prices: dict[str, float],
                           export_duty_rate: dict[str, float],
                           export_fee_per_ton: dict[str, float] | None = None,
                           domestic_prices: dict[str, float] | None = None) -> list[DemandRequest]:
        """Go shopping nationwide to fill this month's export contract.

        The contract volume is a *baseline*: the volume actually pursued flexes
        with the export margin (see `_volume_factor`), so a profitable world
        market pulls more grain out of the country and a squeezed one ships
        less — the price-responsive export demand that transmits global
        conditions into domestic prices through quantities.

        The per-trade search ceiling is still the FOB netback (world price net
        of the percentage export duty *and* the per-ton export fee) scaled by
        `flexibility` (minimum margin requirement) — so the exporter never bids
        above what it can recoup. `search_market` ranks every seller by
        delivered cost (ask + transport) and fills from cheapest first.
        """
        export_fee_per_ton = export_fee_per_ton or {}
        domestic_prices = domestic_prices or {}
        requests: list[DemandRequest] = []
        for crop_id in self.handled_crop_ids:
            base_capacity = self.monthly_capacity_tons.get(crop_id, 0.0)
            if base_capacity <= 0:
                continue

            world_price = world_prices.get(crop_id)
            if not world_price:
                continue
            duty = export_duty_rate.get(crop_id, 0.0)
            fee = export_fee_per_ton.get(crop_id, 0.0)
            netback = world_price * (1.0 - duty) - fee
            if netback <= 0:
                continue

            # Flex the contract by the margin, then cap by storage. This target
            # drives both how much to buy now and how much `ship_out` exports.
            target = base_capacity * self._volume_factor(netback, domestic_prices.get(crop_id))
            target = min(target, self.storage_capacity_tons)
            self._ship_target[crop_id] = target

            current_stock = self.storage.get(crop_id, 0.0)
            quantity = max(target - current_stock, 0.0)
            if quantity <= 1e-6:
                continue

            ceiling = self._price_ceiling(netback)
            requests.append(DemandRequest(
                buyer_id=self.id, crop_id=crop_id, quantity=quantity,
                price_ceiling=ceiling, lat=self.lat, lon=self.lon,
                # The netback is a fundamental, exogenous valuation (world FOB
                # net of duty), so it can safely drive surplus-split pricing —
                # farmers selling to exporters get bargained up toward it.
                valuation=ceiling,
            ))
        return requests

    def ship_out(self) -> dict[str, float]:
        """Export everything currently held, up to this month's volume target.
        Returns the shipped tonnage per crop (used for trade-balance reporting).

        The cap is the margin-flexed `_ship_target` set in `request_purchases`
        (falling back to the base contract for a crop not requested this month),
        so buying and shipping move together when the export margin shifts."""
        shipped: dict[str, float] = {}
        for crop_id, qty in list(self.storage.items()):
            capacity = self._ship_target.get(crop_id, self.monthly_capacity_tons.get(crop_id, qty))
            amount = min(qty, capacity)
            if amount <= 1e-9:
                continue
            self.storage[crop_id] = qty - amount
            self.shipped_total[crop_id] = self.shipped_total.get(crop_id, 0.0) + amount
            shipped[crop_id] = amount
        return shipped

    # ------------------------------------------------------------------ settlement callbacks
    def receive_goods(self, crop_id: str, quantity: float) -> None:
        self.storage[crop_id] = self.storage.get(crop_id, 0.0) + quantity

    def pay(self, amount: float) -> None:
        self.cash -= amount

    def receive_export_revenue(self, amount: float) -> None:
        self.cash += amount
