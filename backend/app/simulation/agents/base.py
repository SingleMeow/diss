"""Shared agent primitives: market snapshots passed to agents each step."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CropMarketInfo:
    """What an agent can observe about a crop's market before deciding."""

    crop_id: str
    last_price: float | None          # last clearing price the agent's home region saw
    price_history: list[float] = field(default_factory=list)  # most recent N clearing prices (own region)
    national_avg_price: float | None = None  # volume-weighted average across all regions last step


@dataclass
class MarketSnapshot:
    """Read-only view of market state handed to every agent at decision time.

    Keeping this immutable and explicit (rather than letting agents reach into
    the engine/world directly) is what lets "decisions based on market data"
    stay deterministic, testable, and swappable for smarter strategies later.
    """

    year: int
    month: int
    month_index: int       # monotonic counter since epoch (used to index time-series data)
    crops: dict[str, CropMarketInfo]

    def info(self, crop_id: str) -> CropMarketInfo:
        return self.crops.get(crop_id, CropMarketInfo(crop_id=crop_id, last_price=None))
