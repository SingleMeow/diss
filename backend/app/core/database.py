"""Minimal SQLite persistence: scenario configs and per-step run history.

The live simulation lives in memory (see `app.core.session`) — the database
only exists so a run's configuration and monthly results survive a server
restart and can be reviewed/replayed later. A single connection-per-call
pattern is used deliberately: this is a single-user local research tool, not
a concurrent web service, so simplicity wins over a connection pool.
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
    run_id INTEGER NOT NULL REFERENCES runs(id),
    step_index INTEGER NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (run_id, step_index)
);
"""


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


def create_run(config: dict) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO runs (config_json) VALUES (?)",
            (json.dumps(config, ensure_ascii=False),),
        )
        return int(cur.lastrowid)


def save_step(run_id: int, step_index: int, record: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO run_steps (run_id, step_index, year, month, data_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, step_index, record["year"], record["month"], json.dumps(record, ensure_ascii=False)),
        )


def load_run_steps(run_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT data_json FROM run_steps WHERE run_id = ? ORDER BY step_index", (run_id,)
        ).fetchall()
        return [json.loads(row[0]) for row in rows]


def list_runs() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at, config_json FROM runs ORDER BY id DESC"
        ).fetchall()
        return [{"id": row[0], "created_at": row[1], "config": json.loads(row[2])} for row in rows]
