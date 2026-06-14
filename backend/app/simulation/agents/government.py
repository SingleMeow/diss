"""Government agent: fiscal levers, trade policy, market interventions, subsidies.

The three primary fiscal levers — the ones that move money between the agents
and the budget on every relevant transaction — are:

* **Direct tax** — a flat share (default 6 %, e.g. УСН «доходы») withheld from
  the *seller's* revenue on every deal and credited to the budget. The seller
  nets its proceeds minus the tax; the buyer is unaffected.
* **Export fee** — a per-ton charge levied on the *exporter* for every ton it
  ships out (RUB/ton, per crop). Kept here as a policy parameter — never on
  the crop definitions — because different scenarios change it mid-run.
* **Subsidies** — direct cash transfers to farmers, in two flavours that can
  target different crops:
    - *per hectare*, paid at sowing time (input support; nudges the crop mix);
    - *per ton sold*, paid on each ton a farmer actually markets (output
      support; rewards marketed production).

On top of those it runs two market-stabilising interventions:

* **Intervention floor price** — if a region's clearing price falls below
  this, the state goes shopping as a buyer at the floor price
  ("закупочные интервенции"), propping up farm incomes and building reserves.
* **Intervention ceiling price** — if price rises above this, the state
  posts grain from its reserves for sale at the ceiling price
  ("товарные интервенции"), cooling the market for domestic consumers.

(The legacy percentage **export duty** — a share of the world price withheld
before an exporter's bid reaches the domestic market — is retained alongside
the new per-ton export fee.)

In the search-and-match market the state participates exactly like any other
agent: a buy intervention becomes a `DemandRequest` (with a hard price ceiling
at the floor — the state will not chase the price up) competing for the
cheapest grain nationwide, and a sell intervention becomes a `SupplyOffer`
posted at the ceiling price from the region whose market it is meant to cool.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.simulation.agents.farmer import Farmer
from app.simulation.market.search_market import DemandRequest, SupplyOffer


@dataclass
class GovernmentPolicy:
    # --- fiscal levers ---
    direct_tax_rate: float = 0.06                                             # share of each deal's seller revenue withheld as direct tax
    export_fee_per_ton: dict[str, float] = field(default_factory=dict)       # crop_id -> RUB/ton charged from the exporter per ton shipped
    subsidy_per_ha: dict[str, float] = field(default_factory=dict)           # crop_id -> RUB/ha paid at sowing
    subsidy_per_ton: dict[str, float] = field(default_factory=dict)          # crop_id -> RUB/ton paid per ton sold
    # --- legacy / trade policy ---
    export_duty_rate: dict[str, float] = field(default_factory=dict)          # crop_id -> share of world price withheld
    intervention_floor_price: dict[str, float] = field(default_factory=dict)  # crop_id -> RUB/ton
    intervention_ceiling_price: dict[str, float] = field(default_factory=dict)  # crop_id -> RUB/ton
    intervention_volume_share: float = 0.15   # max share of a region's traded volume the state will move per month


AGENT_ID = "government"


@dataclass
class Government:
    policy: GovernmentPolicy
    cash: float = 0.0
    reserves: dict[str, float] = field(default_factory=dict)   # crop_id -> tons held in state reserve
    subsidies_paid: dict[str, float] = field(default_factory=dict)       # per-hectare (sowing) subsidies, by crop
    sale_subsidies_paid: dict[str, float] = field(default_factory=dict)  # per-ton (sale) subsidies, by crop
    taxes_collected: float = 0.0                                         # cumulative direct tax withheld from deals
    export_fees_collected: dict[str, float] = field(default_factory=dict)  # cumulative per-ton export fees, by crop

    # ------------------------------------------------------------------ direct tax
    def collect_direct_tax(self, gross_revenue: float) -> float:
        """Withhold the flat direct tax from a seller's gross revenue on a
        single deal and credit it to the budget. Returns the tax withheld so
        the caller can pay the seller the net amount (revenue − tax)."""
        tax = max(0.0, gross_revenue) * self.policy.direct_tax_rate
        if tax <= 0.0:
            return 0.0
        self.cash += tax
        self.taxes_collected += tax
        return tax

    # ------------------------------------------------------------------ trade policy
    def export_duty_rate(self, crop_id: str) -> float:
        return self.policy.export_duty_rate.get(crop_id, 0.0)

    def export_fee_per_ton(self, crop_id: str) -> float:
        return self.policy.export_fee_per_ton.get(crop_id, 0.0)

    def collect_export_fee(self, crop_id: str, quantity: float) -> float:
        """Charge the per-ton export fee on a shipment and credit it to the
        budget. Returns the fee amount so the caller can net it out of the
        exporter's revenue."""
        fee = self.export_fee_per_ton(crop_id) * max(0.0, quantity)
        if fee <= 0.0:
            return 0.0
        self.cash += fee
        self.export_fees_collected[crop_id] = self.export_fees_collected.get(crop_id, 0.0) + fee
        return fee

    # ------------------------------------------------------------------ market interventions
    def intervention_buy_request(self, crop_id: str, last_clearing_price: float | None,
                                 regional_traded_volume: float, lat: float, lon: float) -> DemandRequest | None:
        """A supportive purchase ("закупочные интервенции"): if a region's
        clearing price has fallen below the floor, the state goes shopping
        for grain there at a hard ceiling equal to the floor price itself —
        it is trying to put a floor under farm incomes, not chase the market
        up, so `price_ceiling = floor` (equivalent to flexibility = 0)."""
        floor = self.policy.intervention_floor_price.get(crop_id)
        if floor is None or last_clearing_price is None or last_clearing_price >= floor:
            return None
        quantity = max(regional_traded_volume, 1.0) * self.policy.intervention_volume_share
        # The floor is a fixed policy target (not market-referenced), so it is a
        # safe fundamental valuation for surplus-split pricing: the state bids
        # grain up toward the floor it is defending, but never past it.
        return DemandRequest(buyer_id=AGENT_ID, crop_id=crop_id, quantity=quantity,
                             price_ceiling=floor, lat=lat, lon=lon, valuation=floor)

    def intervention_sell_offer(self, crop_id: str, last_clearing_price: float | None,
                                regional_traded_volume: float, lat: float, lon: float) -> SupplyOffer | None:
        """A cooling sale ("товарные интервенции"): if a region's clearing
        price has risen above the ceiling, the state posts grain from its
        reserves there at exactly the ceiling price, capping how high buyers
        in that region end up paying once delivery cost is added on."""
        ceiling = self.policy.intervention_ceiling_price.get(crop_id)
        if ceiling is None or last_clearing_price is None or last_clearing_price <= ceiling:
            return None
        available = self.reserves.get(crop_id, 0.0)
        if available <= 1e-6:
            return None
        quantity = min(available, max(regional_traded_volume, 1.0) * self.policy.intervention_volume_share)
        return SupplyOffer(seller_id=AGENT_ID, crop_id=crop_id, quantity=quantity,
                           ask_price=ceiling, lat=lat, lon=lon)

    # ------------------------------------------------------------------ subsidies
    def pay_subsidies(self, farmers: list[Farmer], slot_key: int) -> None:
        """Per-hectare subsidy, paid at sowing on the area just planted in this
        window (`slot_key` is the sowing month — see `Farmer.maybe_plant`)."""
        for farmer in farmers:
            for crop_id, area in farmer.planted_area.get(slot_key, {}).items():
                rate = self.policy.subsidy_per_ha.get(crop_id)
                if not rate:
                    continue
                amount = rate * area
                farmer.cash += amount
                self.cash -= amount
                self.subsidies_paid[crop_id] = self.subsidies_paid.get(crop_id, 0.0) + amount

    def pay_sale_subsidy(self, farmer: Farmer, crop_id: str, quantity: float) -> float:
        """Per-ton subsidy, paid on each ton a farmer has just sold."""
        rate = self.policy.subsidy_per_ton.get(crop_id)
        if not rate or quantity <= 0.0:
            return 0.0
        amount = rate * quantity
        farmer.cash += amount
        self.cash -= amount
        self.sale_subsidies_paid[crop_id] = self.sale_subsidies_paid.get(crop_id, 0.0) + amount
        return amount

    # ------------------------------------------------------------------ settlement callbacks (acts as buyer/seller in auctions)
    def receive_goods(self, crop_id: str, quantity: float) -> None:
        self.reserves[crop_id] = self.reserves.get(crop_id, 0.0) + quantity

    def release_goods(self, crop_id: str, quantity: float) -> None:
        self.reserves[crop_id] = max(self.reserves.get(crop_id, 0.0) - quantity, 0.0)

    def pay(self, amount: float) -> None:
        self.cash -= amount

    def receive_payment(self, amount: float) -> None:
        self.cash += amount
