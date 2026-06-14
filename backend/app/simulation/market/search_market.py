"""Decentralised search-and-match commodity market.

This replaces the earlier "regional double auction" with a model that mirrors
how grain actually changes hands in practice: there is no central order book —
buyers (processors, traders, exporters, the state) go shopping. Each seller
posts an asking price at *their own* location (formed from price expectations
and storage pressure — see `Farmer.decide_sales`); each buyer knows every
seller's location and price, works out the *delivered* cost (ask price plus
the cheaper of road/rail transport for the distance involved, see
`app.simulation.logistics`), and buys progressively from the cheapest sources
until its monthly demand is filled or the delivered cost exceeds what it is
willing to pay (its `price_ceiling`, shaped by its flexibility — see
`Buyer.request_purchases` / `Exporter.request_purchases`).

Buyers are processed in random order each month (the `rng` the engine passes
in is reseeded from the world's own RNG, so a run stays reproducible) — this
avoids systematically favouring whichever agent happens to be first in a list,
exactly the kind of artefact a real, decentralised market would not have.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from app.simulation.geo import haversine_km
from app.simulation.logistics import LogisticsConfig, transport_cost_per_ton


@dataclass
class SupplyOffer:
    """A seller's standing offer at their own location (no transport baked in)."""

    seller_id: str
    crop_id: str
    quantity: float
    ask_price: float   # RUB/ton the seller wants to net at their own site
    lat: float
    lon: float


@dataclass
class DemandRequest:
    """A buyer's monthly purchase order, expressed as what it is willing to
    pay *delivered* — i.e. including whatever it costs to transport the grain
    from the seller's site to the buyer's. `price_ceiling=None` means "no
    limit" (the buyer's flexibility is 1.0: it will pay any price to secure
    the volume it needs)."""

    buyer_id: str
    crop_id: str
    quantity: float
    price_ceiling: float | None
    lat: float
    lon: float
    # The buyer's *delivered* valuation (RUB/ton) — what the good is actually
    # worth to it (a processor's target price, an exporter's netback). Used by
    # the surplus-split pricing to lift the executed price above the seller's
    # ask toward this value. `None` falls back to `price_ceiling`.
    valuation: float | None = None


@dataclass
class ExecutedTrade:
    buyer_id: str
    seller_id: str
    crop_id: str
    quantity: float
    unit_price: float       # RUB/ton received by the seller (bargained between their ask and the buyer's valuation)
    transport_cost: float   # RUB/ton paid by the buyer on top of the seller's price
    distance_km: float

    @property
    def delivered_price(self) -> float:
        """RUB/ton the buyer actually pays, all-in."""
        return self.unit_price + self.transport_cost


def match_supply_and_demand(
    offers: list[SupplyOffer],
    requests: list[DemandRequest],
    logistics: LogisticsConfig,
    rng: random.Random,
    bargaining_power: float = 0.0,
) -> list[ExecutedTrade]:
    """Run one month of decentralised search-and-match across every crop.

    For each buyer (in random order), and for each crop it wants, every
    remaining offer of that crop is ranked by *delivered* cost — ask price
    plus point-to-point transport — offers above the buyer's price ceiling
    are discarded, and the buyer fills its order from the cheapest remaining
    sources until either its quantity is satisfied or it runs out of
    acceptable offers. Partially-filled offers stay in the pool (at their
    original ask price) for the next buyer to consider.

    **Surplus-split pricing.** A trade does not execute at the seller's ask;
    it executes at a price between the ask and the buyer's valuation, so the
    demand side lifts the price even when supply is not scarce:

        unit_price = ask + bargaining_power · max(0, (valuation − transport) − ask)

    `bargaining_power` ∈ [0, 1] is the *seller's* share of the surplus — 0
    reproduces the old "buyer pays the ask" behaviour, 1 hands the seller the
    buyer's full valuation. The buyer's valuation is `DemandRequest.valuation`
    (falling back to its `price_ceiling`); since that valuation is bounded by
    the ceiling the offer already passed, the delivered price can never exceed
    what the buyer was willing to pay.
    """
    remaining: dict[str, list[SupplyOffer]] = {}
    for offer in offers:
        if offer.quantity <= 1e-9:
            continue
        remaining.setdefault(offer.crop_id, []).append(
            SupplyOffer(offer.seller_id, offer.crop_id, offer.quantity, offer.ask_price, offer.lat, offer.lon)
        )

    shuffled_requests = list(requests)
    rng.shuffle(shuffled_requests)

    trades: list[ExecutedTrade] = []
    for request in shuffled_requests:
        if request.quantity <= 1e-9:
            continue
        pool = remaining.get(request.crop_id)
        if not pool:
            continue

        ranked = []
        for offer in pool:
            if offer.quantity <= 1e-9:
                continue
            distance = haversine_km(offer.lat, offer.lon, request.lat, request.lon)
            transport = transport_cost_per_ton(distance, logistics)
            delivered = offer.ask_price + transport
            if request.price_ceiling is not None and delivered > request.price_ceiling + 1e-9:
                continue
            ranked.append((delivered, transport, distance, offer))
        ranked.sort(key=lambda row: row[0])

        # Surplus-split pricing uses the buyer's *fundamental* valuation only
        # (output-derived price, export netback, intervention floor). When a
        # buyer has none — a trader whose only reference is the going market
        # price — `valuation` is None and the trade prices at the seller's ask:
        # bargaining toward a market-referenced value would be circular and
        # would compound the price upward without bound. Note this is distinct
        # from `price_ceiling`, which still gates *which* offers are eligible.
        valuation = request.valuation

        still_needed = request.quantity
        for delivered, transport, distance, offer in ranked:
            if still_needed <= 1e-9:
                break
            qty = min(still_needed, offer.quantity)
            if qty <= 1e-9:
                continue
            # Move the executed farm-gate price from the seller's ask toward the
            # buyer's valuation (net of the transport the buyer must also pay),
            # splitting the surplus by `bargaining_power`. Never below the ask.
            unit_price = offer.ask_price
            if bargaining_power > 0 and valuation is not None:
                surplus = (valuation - transport) - offer.ask_price
                if surplus > 0:
                    unit_price = offer.ask_price + bargaining_power * surplus
            trades.append(ExecutedTrade(
                buyer_id=request.buyer_id, seller_id=offer.seller_id, crop_id=request.crop_id,
                quantity=qty, unit_price=unit_price, transport_cost=transport, distance_km=distance,
            ))
            offer.quantity -= qty
            still_needed -= qty

        pool[:] = [o for o in pool if o.quantity > 1e-9]

    return trades
