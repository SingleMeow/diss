import pytest

from app.simulation.engine import SimulationEngine
from app.simulation.scenario import ScenarioConfig, build_world


def test_engine_runs_a_full_year_without_errors_and_trades_clear():
    world = build_world(ScenarioConfig(num_farmers=40, num_buyers=15, seed=1))
    engine = SimulationEngine(world)

    records = engine.run(12)

    assert len(records) == 12
    assert len(world.step_log) == 12
    # Time advances monotonically and wraps the year correctly.
    assert (records[0]["year"], records[0]["month"]) == (2024, 2)
    assert (records[-1]["year"], records[-1]["month"]) == (2025, 1)

    # By the time the (single) harvest month has passed, some crop should have traded.
    traded_anything = any(r["traded_volumes"] for r in records)
    assert traded_anything


def test_storage_never_exceeds_capacity_and_cash_stays_finite():
    world = build_world(ScenarioConfig(num_farmers=30, num_buyers=10, seed=2))
    engine = SimulationEngine(world)
    engine.run(24)

    for farmer in world.farmers:
        assert sum(farmer.storage.values()) <= farmer.storage_capacity_tons + 1e-6
        assert farmer.cash == farmer.cash  # not NaN

    for buyer in world.buyers:
        assert sum(buyer.storage.values()) <= buyer.storage_capacity_tons + 1e-6


def test_exporters_eventually_ship_when_world_price_is_attractive():
    world = build_world(ScenarioConfig(num_farmers=60, num_buyers=20, seed=3))
    engine = SimulationEngine(world)
    engine.run(18)

    assert len(world.export_history) > 0
    assert sum(rec.quantity_tons for rec in world.export_history) > 0
    assert sum(rec.duty_rub for rec in world.export_history) >= 0


def test_direct_tax_is_collected_from_deals_and_can_be_disabled():
    # Default policy levies a 6% direct tax on every deal's seller revenue.
    taxed = build_world(ScenarioConfig(num_farmers=40, num_buyers=15, seed=5))
    assert taxed.government.policy.direct_tax_rate == 0.06
    SimulationEngine(taxed).run(18)
    assert taxed.government.taxes_collected > 0

    # Zeroing the rate mid-config turns the lever off entirely.
    cfg = ScenarioConfig(num_farmers=40, num_buyers=15, seed=5)
    cfg.government_policy.direct_tax_rate = 0.0
    untaxed = build_world(cfg)
    SimulationEngine(untaxed).run(18)
    assert untaxed.government.taxes_collected == 0.0


def test_per_ton_export_fee_is_charged_from_exporters():
    cfg = ScenarioConfig(num_farmers=60, num_buyers=20, seed=3)
    world = build_world(cfg)
    # Fees are a runtime policy parameter (not baked into crops): set one per market good.
    for good in world.crops.market_goods():
        world.government.policy.export_fee_per_ton[good] = 300.0

    SimulationEngine(world).run(18)

    assert sum(rec.quantity_tons for rec in world.export_history) > 0
    assert sum(world.government.export_fees_collected.values()) > 0
    # Each shipment records a fee of exactly fee_per_ton * tons shipped.
    for rec in world.export_history:
        assert rec.fee_rub == pytest.approx(300.0 * rec.quantity_tons)


def test_per_ton_sale_subsidy_pays_farmers_on_tons_sold():
    cfg = ScenarioConfig(num_farmers=60, num_buyers=20, seed=3)
    world = build_world(cfg)
    for crop in world.crops:
        world.government.policy.subsidy_per_ton[crop.id] = 100.0

    SimulationEngine(world).run(18)

    # Some grain trades over 18 months, so per-ton sale subsidies are paid out.
    assert sum(world.government.sale_subsidies_paid.values()) > 0


def test_government_intervention_buys_support_price_when_floor_is_set():
    high_floor_config = ScenarioConfig(num_farmers=40, num_buyers=15, seed=4)
    # Intervention is per MARKET GOOD ("wheat"), not per agronomic variety.
    high_floor_config.government_policy.intervention_floor_price = {"wheat": 1_000_000.0}  # absurdly high -> always triggers
    world = build_world(high_floor_config)
    engine = SimulationEngine(world)
    # 24 months so at least one full wheat marketing year clears (winter wheat
    # sown in autumn is only reaped the following July).
    engine.run(24)

    # With an absurd floor, the state should have accumulated wheat reserves
    # and spent cash propping up the price (cash goes negative as it buys).
    assert world.government.reserves.get("wheat", 0.0) > 0
    assert world.government.cash < 0
