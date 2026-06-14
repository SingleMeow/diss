"""Read-only reference data — the static catalogues the UI needs to build
scenario-configuration forms (region picker, crop editor, exporter list)."""
from __future__ import annotations

from fastapi import APIRouter

from app.simulation.scenario import load_json

router = APIRouter(prefix="/api/reference", tags=["reference"])


@router.get("/regions")
def get_regions():
    return load_json("regions.json")


@router.get("/crops")
def get_crops():
    return load_json("crops.json")


@router.get("/border-points")
def get_border_points():
    return load_json("border_points.json")
