"""Crop type definitions.

Crop parameters are fully data-driven (loaded from `data/crops.json` or
supplied through the API/UI), so the user can add, remove or tweak crops
without touching code — this is the "configurable number and parameters of
crops" requirement from the brief.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CropType:
    id: str
    name: str
    base_yield_t_per_ha: float       # tons harvested per hectare under average conditions
    yield_volatility: float          # stddev of the multiplicative weather shock (0.1 = +-10%)
    sowing_cost_per_ha: float        # RUB/ha spent at sowing time (seed, fuel, fertiliser...)
    storage_loss_rate_per_month: float  # fraction of stored stock lost to spoilage each month
    rotation_group: str              # crops sharing a group compete for the same land more strongly
    suitable_zones: tuple[str, ...]  # climate zones where the crop can be grown ("south", "temperate", "siberia")
    # Agronomic rotation limit: the maximum share of a single farm this crop may
    # occupy in any one sowing window. Sunflower's ~0.15 is the classic Russian
    # constraint (soil depletion / disease pressure forces long breaks); cereals
    # tolerate much higher shares. This is what keeps high-margin oilseeds from
    # taking over the whole farm and lets cereals dominate, as in real rotations.
    max_area_share: float = 1.0
    # Relative prevalence (~ national area share) used to weight a crop's chance
    # of appearing on a farm's crop menu, so major crops (wheat, sunflower,
    # barley) are near-universal where suitable and niche crops (mustard, millet)
    # are rare — shaping the aggregate sown-area structure toward reality.
    prevalence: float = 1.0
    # The fungible commodity this variety is sold AS. Several agronomically
    # distinct varieties (winter vs spring wheat) map to one market good
    # ("wheat") — buyers, exporters, prices and clearing are all per market
    # good, so winter and spring wheat trade in the *same* market at one price,
    # while keeping their separate yields, costs and sowing calendars.
    market_good: str = ""            # defaults to id (a variety that is its own market good)
    market_good_name: str = ""       # human label for the market good (defaults to name)
    # Sowing calendar — now a property of the crop, not the climate zone, so that
    # winter (озимые) and spring (яровые) crops can coexist on the same farm with
    # their real, different windows. A winter crop is sown in autumn and harvested
    # the *following* summer (harvest_month <= sowing_month); a spring crop is sown
    # and harvested within the same calendar year.
    season: str = "spring"           # "winter" | "spring"
    sowing_month: int = 5            # 1-12
    harvest_month: int = 9           # 1-12; if <= sowing_month the harvest is the following year

    def expected_yield(self, area_ha: float) -> float:
        return area_ha * self.base_yield_t_per_ha

    @property
    def harvest_year_offset(self) -> int:
        """Years between sowing and harvest: 1 for winter crops sown in autumn
        and reaped next summer, 0 for spring crops within one calendar year."""
        return 1 if self.harvest_month <= self.sowing_month else 0


class CropRegistry:
    def __init__(self, crops: list[CropType]):
        self._by_id = {c.id: c for c in crops}

    def __iter__(self):
        return iter(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, crop_id: str) -> CropType:
        return self._by_id[crop_id]

    def update(self, crop_id: str, **changes) -> CropType:
        """Replace a crop's parameters in place (CropType is frozen, so this
        swaps in a new instance). Only keys with a non-None value are applied;
        unknown keys are ignored. Used by the live "scenario manipulation" API
        to shock crop economics mid-run — farmers read these values fresh from
        the registry every step, so changes take effect on the next month."""
        crop = self._by_id[crop_id]
        applied = {k: v for k, v in changes.items()
                   if v is not None and hasattr(crop, k)}
        updated = replace(crop, **applied)
        self._by_id[crop_id] = updated
        return updated

    def ids(self) -> list[str]:
        return list(self._by_id.keys())

    def all(self) -> list[CropType]:
        return list(self._by_id.values())

    # ------------------------------------------------------------------ market goods
    def market_goods(self) -> list[str]:
        """Ordered unique list of the market goods these crop varieties sell as
        (e.g. winter_wheat + spring_wheat -> one entry "wheat")."""
        seen: dict[str, None] = {}
        for c in self._by_id.values():
            seen.setdefault(c.market_good, None)
        return list(seen.keys())

    def good_name(self, good: str) -> str:
        for c in self._by_id.values():
            if c.market_good == good:
                return c.market_good_name
        return good

    def varieties_for(self, good: str) -> list[CropType]:
        return [c for c in self._by_id.values() if c.market_good == good]

    def storage_loss_rate(self, good: str) -> float:
        """Monthly spoilage rate for a market good (taken from its varieties —
        winter/spring of the same grain store identically)."""
        rates = [c.storage_loss_rate_per_month for c in self.varieties_for(good)]
        return max(rates) if rates else 0.01

    def production_cost_per_ton(self, good: str) -> float:
        """Lowest per-ton production cost among the varieties of this market good
        — used only as a soft floor under a farmer's asking price."""
        costs = [c.sowing_cost_per_ha / max(c.base_yield_t_per_ha, 1e-6) for c in self.varieties_for(good)]
        return min(costs) if costs else 0.0

    @classmethod
    def from_dicts(cls, raw: list[dict]) -> "CropRegistry":
        crops = [
            CropType(
                id=c["id"],
                name=c["name"],
                base_yield_t_per_ha=c["base_yield_t_per_ha"],
                yield_volatility=c.get("yield_volatility", 0.15),
                sowing_cost_per_ha=c["sowing_cost_per_ha"],
                storage_loss_rate_per_month=c.get("storage_loss_rate_per_month", 0.01),
                rotation_group=c.get("rotation_group", c["id"]),
                suitable_zones=tuple(c.get("suitable_zones", ("south", "temperate", "siberia"))),
                max_area_share=c.get("max_area_share", 1.0),
                prevalence=c.get("prevalence", 1.0),
                season=c.get("season", "spring"),
                sowing_month=c.get("sowing_month", 5),
                harvest_month=c.get("harvest_month", 9),
                market_good=c.get("market_good") or c["id"],
                market_good_name=c.get("market_good_name") or c["name"],
            )
            for c in raw
        ]
        return cls(crops)
