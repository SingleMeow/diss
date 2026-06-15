"""Geography primitives: regions, climate zones, distance calculations."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class ClimateZone(str, Enum):
    """Agro-climatic zone of a region. Gates which crops can be grown there
    (`CropType.suitable_zones`) and carries a per-zone yield multiplier and
    typical farm-size range (see agent_parameters.json:climate_zones). The
    sowing calendar itself is per-crop now (see `calendar.py`), not per-zone."""

    SOUTH = "south"                # South & N. Caucasus — best yields (Krasnodar, Rostov, Stavropol)
    BLACK_EARTH = "black_earth"    # Central Black Earth & forest-steppe (Voronezh, Kursk, Tambov)
    VOLGA = "volga"                # Mid/Lower Volga & South Urals dry steppe (Saratov, Orenburg, Samara)
    TEMPERATE = "temperate"        # Central Non-Black-Earth & North-West (Moscow obl., Tver, Pskov)
    WEST_SIBERIA = "west_siberia"  # West Siberia grain belt (Altai, Omsk, Novosibirsk)
    EAST_SIBERIA = "east_siberia"  # East Siberia (Krasnoyarsk, Irkutsk, Buryatia, Zabaikalye)
    FAR_EAST = "far_east"          # Far East monsoon — soybean country (Amur, Primorye)
    NORTH = "north"                # Far North / permafrost — negligible cropping (Murmansk, Yakutia)


@dataclass(frozen=True)
class Region:
    """A federal subject (or aggregated cluster) used as a node on the map.

    Regions are the geographic home of agents and the unit prices/volumes are
    aggregated by. There is no regional exchange: trading is decentralised
    search-and-match (see `market/search_market.py`), so a region is a location
    and a reporting bucket, not a clearing venue.
    """

    id: str
    name: str
    lat: float
    lon: float
    climate_zone: ClimateZone
    sown_area_ha: float         # total sown area in the region (Rosstat) — drives farmer density/placement
    population: float           # total resident population — drives buyer (consumption/processing) density
    is_border: bool = False     # True for regions hosting an exporter hub (port/border crossing)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class RegionRegistry:
    """In-memory lookup of regions, loaded from `data/regions.json`."""

    def __init__(self, regions: list[Region]):
        self._by_id = {r.id: r for r in regions}

    def __iter__(self):
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, region_id: str) -> Region:
        return self._by_id[region_id]

    def all(self) -> list[Region]:
        return list(self._by_id.values())

    def distance_km(self, region_a: str, region_b: str) -> float:
        a, b = self._by_id[region_a], self._by_id[region_b]
        return haversine_km(a.lat, a.lon, b.lat, b.lon)

    @classmethod
    def from_dicts(cls, raw: list[dict]) -> "RegionRegistry":
        regions = [
            Region(
                id=r["id"],
                name=r["name"],
                lat=r["lat"],
                lon=r["lon"],
                climate_zone=ClimateZone(r["climate_zone"]),
                sown_area_ha=r["sown_area_ha"],
                population=r["population"],
                is_border=r.get("is_border", False),
            )
            for r in raw
        ]
        return cls(regions)
