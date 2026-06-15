"""Whole-model invariants: grain mass balance and full-run reproducibility.

These are the kind of checks that make the model defensible — they assert the
simulation neither leaks nor conjures grain, and that a run is bit-for-bit
reproducible from its seed (a stated goal of the platform).
"""
from __future__ import annotations

import pytest

from app.simulation.engine import SimulationEngine
from app.simulation.scenario import ScenarioConfig, build_world


def _total_grain(world) -> float:
    """All grain held anywhere in the modelled economy (every agent's storage
    plus the state reserve) — the stock the monthly flows reconcile against."""
    return (
        sum(sum(f.storage.values()) for f in world.farmers)
        + sum(sum(b.storage.values()) for b in world.buyers)
        + sum(sum(e.storage.values()) for e in world.exporters)
        + sum(world.government.reserves.values())
    )


def test_grain_mass_balance_holds_every_month():
    """Conservation of grain: month over month the change in total stock equals
    harvested − consumed − spoiled − exported − dumped. Trades, forced sales and
    state interventions only move grain *within* the system, so they must net to
    zero here — any leak (or phantom creation) would break this."""
    world = build_world(ScenarioConfig(seed=9, num_farmers=50, num_buyers=18))
    engine = SimulationEngine(world)

    prev_total = _total_grain(world)
    for _ in range(48):
        rec = engine.step()
        current = _total_grain(world)

        # The record's own stock figure must agree with the live world.
        assert rec["total_grain_in_system"] == pytest.approx(current, abs=1e-3)

        net_flow = (
            rec["harvested_tons"]
            - rec["consumed_tons"]
            - rec["spoiled_tons"]
            - rec["exported_tons"]
            - rec["dumped_tons"]
        )
        tol = 1e-3 + 1e-6 * max(1.0, current, rec["harvested_tons"])
        assert abs((current - prev_total) - net_flow) < tol, (
            rec["year"], rec["month"], current - prev_total, net_flow
        )
        prev_total = current

    # Flows are physical: none can be negative, and no storage ever goes below 0.
    for f in world.farmers:
        assert all(v >= -1e-9 for v in f.storage.values())


def test_individual_flows_are_non_negative():
    world = build_world(ScenarioConfig(seed=4, num_farmers=30, num_buyers=12))
    for rec in SimulationEngine(world).run(36):
        for key in ("harvested_tons", "consumed_tons", "spoiled_tons", "exported_tons", "dumped_tons"):
            assert rec[key] >= -1e-9, (key, rec["year"], rec["month"])


def test_full_run_is_bit_identical_from_the_same_seed():
    """The entire monthly log (prices, volumes, fiscal totals, mass balance) is
    reproducible from the seed — not just the FX path. Two independently-built
    worlds with the same seed must produce identical step logs."""
    def run() -> list[dict]:
        world = build_world(ScenarioConfig(seed=123, num_farmers=40, num_buyers=15))
        return SimulationEngine(world).run(36)

    assert run() == run()


def test_different_seeds_diverge():
    """Sanity counter-check: a different seed yields a different run (so the
    reproducibility above is real determinism, not a frozen constant)."""
    a = SimulationEngine(build_world(ScenarioConfig(seed=1, num_farmers=40, num_buyers=15))).run(24)
    b = SimulationEngine(build_world(ScenarioConfig(seed=2, num_farmers=40, num_buyers=15))).run(24)
    assert a != b
