"""Live "scenario manipulation" — mutating a running world mid-simulation."""
from app.core.session import SimulationSession
from app.schemas.simulation import InterveneRequest
from app.simulation.scenario import ScenarioConfig


def _started_session() -> SimulationSession:
    s = SimulationSession()
    s.start(ScenarioConfig(num_farmers=40, num_buyers=15, seed=3), {})
    s.step(6)
    return s


def test_levers_report_current_world_state():
    s = _started_session()
    lev = s.levers()

    assert lev["government_policy"]["direct_tax_rate"] == 0.06
    # crops in levers are the agronomic varieties (yield/cost editing)...
    assert {c["id"] for c in lev["crops"]} == set(s.world.crops.ids())
    # ...while world prices and export volumes are per market good (commerce).
    for good in s.world.crops.market_goods():
        assert good in lev["world_prices"]
        assert lev["export_volumes"][good]["factor"] == 1.0


def test_intervene_applies_fiscal_crop_and_export_shocks():
    s = _started_session()
    # Market-level levers (prices, export, subsidies, fees) key on the MARKET GOOD
    # ("wheat"); crop-parameter patches key on the agronomic variety ("winter_wheat").
    baseline_capacity = s.world.export_capacity_total("wheat")
    assert baseline_capacity > 0

    lev = s.intervene(InterveneRequest(
        government_policy={
            "direct_tax_rate": 0.12,
            "subsidy_per_ton": {"wheat": 400.0},
            "export_fee_per_ton": {"wheat": 250.0},
        },
        crops=[{"id": "winter_wheat", "base_yield_t_per_ha": 1.5, "sowing_cost_per_ha": 26000.0}],
        world_prices={"wheat": 31000.0},
        export_volume_factors={"wheat": 0.0},  # export embargo
    ))

    # Levers reflect the new state immediately.
    assert lev["government_policy"]["direct_tax_rate"] == 0.12
    assert lev["government_policy"]["subsidy_per_ton"]["wheat"] == 400.0
    assert lev["world_prices"]["wheat"] == 31000.0
    assert lev["export_volumes"]["wheat"]["factor"] == 0.0

    # The world itself is mutated (the crop patch hits the winter-wheat variety).
    wheat = s.world.crops.get("winter_wheat")
    assert wheat.base_yield_t_per_ha == 1.5
    assert wheat.sowing_cost_per_ha == 26000.0
    assert s.world.government.policy.direct_tax_rate == 0.12
    assert s.world.export_capacity_total("wheat") == 0.0
    assert s.world.world_price_for("wheat", s.world.month_index) == 31000.0

    # Stepping forward with the shocked parameters does not blow up, and the
    # embargo holds (no further wheat capacity reappears).
    s.step(6)
    assert s.world.export_capacity_total("wheat") == 0.0


def test_intervene_merges_dicts_without_clobbering_other_crops():
    s = _started_session()
    s.intervene(InterveneRequest(government_policy={"subsidy_per_ton": {"wheat": 100.0}}))
    s.intervene(InterveneRequest(government_policy={"subsidy_per_ton": {"corn": 200.0}}))

    subsidies = s.world.government.policy.subsidy_per_ton
    assert subsidies["wheat"] == 100.0   # first shock survives the second
    assert subsidies["corn"] == 200.0


def test_export_volume_factor_is_idempotent_not_compounding():
    s = _started_session()
    base = s.world.export_capacity_total("wheat")
    s.intervene(InterveneRequest(export_volume_factors={"wheat": 0.5}))
    s.intervene(InterveneRequest(export_volume_factors={"wheat": 0.5}))
    # Re-applying the same factor scales from baseline, not from the prior result.
    assert s.world.export_capacity_total("wheat") == base * 0.5


def test_weather_shock_sets_national_and_regional_yield_factors():
    s = _started_session()
    # Levers start with no weather override.
    lev0 = s.levers()
    assert lev0["weather"]["national_factor"] == 1.0
    assert lev0["weather"]["regional_factors"] == {}

    region = s.world.farmers[0].region_id
    lev = s.intervene(InterveneRequest(weather={
        "national_factor": 0.7,
        "regional_factors": {region: 0.5},
    }))
    # Reflected in levers and on the live weather model.
    assert lev["weather"]["national_factor"] == 0.7
    assert lev["weather"]["regional_factors"][region] == 0.5
    assert s.world.weather.manual_factor(region) == 0.7 * 0.5

    # Setting a regional factor back to 1.0 clears it from the override list.
    lev2 = s.intervene(InterveneRequest(weather={"regional_factors": {region: 1.0}}))
    assert region not in lev2["weather"]["regional_factors"]
    assert s.world.weather.manual_factor(region) == 0.7  # national override remains


def test_national_weather_shock_reduces_harvest_volume():
    def total_traded(national_factor: float) -> float:
        s = SimulationSession()
        s.start(ScenarioConfig(num_farmers=60, num_buyers=20, seed=7), {})
        if national_factor != 1.0:
            s.intervene(InterveneRequest(weather={"national_factor": national_factor}))
        recs = s.step(24)
        return sum(sum(r["traded_volumes"].values()) for r in recs)

    assert total_traded(0.6) < total_traded(1.0) * 0.85
