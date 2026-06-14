"""Tests for the four model extensions: correlated weather, farmer fixed
costs, stochastic world-price/FX dynamics, and the buyer lifecycle."""
import random
import statistics as st

import pytest

from app.simulation.agents.base import CropMarketInfo, MarketSnapshot
from app.simulation.agents.farmer import Farmer
from app.simulation.crops import CropRegistry
from app.simulation.engine import SimulationEngine
from app.simulation.geo import ClimateZone
from app.simulation.scenario import ScenarioConfig, build_world
from app.simulation.weather import WeatherModel

CROPS = CropRegistry.from_dicts([
    {"id": "wheat", "name": "Wheat", "base_yield_t_per_ha": 3.0, "yield_volatility": 0.2,
     "sowing_cost_per_ha": 20000, "storage_loss_rate_per_month": 0.0, "suitable_zones": ["temperate"]},
])


def _harvest_one(farmer: Farmer, weather, seed_area=1000.0) -> float:
    """Plant + harvest a single wheat crop and return the realised tonnage."""
    snap = MarketSnapshot(year=2024, month=5, month_index=4,
                          crops={"wheat": CropMarketInfo(crop_id="wheat", last_price=20000)})
    farmer.update_price_expectations(snap, CROPS)
    # Force a known planted area so the only variation is the weather shock.
    from app.simulation.agents.farmer import PendingHarvest
    farmer.pending_harvests.append(PendingHarvest("wheat", seed_area, 2024, 9))
    farmer.maybe_harvest(2024, 9, CROPS, weather)
    return farmer.storage.get("wheat", 0.0)


# --------------------------------------------------------------------- A. weather
def test_correlated_weather_produces_larger_aggregate_shock_than_independent():
    """With shared national/regional factors, farms in a region move together,
    so the spread of *aggregate* regional harvests across many simulated years
    is materially larger than under fully-independent draws."""
    def aggregate_cv(weather: WeatherModel) -> float:
        totals = []
        for year in range(1, 400):
            farms = [Farmer(id=f"f{i}", name="f", region_id="r1", lat=55, lon=60,
                            climate_zone=ClimateZone.TEMPERATE, total_area_ha=1000,
                            storage_capacity_tons=1e9, allowed_crop_ids=["wheat"],
                            rng=random.Random(1000 * year + i)) for i in range(50)]
            snap = MarketSnapshot(year=year, month=5, month_index=4,
                                  crops={"wheat": CropMarketInfo(crop_id="wheat", last_price=20000)})
            from app.simulation.agents.farmer import PendingHarvest
            total = 0.0
            for f in farms:
                f.pending_harvests.append(PendingHarvest("wheat", 1000.0, year, 9))
                f.maybe_harvest(year, 9, CROPS, weather)
                total += f.storage.get("wheat", 0.0)
            totals.append(total)
        return st.pstdev(totals) / st.mean(totals)

    correlated = WeatherModel(seed=1, national_weight=0.4, regional_weight=0.35, idiosyncratic_weight=0.25)
    independent = WeatherModel(seed=1, national_weight=0.0, regional_weight=0.0, idiosyncratic_weight=1.0)

    cv_corr = aggregate_cv(correlated)
    cv_indep = aggregate_cv(independent)
    # Independent draws wash out (~vol/sqrt(N)); correlated draws keep a real
    # aggregate shock — expect it several times larger.
    assert cv_corr > cv_indep * 3


def test_weather_common_factor_is_shared_and_reproducible():
    w1 = WeatherModel(seed=7)
    w2 = WeatherModel(seed=7)
    a = w1.common_factor("r1", "wheat", 2024, 9)
    b = w2.common_factor("r1", "wheat", 2024, 9)
    assert a == b  # reproducible across instances
    # Different region OR different year gives a different draw (almost surely).
    assert w1.common_factor("r2", "wheat", 2024, 9) != a
    assert w1.common_factor("r1", "wheat", 2025, 9) != a


# --------------------------------------------------------------------- B. fixed costs
def test_fixed_cost_is_charged_monthly_on_whole_area():
    farmer = Farmer(id="f", name="f", region_id="r", lat=55, lon=60,
                    climate_zone=ClimateZone.TEMPERATE, total_area_ha=1000.0,
                    storage_capacity_tons=2000.0, allowed_crop_ids=["wheat"],
                    cash=0.0, fixed_cost_per_ha_per_year=12000.0)
    farmer.apply_fixed_costs()
    assert farmer.cash == pytest.approx(-1000.0 * 12000.0 / 12.0)  # one month's share


def test_fixed_costs_reduce_farm_profitability_and_cause_closures():
    """Turning on a realistic fixed cost should leave farms materially less
    cash-rich and produce more closures than the variable-cost-only model."""
    def run(fixed_cost: float):
        cfg = ScenarioConfig(seed=11, num_farmers=80, num_buyers=25,
                             farmer_fixed_cost_per_ha_per_year=fixed_cost)
        w = build_world(cfg)
        recs = SimulationEngine(w).run(60)
        total_cash = sum(f.cash for f in w.farmers)
        closures = sum(r["farms_closed"] for r in recs)
        return total_cash, closures

    cash_free, closures_free = run(0.0)
    cash_cost, closures_cost = run(9000.0)
    assert cash_cost < cash_free
    assert closures_cost >= closures_free


# --------------------------------------------------------------------- C. world dynamics
def test_world_price_dynamics_move_effective_price_but_are_reproducible():
    cfg = ScenarioConfig(seed=3, num_farmers=30, num_buyers=10)
    w = build_world(cfg)
    eng = SimulationEngine(w)
    fx_path = []
    for _ in range(36):
        eng.step()
        fx_path.append(w.fx_rate)
    # FX actually moved away from its base under non-zero volatility...
    assert st.pstdev(fx_path) > 0
    # ...and the whole path is reproducible from the seed.
    w2 = build_world(ScenarioConfig(seed=3, num_farmers=30, num_buyers=10))
    eng2 = SimulationEngine(w2)
    fx_path2 = []
    for _ in range(36):
        eng2.step()
        fx_path2.append(w2.fx_rate)
    assert fx_path == fx_path2


def test_zero_volatility_recovers_deterministic_world_prices():
    cfg = ScenarioConfig(seed=3, num_farmers=20, num_buyers=8,
                         fx_volatility=0.0, world_price_volatility=0.0)
    w = build_world(cfg)
    eng = SimulationEngine(w)
    for _ in range(24):
        eng.step()
    assert w.fx_rate == w.fx_base
    assert w.world_price_shock == 1.0


# --------------------------------------------------------------------- D. buyer lifecycle
def test_buyers_have_lifecycle_signals_and_can_close_and_enter():
    cfg = ScenarioConfig(seed=5, num_farmers=60, num_buyers=20)
    w = build_world(cfg)
    recs = SimulationEngine(w).run(60)
    # Profitability EMA is tracked for every buyer that has lived at least one
    # full month (buyers spawned in the very last January step have not yet had
    # an end-of-month update, so exclude that month's fresh entrants).
    tracked = sum(1 for b in w.buyers if b.cash_ema is not None)
    assert tracked >= len(w.buyers) - recs[-1]["buyers_spawned"]
    # Over five years the buyer demography changes (entries and/or exits recorded).
    assert sum(r["buyers_spawned"] for r in recs) + sum(r["buyers_closed"] for r in recs) > 0
    # Counts in the log stay consistent with the live list.
    assert recs[-1]["buyer_count"] == len(w.buyers)
