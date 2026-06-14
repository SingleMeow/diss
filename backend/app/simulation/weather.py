"""Hierarchical, spatially-correlated weather/yield shocks.

Real grain-price volatility is driven overwhelmingly by *correlated* weather: a
drought hits a whole region (or the whole country) at once, so individual farm
yields move together rather than independently. A model in which every farmer
draws an independent yield shock washes that out by the law of large numbers —
with N farmers the aggregate (national) yield shock shrinks like σ/√N, so no
meaningful supply shock ever reaches the market and prices stay artificially
placid.

This module restores aggregate shocks with a variance decomposition. Each
farmer's realised yield multiplier for a crop in a given harvest month is

    shock_i = max(1 + σ_crop · z_i, 0)

where the standardised draw `z_i` is built from three independent layers that
sum to unit variance:

    z_i = √w_nat · z_national + √w_reg · z_regional + √w_id · z_idiosyncratic
          ───────────────────  ───────────────────   ──────────────────────
          one draw shared by    one draw shared by    the farmer's own draw
          every farm in the     every farm in the
          country that month    same region

`w_nat + w_reg + w_id = 1`, so Var(z_i) = 1 and each farm's marginal yield
volatility is still exactly `σ_crop` (`yield_volatility`) — only the
*correlation structure* changes. Raising `w_nat`/`w_reg` concentrates the
variance into common factors, producing the synchronised good/bad harvest
years that drive real price cycles; setting them to 0 reproduces the old
fully-independent behaviour.

The national and regional draws are memoised per (crop, year, month) — and
per (region, crop, year, month) — so every farmer sharing a factor sees the
*same* number, and each factor is drawn from a key-seeded RNG so the whole
thing is reproducible from the scenario seed and independent of the order in
which farmers are processed.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field


def _stable_seed(*parts: object) -> int:
    """Deterministic 64-bit seed from arbitrary key parts.

    Uses BLAKE2b on the string form so the result is stable across processes
    (unlike `hash(tuple)`, which is randomised by PYTHONHASHSEED).
    """
    digest = hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8)
    return int.from_bytes(digest.digest(), "big")


@dataclass
class WeatherModel:
    """Draws and caches the common (national + regional) weather factors.

    On top of the stochastic draw it also carries *manual* yield multipliers —
    a nationwide one and per-region ones — that the live "scenario manipulation"
    API can set to stage a deliberate drought or bumper harvest. They default to
    1.0 (no effect) and multiply the realised harvest shock (see
    `Farmer.maybe_harvest`): e.g. `national_factor = 0.7` is a 30 % nationwide
    crop failure, `regional_factors["rostov"] = 1.2` a 20 % regional bumper.
    They persist until changed, so they apply to every harvest that falls while
    they are active — reset to 1.0 to clear.
    """

    seed: int
    national_weight: float = 0.40
    regional_weight: float = 0.35
    idiosyncratic_weight: float = 0.25

    # Manual overrides set via the live manipulation API (not part of the
    # stochastic process; default 1.0 = no manual bias).
    national_factor: float = 1.0
    regional_factors: dict[str, float] = field(default_factory=dict)

    _national: dict[tuple, float] = field(default_factory=dict, init=False, repr=False)
    _regional: dict[tuple, float] = field(default_factory=dict, init=False, repr=False)

    def manual_factor(self, region_id: str) -> float:
        """The combined deterministic yield multiplier a region's harvest is
        scaled by: the nationwide override times this region's own override
        (both 1.0 unless deliberately set through the manipulation API)."""
        return self.national_factor * self.regional_factors.get(region_id, 1.0)

    def national_z(self, crop_id: str, year: int, month: int) -> float:
        key = (crop_id, year, month)
        z = self._national.get(key)
        if z is None:
            z = random.Random(_stable_seed(self.seed, "nat", *key)).gauss(0.0, 1.0)
            self._national[key] = z
        return z

    def regional_z(self, region_id: str, crop_id: str, year: int, month: int) -> float:
        key = (region_id, crop_id, year, month)
        z = self._regional.get(key)
        if z is None:
            z = random.Random(_stable_seed(self.seed, "reg", *key)).gauss(0.0, 1.0)
            self._regional[key] = z
        return z

    def common_factor(self, region_id: str, crop_id: str, year: int, month: int) -> float:
        """The shared (non-idiosyncratic) standardised component of a harvest's
        yield shock: √w_nat·z_national + √w_reg·z_regional. The farmer adds its
        own √w_id·z_idiosyncratic on top (see `Farmer.maybe_harvest`)."""
        return (
            math.sqrt(self.national_weight) * self.national_z(crop_id, year, month)
            + math.sqrt(self.regional_weight) * self.regional_z(region_id, crop_id, year, month)
        )
