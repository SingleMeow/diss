"""Sowing/harvest calendar — driven by the *crop*, not the climate zone.

The simulation advances in monthly steps. Each crop carries its own sowing and
harvest months (see `CropType.season` / `sowing_month` / `harvest_month`):

* **spring crops** (яровые) are sown in spring (~May) and harvested the same
  year (~September–October);
* **winter crops** (озимые) are sown in autumn (~September) and harvested the
  *following* summer (~July).

The climate zone no longer dictates the calendar — it only gates *which* crops
a farm may grow (`CropType.suitable_zones`): winter cereals don't survive the
Siberian winter, so they are simply absent from those farms' crop lists. A
farm that grows both a winter and a spring crop therefore reaps twice a year
(a July winter harvest and a September/October spring harvest) — which is how
the old "two cycles in the south" behaviour now emerges naturally, anywhere the
crop mix supports it.
"""
from __future__ import annotations

from app.simulation.crops import CropType


def is_sowing_month(crop: CropType, month: int) -> bool:
    """Whether `crop` is sown in this calendar month."""
    return crop.sowing_month == month


def is_harvest_month(crop: CropType, month: int) -> bool:
    """Whether `crop` is harvested in this calendar month."""
    return crop.harvest_month == month


def harvest_year(crop: CropType, sowing_year: int) -> int:
    """The calendar year a crop sown in `sowing_year` is harvested in."""
    return sowing_year + crop.harvest_year_offset
