"""Simulation engine: drives the world forward one calendar month at a time.

Each `step()` runs the agents through a fixed phase order — this ordering is
the heart of the model and mirrors a real marketing-year cycle:

 0. Advance the exogenous global-market state (stochastic world-price shock and
    RUB/USD rate — see `World.advance_market_dynamics`)
 1. Harvests (whenever a sown crop reaches its harvest month, with correlated
    weather shocks) — may force overflow sales. Runs *before* planting so a
    September spring harvest frees land the same month's winter sowing reuses.
 2. Planting decisions (in each crop's own sowing month) + sowing subsidies
 3. Storage spoilage + fixed operating costs (land rent/depreciation/overhead)
 4. Price-expectation update (Nerlove adaptive scheme) — farmers absorb last
    month's observed prices into their belief about this month's price before
    deciding anything else
 5. Offer/request collection: farmers post `SupplyOffer`s (priced from their
    updated expectations, see `Farmer.decide_sales`); buyers, exporters and
    the government post `DemandRequest`s (their fixed monthly demand, shaped
    by their flexibility coefficient into a price ceiling — or no offers/
    requests at all where policy doesn't call for one)
 6. Decentralised search-and-match clearing: every buyer shops the *whole
    country* for the cheapest delivered grain (ask price + transport cost)
    within its price ceiling — see `search_market.match_supply_and_demand`
 7. Settlement: goods and money change hands at the agreed prices; trades are
    bucketed by (region, crop) and aggregated into one volume-weighted
    "regional clearing price" record per bucket, preserving the shape of the
    history the UI/charts expect
 8. Buyers consume their monthly throughput; exporters ship what they can
 9. Agent lifecycle (yearly): close chronically insolvent farms AND buyers,
    admit new entrants on both sides by regional profitability
10. Bookkeeping: price/volume history, national averages, per-step log
"""
from __future__ import annotations

from app.simulation.agents.buyer import Buyer
from app.simulation.agents.exporter import Exporter
from app.simulation.agents.farmer import Farmer
from app.simulation.agents.government import Government
from app.simulation.market.search_market import (
    DemandRequest,
    ExecutedTrade,
    SupplyOffer,
    match_supply_and_demand,
)
from app.simulation.world import MonthlyExportRecord, World


class SimulationEngine:
    def __init__(self, world: World):
        self.world = world

    def run(self, n_steps: int) -> list[dict]:
        return [self.step() for _ in range(n_steps)]

    # ------------------------------------------------------------------ one month
    def step(self) -> dict:
        w = self.world
        w.advance_month()
        w.advance_market_dynamics()
        year, month = w.year, w.month

        for farmer in w.farmers:
            farmer.start_month_snapshot()
        for buyer in w.buyers:
            buyer.start_month_snapshot()

        # Harvest BEFORE planting so a September spring-crop harvest frees the
        # land that month's winter sowing then re-uses (winter and spring crops
        # share the same finite hectares — see Farmer._decide_planting).
        # Each phase reports the grain it moved into/out of the modelled economy
        # so the monthly record carries a full mass balance (see _log_step).
        forced_sales, harvested_tons = self._harvest_phase(year, month)
        self._planting_phase(year, month)
        spoiled_tons = self._spoilage_phase()
        self._update_expectations()
        offers, requests = self._collect_offers_and_requests(forced_sales)
        trades = match_supply_and_demand(offers, requests, w.logistics, w.rng,
                                         bargaining_power=w.surplus_bargaining_power)
        trade_summary = self._settle(trades, requests, offers)
        dumped_tons = self._return_unsold_offers(offers, trades)
        consumed_tons, exported_tons = self._consumption_and_exports(month)
        national_prices = w.finalize_national_prices()
        self._demand_update_phase()

        lifecycle = self._lifecycle_phase(year, month)

        flows = {
            "harvested_tons": harvested_tons,
            "consumed_tons": consumed_tons,
            "spoiled_tons": spoiled_tons,
            "exported_tons": exported_tons,
            "dumped_tons": dumped_tons,
        }
        record = self._log_step(year, month, trade_summary, national_prices, lifecycle, flows)
        w.step_log.append(record)
        return record

    # ------------------------------------------------------------------ phase 1
    def _planting_phase(self, year: int, month: int) -> None:
        w = self.world
        for farmer in w.farmers:
            snapshot = w.build_snapshot(farmer.region_id)
            slot = farmer.maybe_plant(year, month, snapshot, w.crops)
            if slot is not None:
                w.government.pay_subsidies([farmer], slot)

    # ------------------------------------------------------------------ phase 2
    def _harvest_phase(self, year: int, month: int) -> tuple[dict[str, dict[str, float]], float]:
        """Realise due harvests. Returns the per-farmer forced-overflow sales and
        the total tonnage harvested this month (= the rise in farmer storage plus
        whatever overflowed capacity and was dumped onto the market as forced)."""
        w = self.world
        before = self._total_farmer_storage()
        forced: dict[str, dict[str, float]] = {}
        forced_total = 0.0
        for farmer in w.farmers:
            overflow = farmer.maybe_harvest(year, month, w.crops, w.weather)
            if overflow:
                forced[farmer.id] = overflow
                forced_total += sum(overflow.values())
        harvested = (self._total_farmer_storage() - before) + forced_total
        return forced, harvested

    # ------------------------------------------------------------------ phase 3
    def _spoilage_phase(self) -> float:
        """Apply storage spoilage + (cash-only) fixed/credit charges. Returns the
        tonnage lost to spoilage — the drop in farmer storage, since the fixed and
        credit charges touch cash only, not stock."""
        w = self.world
        before = self._total_farmer_storage()
        for farmer in w.farmers:
            farmer.apply_storage_losses(w.crops)
            farmer.apply_fixed_costs()
            farmer.apply_credit_charges()
        return before - self._total_farmer_storage()

    @staticmethod
    def _sum_storage(agents) -> float:
        return sum(sum(a.storage.values()) for a in agents)

    def _total_farmer_storage(self) -> float:
        return self._sum_storage(self.world.farmers)

    # ------------------------------------------------------------------ phase 4
    def _update_expectations(self) -> None:
        """Nerlove adaptive price expectations: every farmer absorbs the most
        recently observed price (its home region's, falling back to the
        national average) into its belief about this month's price *before*
        it decides what to plant or what to ask for its grain — so both
        decisions are driven by the same up-to-date expectation."""
        w = self.world
        for farmer in w.farmers:
            snapshot = w.build_snapshot(farmer.region_id)
            farmer.update_price_expectations(snapshot, w.crops)
        # Buyers maintain a smoothed mean-reversion price anchor too (drives the
        # strategic-inventory adjustment in Buyer.request_purchases), updated
        # here so their belief is current before they decide what to buy.
        for buyer in w.buyers:
            buyer.update_price_expectations(w.build_snapshot(buyer.region_id))

    # ------------------------------------------------------------------ phase 5
    def _collect_offers_and_requests(
        self, forced_sales: dict[str, dict[str, float]]
    ) -> tuple[list[SupplyOffer], list[DemandRequest]]:
        """Gather every standing offer and demand request for the month.

        Unlike the old order-book model there is no notion of "submitting to
        a region's exchange" any more — every agent simply states what it has
        to sell or needs to buy, *at its own location*, and the search-and-
        match phase works out who ends up trading with whom based on delivered
        cost. Government interventions are priced from each region's *last*
        observed clearing price (the state reacts with a one-month lag, just
        like real intervention mechanisms do), and posted from that region's
        coordinates (its hub, in lieu of the state having its own footprint).
        """
        w = self.world
        offers: list[SupplyOffer] = []
        requests: list[DemandRequest] = []

        month_idx = w.month_index
        goods = w.crops.market_goods()
        world_prices = w.world_prices_now(month_idx)
        duty_rates = {good: w.government.export_duty_rate(good) for good in goods}
        export_fees = {good: w.government.export_fee_per_ton(good) for good in goods}
        # Last national farm-gate average per good — the domestic sourcing-cost
        # reference exporters compare against their netback to flex export volume.
        domestic_prices = {good: w.last_national_price(good) for good in goods}

        for farmer in w.farmers:
            snapshot = w.build_snapshot(farmer.region_id)
            offers.extend(farmer.decide_sales(snapshot, w.crops, forced_sales.get(farmer.id)))

        for buyer in w.buyers:
            snapshot = w.build_snapshot(buyer.region_id)
            requests.extend(buyer.request_purchases(snapshot))

        for exporter in w.exporters:
            requests.extend(exporter.request_purchases(world_prices, duty_rates, export_fees, domestic_prices))

        # Only goods with a floor or ceiling configured can ever produce an
        # intervention, so scan just those — not every region × every good. With
        # no interventions set (the default) this skips the whole sweep instead
        # of building and discarding ~(regions × goods) None requests each month.
        policy = w.government.policy
        intervention_goods = set(policy.intervention_floor_price) | set(policy.intervention_ceiling_price)
        for region in w.regions:
            for good in intervention_goods:
                last_price = w.last_price(region.id, good)
                last_volume = w.last_volume(region.id, good)
                buy_request = w.government.intervention_buy_request(
                    good, last_price, last_volume, region.lat, region.lon)
                if buy_request is not None:
                    requests.append(buy_request)
                sell_offer = w.government.intervention_sell_offer(
                    good, last_price, last_volume, region.lat, region.lon)
                if sell_offer is not None:
                    offers.append(sell_offer)

        return offers, requests

    # ------------------------------------------------------------------ phase 6 + 7
    def _settle(self, trades: list[ExecutedTrade], requests: list[DemandRequest],
                offers: list[SupplyOffer]) -> dict[str, dict[str, float]]:
        """Apply every executed trade to both sides' cash/storage, bucket
        trades by (region, crop) and record one volume-weighted clearing
        price per bucket.

        Clearing price recorded = farm-gate unit price (what the seller
        receives) scaled by a **two-sided supply/demand-pressure** factor. The
        national imbalance for the crop, `(demand − supply) / max(demand, supply)
        ∈ [−1, 1]`, is compared to its own smoothed baseline (EMA, see
        `world._imbalance_ema`): the price is scaled by `demand_price_premium ×
        (imbalance − baseline)`. Working off the *deviation* is essential — the
        raw imbalance is structurally biased positive (farmers withhold stock, so
        requested demand persistently exceeds the tonnage offered even with grain
        in storage) and seasonally lumpy; the baseline absorbs both, leaving only
        transient tightness/glut to move the price. A positive deviation (demand
        spikes / supply dries up beyond normal) lifts the price; a negative one
        (glut) pushes it down. This is the channel through which *quantity* moves
        price, so endogenous demand (buyers' price-elastic throughput and
        exporters' margin-flexed volume) feeds back into the clearing price and
        damps spikes. `supply` is the tonnage offered this month, `demand` the
        tonnage requested — both national, per crop.

        The region a bucket is attributed to is the *seller's* home region
        when it has one (almost always — farmers and the government's
        regional reserves), falling back to the buyer's when it doesn't (a
        government sell-intervention has no region of its own).
        """
        w = self.world
        summary: dict[str, dict[str, float]] = {}
        buckets: dict[tuple[str, str], dict[str, float]] = {}

        for trade in trades:
            self._apply_trade(trade)

            seller = w.find_agent(trade.seller_id)
            buyer = w.find_agent(trade.buyer_id)
            region_id = getattr(seller, "region_id", None) or getattr(buyer, "region_id", None)
            if region_id is None:
                continue

            bucket = buckets.setdefault((region_id, trade.crop_id), {"volume": 0.0, "value": 0.0})
            bucket["volume"] += trade.quantity
            bucket["value"] += trade.unit_price * trade.quantity  # farm-gate price: what farmers actually receive

            crop_summary = summary.setdefault(trade.crop_id, {"volume": 0.0, "value": 0.0})
            crop_summary["volume"] += trade.quantity
            crop_summary["value"] += trade.delivered_price * trade.quantity

        # National supply/demand totals per crop this month: tonnage requested
        # (across all buyers/exporters) vs. tonnage offered (across all sellers).
        # Their imbalance is the two-sided price-pressure signal applied below.
        total_demand_per_crop: dict[str, float] = {}
        for req in requests:
            total_demand_per_crop[req.crop_id] = total_demand_per_crop.get(req.crop_id, 0.0) + req.quantity
        total_supply_per_crop: dict[str, float] = {}
        for off in offers:
            total_supply_per_crop[off.crop_id] = total_supply_per_crop.get(off.crop_id, 0.0) + off.quantity

        # Pressure factor per crop: premium × (current imbalance − smoothed
        # baseline), clamped, computed once per crop and reused for every region
        # the crop cleared in. The baseline EMA is updated here (once per crop).
        pressure: dict[str, float] = {}
        alpha = w.demand_pressure_smoothing
        for crop_id in {cid for _, cid in buckets}:
            demand = total_demand_per_crop.get(crop_id, 0.0)
            supply = total_supply_per_crop.get(crop_id, 0.0)
            denom = max(demand, supply)
            if denom <= 1e-6:
                pressure[crop_id] = 0.0
                continue
            imbalance = (demand - supply) / denom   # ∈ [−1, 1]
            # Seed the baseline at the first observation so there is no cold-start
            # shock (deviation 0 on month one), then track it with the EMA.
            baseline = w._imbalance_ema.get(crop_id, imbalance)
            deviation = max(-1.0, min(1.0, imbalance - baseline))
            w._imbalance_ema[crop_id] = baseline + alpha * (imbalance - baseline)
            pressure[crop_id] = w.demand_price_premium * deviation

        for (region_id, crop_id), bucket in buckets.items():
            if bucket["volume"] <= 0:
                continue
            avg_unit_price = bucket["value"] / bucket["volume"]
            demand_signal = avg_unit_price * (1.0 + pressure.get(crop_id, 0.0))
            w.record_clearing(region_id, crop_id, demand_signal, bucket["volume"])

        return summary

    def _return_unsold_offers(self, offers: list[SupplyOffer], trades: list[ExecutedTrade]) -> float:
        """Grain a farmer withdrew from storage to offer for sale (see
        `Farmer.decide_sales`, which deducts the offered quantity from
        `storage` up front) but that found no buyer this month must go back
        into storage — otherwise it silently disappears from the model
        (produced and withdrawn, yet neither sold nor consumed nor stored).

        Returns the tonnage that could *not* be returned because the farm's
        storage was already full (forced-overflow grain a farm had to dump and
        cannot reabsorb): this is grain that genuinely leaves the economy, so it
        is reported as `dumped_tons` for the monthly mass balance."""
        w = self.world
        sold_by_seller_crop: dict[tuple[str, str], float] = {}
        for trade in trades:
            key = (trade.seller_id, trade.crop_id)
            sold_by_seller_crop[key] = sold_by_seller_crop.get(key, 0.0) + trade.quantity

        dumped = 0.0
        for offer in offers:
            seller = w.find_agent(offer.seller_id)
            if not isinstance(seller, Farmer):
                continue
            key = (offer.seller_id, offer.crop_id)
            unsold = offer.quantity - sold_by_seller_crop.get(key, 0.0)
            if unsold > 1e-6:
                stored = seller.return_unsold(offer.crop_id, unsold)
                dumped += unsold - stored
        return dumped

    def _apply_trade(self, trade: ExecutedTrade) -> None:
        w = self.world
        crop_id = trade.crop_id
        qty = trade.quantity
        seller = w.find_agent(trade.seller_id)
        buyer = w.find_agent(trade.buyer_id)
        revenue = trade.unit_price * qty
        cost = trade.delivered_price * qty

        if isinstance(seller, Farmer):
            # Direct tax (6 %) is withheld from the farmer's proceeds on every
            # deal; the per-ton subsidy is paid out on the same tonnage sold.
            tax = w.government.collect_direct_tax(revenue)
            seller.receive_payment(revenue - tax)
            w.government.pay_sale_subsidy(seller, crop_id, qty)
        elif isinstance(seller, Government):
            # State sell-interventions are not taxed/subsidised (it would only
            # move budget money in a circle).
            seller.release_goods(crop_id, qty)
            seller.receive_payment(revenue)

        if isinstance(buyer, Buyer):
            buyer.receive_goods(crop_id, qty)
            buyer.pay(cost)
        elif isinstance(buyer, Exporter):
            buyer.receive_goods(crop_id, qty)
            buyer.pay(cost)
        elif isinstance(buyer, Government):
            buyer.receive_goods(crop_id, qty)
            buyer.pay(cost)

    # ------------------------------------------------------------------ phase 8
    def _consumption_and_exports(self, month: int) -> tuple[float, float]:
        """Buyers process their monthly throughput; exporters ship abroad.
        Returns (tonnage consumed by buyers, tonnage exported) — both are grain
        leaving the modelled domestic economy, for the monthly mass balance."""
        w = self.world
        before_buyers = self._sum_storage(w.buyers)
        for buyer in w.buyers:
            buyer.consume(w.build_snapshot(buyer.region_id))
        consumed = before_buyers - self._sum_storage(w.buyers)

        exported = 0.0
        month_idx = w.month_index
        for exporter in w.exporters:
            shipped = exporter.ship_out()
            for crop_id, qty in shipped.items():
                exported += qty
                world_price = w.world_price_for(crop_id, month_idx) or 0.0
                duty_rate = w.government.export_duty_rate(crop_id)
                gross = world_price * qty
                duty_amount = gross * duty_rate
                fee_amount = w.government.collect_export_fee(crop_id, qty)  # per-ton fee, credited to the budget
                exporter.receive_export_revenue(gross - duty_amount - fee_amount)
                w.government.receive_payment(duty_amount)
                w.export_history.append(MonthlyExportRecord(
                    year=w.year, month=month, crop_id=crop_id, exporter_id=exporter.id,
                    destination=exporter.destination_country, quantity_tons=qty,
                    revenue_rub=gross - duty_amount - fee_amount, duty_rub=duty_amount,
                    fee_rub=fee_amount,
                ))
        return consumed, exported

    # ------------------------------------------------------------------ phase 8 (price-elastic demand)
    def _demand_update_phase(self) -> None:
        """After prices are finalized, rescale each buyer's processing throughput
        by its constant-elasticity response to the price it paid this month
        (see Buyer.update_demand). Sustained overpayment shrinks demand, cheap
        input expands it (within capacity) — continuous, two-sided endogenous
        demand that lets the market clear without ratchets or counters."""
        w = self.world
        for buyer in w.buyers:
            snapshot = w.build_snapshot(buyer.region_id)
            buyer.update_demand(snapshot)

    # ------------------------------------------------------------------ phase 9 (lifecycle)
    def _lifecycle_phase(self, year: int, month: int) -> dict[str, list[str]]:
        """End-of-month agent lifecycle: update profitability signals for both
        farms and buyers, then once per year (January) close chronically
        insolvent agents and admit new entrants on each side.

        Closures happen when `insolvent_months >= closure_months`. Entries
        happen per region with probability proportional to regional
        profitability (see `_entry_probability` / `_buyer_entry_probability` in
        scenario.py). The two sides are fully symmetric — a coherent two-sided
        market demography rather than the old farmers-only lifecycle.
        """
        # Lazy import avoids module-level circularity (scenario → world → engine).
        from app.simulation.scenario import (
            _buyer_entry_probability,
            _entry_probability,
            _spawn_single_buyer,
            _spawn_single_farmer,
        )

        w = self.world
        for farmer in w.farmers:
            farmer.end_month_update()
        for buyer in w.buyers:
            buyer.end_month_update(buyer.max_debt)

        farms_closed: list[str] = []
        farms_spawned: list[str] = []
        buyers_closed: list[str] = []
        buyers_spawned: list[str] = []

        if month != 1:
            return {"farms_closed": farms_closed, "farms_spawned": farms_spawned,
                    "buyers_closed": buyers_closed, "buyers_spawned": buyers_spawned}

        # --- farm closures / entries ---
        if w.farm_closure_months > 0:
            for f in [f for f in w.farmers if f.insolvent_months >= w.farm_closure_months]:
                w.farmers.remove(f)
                w.unregister_agent(f.id)
                farms_closed.append(f.id)
        if w.farm_entry_rate_max > 0:
            for region in w.regions:
                if region.is_border or region.sown_area_ha <= 0:
                    continue
                regional_farmers = [f for f in w.farmers if f.region_id == region.id]
                p = _entry_probability(regional_farmers, w.farm_entry_rate_max, w.farm_entry_profitability_ha)
                if w.rng.random() < p:
                    new_farmer = _spawn_single_farmer(region, w.crops, w, w.rng)
                    w.farmers.append(new_farmer)
                    w.register_agent(new_farmer)
                    farms_spawned.append(new_farmer.id)

        # --- buyer closures / entries (symmetric) ---
        if w.buyer_closure_months > 0:
            for b in [b for b in w.buyers if b.insolvent_months >= w.buyer_closure_months]:
                w.buyers.remove(b)
                w.unregister_agent(b.id)
                buyers_closed.append(b.id)
        if w.buyer_entry_rate_max > 0:
            for region in w.regions:
                if region.is_border or region.population <= 0:
                    continue
                regional_buyers = [b for b in w.buyers if b.region_id == region.id]
                p = _buyer_entry_probability(regional_buyers, w.buyer_entry_rate_max, w.buyer_entry_profitability)
                if w.rng.random() < p:
                    new_buyer = _spawn_single_buyer(region, w.crops, w, w.rng)
                    if new_buyer is not None:
                        w.buyers.append(new_buyer)
                        w.register_agent(new_buyer)
                        buyers_spawned.append(new_buyer.id)

        return {"farms_closed": farms_closed, "farms_spawned": farms_spawned,
                "buyers_closed": buyers_closed, "buyers_spawned": buyers_spawned}

    # ------------------------------------------------------------------ phase 10
    def _log_step(self, year: int, month: int, trade_summary: dict[str, dict[str, float]],
                  national_prices: dict[str, float],
                  lifecycle: dict[str, list[str]] | None = None,
                  flows: dict[str, float] | None = None) -> dict:
        w = self.world
        lifecycle = lifecycle or {}
        flows = flows or {}
        total_farmer_storage = self._sum_storage(w.farmers)
        total_buyer_storage = self._sum_storage(w.buyers)
        total_exporter_storage = self._sum_storage(w.exporters)
        total_reserves = sum(w.government.reserves.values())
        return {
            "year": year,
            "month": month,
            "national_prices": dict(national_prices),
            "traded_volumes": {cid: b["volume"] for cid, b in trade_summary.items()},
            "traded_value": {cid: b["value"] for cid, b in trade_summary.items()},
            "government_cash": w.government.cash,
            "government_reserves": dict(w.government.reserves),
            "taxes_collected": w.government.taxes_collected,
            "export_fees_collected": dict(w.government.export_fees_collected),
            "subsidies_paid": dict(w.government.subsidies_paid),
            "sale_subsidies_paid": dict(w.government.sale_subsidies_paid),
            "farmer_count": len(w.farmers),
            "buyer_count": len(w.buyers),
            "farms_closed": len(lifecycle.get("farms_closed", [])),
            "farms_spawned": len(lifecycle.get("farms_spawned", [])),
            "buyers_closed": len(lifecycle.get("buyers_closed", [])),
            "buyers_spawned": len(lifecycle.get("buyers_spawned", [])),
            "fx_rate": w.fx_rate,
            "world_price_shock": w.world_price_shock,
            "total_farmer_storage": total_farmer_storage,
            "total_buyer_storage": total_buyer_storage,
            "total_exporter_storage": total_exporter_storage,
            "total_government_reserves": total_reserves,
            # Total grain held anywhere in the modelled economy at month end —
            # the stock the monthly flows (below) reconcile against.
            "total_grain_in_system": (
                total_farmer_storage + total_buyer_storage
                + total_exporter_storage + total_reserves
            ),
            # Per-month grain flows: the only inflow is the harvest; grain leaves
            # the economy through consumption, spoilage, export and forced dumping.
            # ΔTotal grain == harvested − consumed − spoiled − exported − dumped.
            "harvested_tons": flows.get("harvested_tons", 0.0),
            "consumed_tons": flows.get("consumed_tons", 0.0),
            "spoiled_tons": flows.get("spoiled_tons", 0.0),
            "exported_tons": flows.get("exported_tons", 0.0),
            "dumped_tons": flows.get("dumped_tons", 0.0),
        }
