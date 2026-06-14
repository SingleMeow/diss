"""FastAPI entrypoint — wires up CORS, the database and the route modules."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_reference, routes_simulation
from app.core import database

app = FastAPI(title="Russian Agricultural Market — Agent-Based Simulation")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    database.init_db()


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(routes_simulation.router)
app.include_router(routes_reference.router)
