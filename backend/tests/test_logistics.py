from app.simulation.logistics import LogisticsConfig, TransportMode, cheapest_shipment


CFG = LogisticsConfig(
    road_cost_per_ton_km=4.5,
    rail_cost_per_ton_km=1.8,
    elevator_handling_fee_per_ton=350.0,
    rail_min_distance_km=250.0,
)


def test_short_haul_uses_road():
    shipment = cheapest_shipment(80, CFG)
    assert shipment.mode == TransportMode.ROAD
    assert shipment.cost_per_ton == 80 * CFG.road_cost_per_ton_km


def test_long_haul_switches_to_rail_when_cheaper():
    distance = 1500
    shipment = cheapest_shipment(distance, CFG)
    road_cost = distance * CFG.road_cost_per_ton_km
    rail_cost = distance * CFG.rail_cost_per_ton_km + 2 * CFG.elevator_handling_fee_per_ton
    assert rail_cost < road_cost
    assert shipment.mode == TransportMode.RAIL
    assert shipment.cost_per_ton == rail_cost


def test_rail_not_offered_below_threshold_even_if_cheaper_per_km():
    # At 200km rail's per-km rate would already beat road's, but the
    # elevator double-handling fee plus the distance threshold should
    # keep the shipment on the road.
    shipment = cheapest_shipment(200, CFG)
    assert shipment.mode == TransportMode.ROAD


def test_zero_distance_is_free():
    shipment = cheapest_shipment(0, CFG)
    assert shipment.cost_per_ton == 0.0
