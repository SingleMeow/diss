"""API-level tests: drive the FastAPI app through `TestClient` to lock the
HTTP contract (status codes + JSON response shapes the frontend depends on)
and exercise the stored-run browsing endpoints end to end.

Each test gets an isolated temporary SQLite database and a freshly-reset
session singleton, so runs created here never touch the real `simulation.db`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

SMALL = {"num_farmers": 20, "num_buyers": 8, "seed": 1}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point persistence at a throwaway DB *before* the app's lifespan runs
    # init_db, and reset the in-memory session so tests don't leak into each other.
    from app.core import database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")

    from app.core.session import session
    session.world = None
    session.engine = None
    session.run_id = None
    session.step_index = 0

    from app.main import app
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------- basics
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_reference_endpoints_return_catalogues(client):
    for path in ("/api/reference/regions", "/api/reference/crops", "/api/reference/border-points"):
        r = client.get(path)
        assert r.status_code == 200
        assert isinstance(r.json(), list) and len(r.json()) > 0


def test_step_before_start_is_409(client):
    # Fresh session (the fixture reset it) → stepping must be rejected, not 500.
    r = client.post("/api/simulation/step", json={"n": 1})
    assert r.status_code == 409


# --------------------------------------------------------------------- lifecycle
def test_start_then_step_advances_state(client):
    start = client.post("/api/simulation/start", json=SMALL)
    assert start.status_code == 200
    state = start.json()
    assert state["run_id"] is not None
    assert state["counts"]["farmers"] > 0
    assert state["step_index"] == 0

    stepped = client.post("/api/simulation/step", json={"n": 3})
    assert stepped.status_code == 200
    body = stepped.json()
    assert len(body["steps"]) == 3
    assert body["state"]["step_index"] == 3
    # The monthly record carries the new grain mass-balance fields.
    rec = body["steps"][-1]
    for key in ("national_prices", "harvested_tons", "consumed_tons",
                "spoiled_tons", "exported_tons", "dumped_tons", "total_grain_in_system"):
        assert key in rec


def test_agents_endpoint_shapes(client):
    client.post("/api/simulation/start", json=SMALL)
    client.post("/api/simulation/step", json={"n": 12})
    agents = client.get("/api/simulation/agents").json()
    assert isinstance(agents, list) and len(agents) > 0
    types = {a["type"] for a in agents}
    assert {"farmer", "buyer", "exporter"} <= types
    farmer = next(a for a in agents if a["type"] == "farmer")
    for key in ("id", "region_id", "lat", "lon", "cash", "total_area_ha", "storage_tons"):
        assert key in farmer


def test_levers_and_intervene_round_trip(client):
    client.post("/api/simulation/start", json=SMALL)
    levers = client.get("/api/simulation/levers").json()
    assert levers["government_policy"]["direct_tax_rate"] == pytest.approx(0.06)

    patched = client.post("/api/simulation/intervene", json={
        "government_policy": {"subsidy_per_ton": {"wheat": 250.0}},
    }).json()
    assert patched["government_policy"]["subsidy_per_ton"]["wheat"] == 250.0


# --------------------------------------------------------------------- stored runs
def test_runs_list_load_and_delete(client):
    # Run A: 3 months.
    run_a = client.post("/api/simulation/start", json=SMALL).json()["run_id"]
    client.post("/api/simulation/step", json={"n": 3})
    # Run B: 1 month (starting a new run does not disturb A's stored rows).
    run_b = client.post("/api/simulation/start", json={**SMALL, "seed": 2}).json()["run_id"]
    client.post("/api/simulation/step", json={"n": 1})
    assert run_a != run_b

    runs = client.get("/api/simulation/runs").json()
    ids = [r["id"] for r in runs]
    assert run_a in ids and run_b in ids
    assert ids == sorted(ids, reverse=True)             # newest first
    summary_a = next(r for r in runs if r["id"] == run_a)
    assert summary_a["step_count"] == 3

    loaded = client.get(f"/api/simulation/runs/{run_a}").json()
    assert loaded["run_id"] == run_a
    assert len(loaded["history"]) == 3                  # full monthly history persisted
    assert isinstance(loaded["agents"], list) and len(loaded["agents"]) > 0
    assert isinstance(loaded["exports"], list)
    assert isinstance(loaded["market"], dict)
    assert loaded["state"]["step_index"] == 3

    assert client.get("/api/simulation/runs/999999").status_code == 404

    assert client.delete(f"/api/simulation/runs/{run_a}").status_code == 200
    assert client.get(f"/api/simulation/runs/{run_a}").status_code == 404
    # Deleting A leaves B intact.
    assert client.get(f"/api/simulation/runs/{run_b}").status_code == 200
