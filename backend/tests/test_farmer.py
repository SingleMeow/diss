import random

import pytest

from app.simulation.agents.base import CropMarketInfo, MarketSnapshot
from app.simulation.agents.farmer import Farmer
from app.simulation.crops import CropRegistry
from app.simulation.geo import ClimateZone

CROPS = CropRegistry.from_dicts([
    {"id": "wheat", "name": "Wheat", "base_yield_t_per_ha": 3.0, "yield_volatility": 0.0,
     "sowing_cost_per_ha": 20000, "storage_loss_rate_per_month": 0.01, "suitable_zones": ["temperate"]},
    {"id": "barley", "name": "Barley", "base_yield_t_per_ha": 2.5, "yield_volatility": 0.0,
     "sowing_cost_per_ha": 15000, "storage_loss_rate_per_month": 0.01, "suitable_zones": ["temperate"]},
])


def make_farmer(**overrides) -> Farmer:
    base = dict(
        id="f1", name="Test farm", region_id="r1", lat=55.0, lon=60.0,
        climate_zone=ClimateZone.TEMPERATE, total_area_ha=1000.0,
        storage_capacity_tons=2000.0, allowed_crop_ids=["wheat", "barley"],
        rng=random.Random(0),
    )
    base.update(overrides)
    return Farmer(**base)


def snapshot_with_prices(prices: dict[str, float]) -> MarketSnapshot:
    return MarketSnapshot(year=2024, month=5, month_index=4, crops={
        cid: CropMarketInfo(crop_id=cid, last_price=p) for cid, p in prices.items()
    })


def test_planting_only_happens_in_sowing_month():
    farmer = make_farmer()
    snap = snapshot_with_prices({"wheat": 15000, "barley": 12000})
    farmer.update_price_expectations(snap, CROPS)

    # Nothing is sown in April (the test crops are spring crops, sown in May).
    assert farmer.maybe_plant(2024, 4, snap, CROPS) is None
    assert farmer.planted_area == {}

    # maybe_plant now returns the sowing month as a slot key (5), not a cycle.
    slot = farmer.maybe_plant(2024, 5, snap, CROPS)
    assert slot == 5
    assert sum(farmer.planted_area[5].values()) <= farmer.total_area_ha + 1e-6
    assert any(v > 0 for v in farmer.planted_area[5].values())  # something was sown


# Same crops but with an agronomic cap on wheat (max 40 % of the farm).
CAPPED_CROPS = CropRegistry.from_dicts([
    {"id": "wheat", "name": "Wheat", "base_yield_t_per_ha": 3.0, "yield_volatility": 0.0,
     "sowing_cost_per_ha": 20000, "storage_loss_rate_per_month": 0.0,
     "suitable_zones": ["temperate"], "max_area_share": 0.4},
    {"id": "barley", "name": "Barley", "base_yield_t_per_ha": 2.5, "yield_volatility": 0.0,
     "sowing_cost_per_ha": 15000, "storage_loss_rate_per_month": 0.0,
     "suitable_zones": ["temperate"], "max_area_share": 1.0},
])


def test_max_area_share_caps_a_crop():
    """A crop never exceeds its agronomic max-area share, even when it is by far
    the most profitable (this is the rotation limit that keeps oilseeds from
    taking over and lets cereals dominate in aggregate)."""
    farmer = make_farmer()
    snap = snapshot_with_prices({"wheat": 50000, "barley": 12000})  # wheat hugely profitable
    farmer.update_price_expectations(snap, CAPPED_CROPS)
    farmer.maybe_plant(2024, 5, snap, CAPPED_CROPS)

    wheat_area = farmer.planted_area[5].get("wheat", 0.0)
    assert wheat_area <= 0.4 * farmer.total_area_ha + 1e-6   # cap binds
    assert wheat_area > 0.3 * farmer.total_area_ha            # and it fills toward the cap
    # The residual land goes to the other (high-cap) crop, not idle.
    assert farmer.planted_area[5].get("barley", 0.0) > 0.5 * farmer.total_area_ha


def test_acreage_inertia_gives_partial_adjustment():
    """With inertia ∈ (0,1), a sudden margin flip moves the sowing mix only
    partway toward the new optimum, not all the way (Nerlovian acreage
    adjustment), so the crop mix is sticky year to year."""
    farmer = make_farmer(acreage_inertia=0.5)
    high_wheat = snapshot_with_prices({"wheat": 50000, "barley": 8000})
    farmer.update_price_expectations(high_wheat, CROPS)
    farmer.maybe_plant(2024, 5, high_wheat, CROPS)   # year 1: wheat dominates
    farmer.maybe_harvest(2024, 9, CROPS)             # free the land
    y1_wheat = farmer.planted_area[5].get("wheat", 0.0)
    assert y1_wheat > 0.8 * farmer.total_area_ha

    # Barley now looks far better; a pure-margin farm would drop wheat to ~0.
    high_barley = snapshot_with_prices({"wheat": 8000, "barley": 50000})
    farmer.update_price_expectations(high_barley, CROPS)
    farmer.maybe_plant(2025, 5, high_barley, CROPS)
    y2_wheat = farmer.planted_area[5].get("wheat", 0.0)
    assert 0 < y2_wheat < y1_wheat                    # partial adjustment, not a full flip


def test_harvest_only_in_harvest_month_and_respects_storage_cap():
    farmer = make_farmer(total_area_ha=1000.0, storage_capacity_tons=500.0)
    snap = snapshot_with_prices({"wheat": 20000, "barley": 12000})
    farmer.update_price_expectations(snap, CROPS)
    farmer.maybe_plant(2024, 5, snap, CROPS)

    # Not yet harvest time.
    assert farmer.maybe_harvest(2024, 8, CROPS) == {}
    assert sum(farmer.storage.values()) == 0

    forced = farmer.maybe_harvest(2024, 9, CROPS)
    total_stock = sum(farmer.storage.values())
    # Storage never exceeds capacity; any overflow is reported as a forced sale.
    assert total_stock <= farmer.storage_capacity_tons + 1e-6
    if forced:
        assert total_stock == pytest.approx(farmer.storage_capacity_tons)


def test_update_price_expectations_moves_toward_observed_price():
    farmer = make_farmer()
    snap = snapshot_with_prices({"wheat": 30000})

    farmer.update_price_expectations(snap, CROPS)
    first = farmer.expected_price["wheat"]
    # Nerlove partial adjustment: the new expectation sits strictly between
    # the (production-cost-based) prior and the observed price...
    prior = farmer._production_cost_per_ton("wheat", CROPS) * (1.0 + farmer.target_margin)
    assert prior < first < 30000

    # ...and repeated exposure to the same observed price keeps nudging the
    # expectation closer to it without ever overshooting.
    farmer.update_price_expectations(snap, CROPS)
    second = farmer.expected_price["wheat"]
    assert first < second < 30000


def test_decide_sales_prices_from_expectations_and_storage_pressure():
    optimistic_snap = snapshot_with_prices({"wheat": 50000})
    pessimistic_snap = snapshot_with_prices({"wheat": 8000})

    farmer_optimistic = make_farmer(storage={"wheat": 200.0}, storage_capacity_tons=2000.0)
    farmer_pessimistic = make_farmer(storage={"wheat": 200.0}, storage_capacity_tons=2000.0)
    farmer_optimistic.update_price_expectations(optimistic_snap, CROPS)
    farmer_pessimistic.update_price_expectations(pessimistic_snap, CROPS)

    optimistic_offers = farmer_optimistic.decide_sales(optimistic_snap, CROPS)
    pessimistic_offers = farmer_pessimistic.decide_sales(pessimistic_snap, CROPS)

    # A farmer who has seen high prices forms a higher price expectation and
    # therefore asks more for its grain than one who has seen low prices.
    assert optimistic_offers[0].ask_price > pessimistic_offers[0].ask_price

    # A near-full warehouse pushes the asking price down (storage discount),
    # everything else being equal — the farmer needs to move grain to make
    # room for the next harvest.
    farmer_full = make_farmer(storage={"wheat": 1900.0}, storage_capacity_tons=2000.0)
    farmer_full.update_price_expectations(optimistic_snap, CROPS)
    full_offers = farmer_full.decide_sales(optimistic_snap, CROPS)
    assert full_offers[0].ask_price < optimistic_offers[0].ask_price

    # Offers are posted at the farmer's own coordinates (point-to-point search).
    assert optimistic_offers[0].lat == farmer_optimistic.lat
    assert optimistic_offers[0].lon == farmer_optimistic.lon
