"""SQLite persistence: scenario configs, per-step run history, and a rolling
full snapshot of each run's latest derived state.

The live simulation lives in memory (see `app.core.session`) — the database
exists so a run's configuration *and* its results survive a server restart and
can be browsed/reloaded later (see the `/api/simulation/runs` endpoints).

Three tables, all keyed by `run_id`:

* `runs`         — one row per run: the scenario config it was started from.
* `run_steps`    — one row per simulated month: the full monthly log record
                   (national prices, volumes, fiscal totals, grain mass balance…).
* `run_snapshots`— one row per run, overwritten as the run advances: the latest
                   cumulative derived views (state, every agent, the full export
                   ledger and the full regional price archive). Because the export
                   ledger and price archive are cumulative, this single latest
                   snapshot captures the entire run's exports/market history; the
                   month-by-month aggregates live in `run_steps`.

A single connection-per-call pattern is used deliberately: this is a single-user
local research tool, not a concurrent web service, so simplicity wins over a
connection pool.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "simulation.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    config_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_steps (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (run_id, step_index)
);
CREATE TABLE IF NOT EXISTS run_snapshots (
    run_id INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    state_json TEXT NOT NULL,
    agents_json TEXT NOT NULL,
    exports_json TEXT NOT NULL,
    market_json TEXT NOT NULL
);
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")  # honour ON DELETE CASCADE
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ------------------------------------------------------------------ writes
def create_run(config: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs (config_json) VALUES (?)",
            (_dumps(config),),
        )
        return int(cur.lastrowid)


def save_step(run_id: int, step_index: int, record: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO run_steps (run_id, step_index, year, month, data_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, step_index, record["year"], record["month"], _dumps(record)),
        )


def save_snapshot(run_id: int, step_index: int, *, state: dict, agents: list,
                  exports: list, market: dict) -> None:
    """Overwrite this run's single rolling snapshot with the latest derived
    views. Called once per step batch (not per month) so the write cost stays
    bounded even on a long `step(n)`."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO run_snapshots "
            "(run_id, step_index, updated_at, state_json, agents_json, exports_json, market_json) "
            "VALUES (?, ?, datetime('now'), ?, ?, ?, ?)",
            (run_id, step_index, _dumps(state), _dumps(agents), _dumps(exports), _dumps(market)),
        )


# ------------------------------------------------------------------ reads
def load_run_steps(run_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT data_json FROM run_steps WHERE run_id = ? ORDER BY step_index", (run_id,)
        ).fetchall()
        return [json.loads(row[0]) for row in rows]


def load_snapshot(run_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT step_index, updated_at, state_json, agents_json, exports_json, market_json "
            "FROM run_snapshots WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "step_index": row[0],
        "updated_at": row[1],
        "state": json.loads(row[2]),
        "agents": json.loads(row[3]),
        "exports": json.loads(row[4]),
        "market": json.loads(row[5]),
    }


def get_run(run_id: int) -> dict | None:
    """Full reload of a stored run: its config + monthly history + the latest
    cumulative snapshot (state/agents/exports/market). None if the run is unknown."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, created_at, config_json FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    if row is None:
        return None
    snapshot = load_snapshot(run_id) or {}
    return {
        "run_id": row[0],
        "created_at": row[1],
        "config": json.loads(row[2]),
        "history": load_run_steps(run_id),
        "step_index": snapshot.get("step_index", 0),
        "updated_at": snapshot.get("updated_at"),
        "state": snapshot.get("state"),
        "agents": snapshot.get("agents", []),
        "exports": snapshot.get("exports", []),
        "market": snapshot.get("market", {}),
    }


def list_runs() -> list[dict]:
    """Lightweight catalogue of every stored run for the run-picker, newest
    first: id, when it was created/last advanced, its config, how many months
    it has been stepped and where it currently stands."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.created_at, r.config_json,
                   (SELECT COUNT(*)        FROM run_steps s WHERE s.run_id = r.id) AS step_count,
                   (SELECT year  FROM run_steps s WHERE s.run_id = r.id ORDER BY step_index DESC LIMIT 1) AS last_year,
                   (SELECT month FROM run_steps s WHERE s.run_id = r.id ORDER BY step_index DESC LIMIT 1) AS last_month,
                   snap.updated_at
            FROM runs r
            LEFT JOIN run_snapshots snap ON snap.run_id = r.id
            ORDER BY r.id DESC
            """
        ).fetchall()
    return [
        {
            "id": row[0],
            "created_at": row[1],
            "config": json.loads(row[2]),
            "step_count": row[3] or 0,
            "last_year": row[4],
            "last_month": row[5],
            "updated_at": row[6],
        }
        for row in rows
    ]


def delete_run(run_id: int) -> bool:
    """Delete a run and all its child rows. Returns True if a run was removed."""
    with get_connection() as conn:
        conn.execute("DELETE FROM run_steps WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM run_snapshots WHERE run_id = ?", (run_id,))
        cur = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        return cur.rowcount > 0
