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

    cash: float = 0.0
    storage: dict[str, float] = field(default_factory=dict)
    shipped_total: dict[str, float] = field(default_factory=dict)

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

    def request_purchases(self, world_prices: dict[str, float],
                           export_duty_rate: dict[str, float],
                           export_fee_per_ton: dict[str, float] | None = None) -> list[DemandRequest]:
        """Go shopping nationwide to fill this month's fixed export contract.

        The exporter's search ceiling is the FOB netback (world price net of
        the percentage export duty *and* the per-ton export fee the state
        charges at shipment) scaled by `flexibility` (minimum margin
        requirement) — so the exporter never bids above what it can recoup
        from export sales. `search_market` ranks every seller by delivered
        cost (ask + transport) and fills from cheapest first, naturally
        filtering out distant sources whose transport cost eats into the
        margin.
        """
        export_fee_per_ton = export_fee_per_ton or {}
        requests: list[DemandRequest] = []
        for crop_id in self.handled_crop_ids:
            capacity = self.monthly_capacity_tons.get(crop_id, 0.0)
            current_stock = self.storage.get(crop_id, 0.0)
            quantity = max(min(capacity, self.storage_capacity_tons) - current_stock, 0.0)
            if quantity <= 1e-6:
                continue

            world_price = world_prices.get(crop_id)
            if not world_price:
                continue
            duty = export_duty_rate.get(crop_id, 0.0)
            fee = export_fee_per_ton.get(crop_id, 0.0)
            netback = world_price * (1.0 - duty) - fee
            if netback <= 0:
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
        """Export everything currently held, up to monthly capacity. Returns
        the shipped tonnage per crop (used for trade-balance reporting)."""
        shipped: dict[str, float] = {}
        for crop_id, qty in list(self.storage.items()):
            capacity = self.monthly_capacity_tons.get(crop_id, qty)
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
