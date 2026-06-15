"""FastAPI entrypoint — wires up CORS, the database and the route modules."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_reference, routes_simulation
from app.core import database


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure the SQLite schema exists. (Replaces the deprecated
    # @app.on_event("startup") hook.)
    database.init_db()
    yield
    # Shutdown: nothing to tear down — connections are opened per-call.


app = FastAPI(
    title="Russian Agricultural Market — Agent-Based Simulation",
    lifespan=lifespan,
)

# This is a single-user local tool; the frontend runs on the Vite dev server
# (5173) / preview (4173). Pin to those local origins rather than "*": the
# wildcard is invalid together with allow_credentials=True (browsers reject it).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(routes_simulation.router)
app.include_router(routes_reference.router)
