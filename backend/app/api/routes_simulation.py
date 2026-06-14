"""Simulation control & inspection endpoints — start a run, step it forward,
and read back its state, agents, history, market series and export ledger."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.session import SimulationNotStarted, session
from app.simulation.agents.government import GovernmentPolicy
from app.simulation.logistics import LogisticsConfig
from app.simulation.scenario import ScenarioConfig
from app.schemas.simulation import InterveneRequest, ScenarioConfigIn, StepRequest

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


def _to_scenario_config(payload: ScenarioConfigIn) -> tuple[ScenarioConfig, dict]:
    config_dict = payload.model_dump(exclude_none=True)

    kwargs: dict = dict(
        seed=payload.seed,
        start_year=payload.start_year,
        start_month=payload.start_month,
        num_farmers=payload.num_farmers,
        num_buyers=payload.num_buyers,
        market_scale=payload.market_scale,
        buyer_max_debt=payload.buyer_max_debt,
        farmer_max_debt=payload.farmer_max_debt,
        farm_closure_months=payload.farm_closure_months,
        farm_entry_rate_max=payload.farm_entry_rate_max,
        farm_entry_profitability_ha=payload.farm_entry_profitability_ha,
        buyer_closure_months=payload.buyer_closure_months,
        buyer_entry_rate_max=payload.buyer_entry_rate_max,
        buyer_entry_profitability=payload.buyer_entry_profitability,
        farmer_fixed_cost_per_ha_per_year=payload.farmer_fixed_cost_per_ha_per_year,
        fx_base=payload.fx_base,
        fx_volatility=payload.fx_volatility,
        fx_reversion=payload.fx_reversion,
        world_price_volatility=payload.world_price_volatility,
        world_price_reversion=payload.world_price_reversion,
        crop_ids=payload.crop_ids,
        region_ids=payload.region_ids,
        crop_overrides=[c.model_dump() for c in payload.crop_overrides],
        extra_farmers=[f.model_dump(exclude_none=True) for f in payload.extra_farmers],
        extra_buyers=[b.model_dump(exclude_none=True) for b in payload.extra_buyers],
        extra_exporters=[e.model_dump(exclude_none=True) for e in payload.extra_exporters],
        logistics=LogisticsConfig(**payload.logistics.model_dump()),
        government_policy=GovernmentPolicy(**payload.government_policy.model_dump()),
    )
    if payload.world_prices is not None:
        kwargs["world_prices"] = payload.world_prices

    return ScenarioConfig(**kwargs), config_dict


@router.post("/start")
def start_simulation(payload: ScenarioConfigIn = ScenarioConfigIn()):
    config, config_dict = _to_scenario_config(payload)
    return session.start(config, config_dict)


@router.post("/step")
def step_simulation(payload: StepRequest = StepRequest()):
    try:
        records = session.step(payload.n)
    except SimulationNotStarted as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"steps": records, "state": session.state()}


def _guarded(fn):
    try:
        return fn()
    except SimulationNotStarted as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/levers")
def get_levers():
    """Current values of every live-manipulable parameter (Scenario-Manipulation tab)."""
    return _guarded(session.levers)


@router.post("/intervene")
def intervene(payload: InterveneRequest):
    """Apply a live shock to the running model; returns the refreshed levers."""
    return _guarded(lambda: session.intervene(payload))


@router.get("/state")
def get_state():
    return _guarded(session.state)


@router.get("/agents")
def get_agents():
    return _guarded(session.agents)


@router.get("/history")
def get_history():
    return _guarded(session.history)


@router.get("/market")
def get_market_history():
    return _guarded(session.market_history)


@router.get("/exports")
def get_exports():
    return _guarded(session.exports)
