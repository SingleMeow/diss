"""Transport-cost model: road vs. rail, plus elevator transshipment fees.

Real-world rule of thumb that we approximate here: road haulage has a low
fixed cost but a high per-km rate, so it wins on short hauls; rail has a much
lower per-km rate but requires loading/unloading through an elevator (a fixed
cost per tonne paid at both ends), so it only pays off past some distance
threshold. Each shipment automatically picks whichever mode is cheaper for
its distance — this is what "for rail and road the costs are different" means
operationally: the agents do not choose a mode, the network does, and the
chosen mode's cost (including elevator fees when rail is used) is what
shows up in the netback price.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TransportMode(str, Enum):
    ROAD = "road"
    RAIL = "rail"


@dataclass(frozen=True)
class LogisticsConfig:
    road_cost_per_ton_km: float = 6       # RUB / (ton * km)
    rail_cost_per_ton_km: float = 1.8       # RUB / (ton * km)
    elevator_handling_fee_per_ton: float = 350.0  # RUB / ton, charged at each elevator transshipment
    rail_min_distance_km: float = 250.0     # below this, rail is not offered (no siding/uneconomical)


@dataclass(frozen=True)
class Shipment:
    mode: TransportMode
    distance_km: float
    cost_per_ton: float


def cheapest_shipment(distance_km: float, config: LogisticsConfig) -> Shipment:
    """Return the lower-cost transport option for a given distance.

    Rail incurs the elevator fee twice (loading at origin, unloading at
    destination) and is only considered for hauls beyond `rail_min_distance_km`.
    """
    if distance_km <= 0:
        return Shipment(mode=TransportMode.ROAD, distance_km=0.0, cost_per_ton=0.0)

    road_cost = distance_km * config.road_cost_per_ton_km

    if distance_km >= config.rail_min_distance_km:
        rail_cost = (
            distance_km * config.rail_cost_per_ton_km
            + 2 * config.elevator_handling_fee_per_ton
        )
        if rail_cost < road_cost:
            return Shipment(mode=TransportMode.RAIL, distance_km=distance_km, cost_per_ton=rail_cost)

    return Shipment(mode=TransportMode.ROAD, distance_km=distance_km, cost_per_ton=road_cost)


def transport_cost_per_ton(distance_km: float, config: LogisticsConfig) -> float:
    return cheapest_shipment(distance_km, config).cost_per_ton
