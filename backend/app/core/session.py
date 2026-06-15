"""Holds the single live simulation run in memory and bridges it to the API.

This is a local, single-user research tool — one simulation runs at a time,
driven step-by-step from the UI. The session keeps the heavyweight `World`/
`SimulationEngine` objects in memory (they are plain Python objects full of
agent state that would be painful to round-trip through a database every
request) and persists only the configuration and the monthly summary log to
SQLite, via `app.core.database`, so a run's results survive a server restart
and can be reviewed later.
"""
from __future__ import annotations

from app.core import database as db
from app.simulation.engine import SimulationEngine
from app.simulation.scenario import ScenarioConfig, build_world
from app.simulation.world import World


class SimulationNotStarted(RuntimeError):
    pass


class SimulationSession:
    def __init__(self) -> None:
        self.world: World | None = None
        self.engine: SimulationEngine | None = None
        self.run_id: int | None = None
        self.step_index: int = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self, config: ScenarioConfig, config_dict: dict) -> dict:
        # Ensure the persistence schema exists even when the session is driven
        # directly (tests, scripts) rather than through the app's lifespan hook.
        # init_db is idempotent (CREATE TABLE IF NOT EXISTS).
        db.init_db()
        self.world = build_world(config)
        self.engine = SimulationEngine(self.world)
        self.run_id = db.create_run(config_dict)
        self.step_index = 0
        self._persist_snapshot()   # capture the initial (pre-step) state so a 0-step run still loads
        return self.state()

    def _require(self) -> World:
        if self.world is None or self.engine is None:
            raise SimulationNotStarted("Simulation has not been started yet — call POST /api/simulation/start first")
        return self.world

    # ------------------------------------------------------------------ stepping
    def step(self, n: int) -> list[dict]:
        self._require()
        records = []
        for _ in range(n):
            record = self.engine.step()
            if self.run_id is not None:
                db.save_step(self.run_id, self.step_index, record)
            self.step_index += 1
            records.append(record)
        # One rolling snapshot per batch (not per month): the export ledger and
        # price archive are cumulative, so the latest snapshot captures the whole
        # run's exports/market while keeping the write cost bounded on big steps.
        self._persist_snapshot()
        return records

    def _persist_snapshot(self) -> None:
        """Overwrite this run's rolling snapshot with the current derived views."""
        if self.run_id is None:
            return
        db.save_snapshot(
            self.run_id, self.step_index,
            state=self.state(), agents=self.agents(),
            exports=self.exports(), market=self.market_history(),
        )

    # ------------------------------------------------------------------ live manipulation
    def intervene(self, payload) -> dict:
        """Apply a live "shock" to the running world (taxes/fees/subsidies,
        crop economics, world prices and export volumes) and return the
        refreshed lever values. Takes effect from the next `step()` onward."""
        w = self._require()

        gp = payload.government_policy
        if gp is not None:
            policy = w.government.policy
            if gp.direct_tax_rate is not None:
                policy.direct_tax_rate = gp.direct_tax_rate
            if gp.intervention_volume_share is not None:
                policy.intervention_volume_share = gp.intervention_volume_share
            for field_name in ("export_fee_per_ton", "export_duty_rate", "subsidy_per_ha",
                               "subsidy_per_ton", "intervention_floor_price",
                               "intervention_ceiling_price"):
                incoming = getattr(gp, field_name)
                if incoming:
                    getattr(policy, field_name).update(incoming)

        for crop_patch in payload.crops:
            if crop_patch.id in w.crops.ids():
                w.crops.update(
                    crop_patch.id,
                    base_yield_t_per_ha=crop_patch.base_yield_t_per_ha,
                    yield_volatility=crop_patch.yield_volatility,
                    sowing_cost_per_ha=crop_patch.sowing_cost_per_ha,
                )

        if payload.world_prices:
            for crop_id, price in payload.world_prices.items():
                if crop_id in w.world_prices:
                    w.set_world_price(crop_id, price)

        if payload.export_volume_factors:
            for crop_id, factor in payload.export_volume_factors.items():
                w.set_export_volume_factor(crop_id, factor)

        if payload.weather is not None:
            if payload.weather.national_factor is not None:
                w.weather.national_factor = payload.weather.national_factor
            if payload.weather.regional_factors:
                for region_id, factor in payload.weather.regional_factors.items():
                    # A factor of 1.0 is "no shock" — drop it so resets keep the
                    # active-override list (and the levers view) clean.
                    if abs(factor - 1.0) < 1e-9:
                        w.weather.regional_factors.pop(region_id, None)
                    else:
                        w.weather.regional_factors[region_id] = factor

        return self.levers()

    def levers(self) -> dict:
        """Current values of every live-manipulable parameter — fuel for the
        Scenario-Manipulation tab so its tables open pre-filled with reality."""
        w = self._require()
        policy = w.government.policy
        month_idx = w.month_index
        return {
            "government_policy": {
                "direct_tax_rate": policy.direct_tax_rate,
                "intervention_volume_share": policy.intervention_volume_share,
                "export_fee_per_ton": dict(policy.export_fee_per_ton),
                "export_duty_rate": dict(policy.export_duty_rate),
                "subsidy_per_ha": dict(policy.subsidy_per_ha),
                "subsidy_per_ton": dict(policy.subsidy_per_ton),
                "intervention_floor_price": dict(policy.intervention_floor_price),
                "intervention_ceiling_price": dict(policy.intervention_ceiling_price),
            },
            "crops": [
                {
                    "id": c.id,
                    "name": c.name,
                    "market_good": c.market_good,
                    "market_good_name": c.market_good_name,
                    "base_yield_t_per_ha": c.base_yield_t_per_ha,
                    "yield_volatility": c.yield_volatility,
                    "sowing_cost_per_ha": c.sowing_cost_per_ha,
                }
                for c in w.crops
            ],
            "world_prices": {
                cid: w.world_price_for(cid, month_idx)
                for cid in w.world_prices
            },
            "export_volumes": {
                good: {
                    "factor": w.export_volume_factor(good),
                    "capacity_tons": w.export_capacity_total(good),
                }
                for good in w.crops.market_goods()
            },
            "weather": {
                "national_factor": w.weather.national_factor,
                "regional_factors": dict(w.weather.regional_factors),
            },
        }

    # ------------------------------------------------------------------ read views
    def state(self) -> dict:
        w = self._require()
        return {
            "run_id": self.run_id,
            "year": w.year,
            "month": w.month,
            "step_index": self.step_index,
            "counts": {
                "regions": len(w.regions),
                "crops": len(w.crops),
                "farmers": len(w.farmers),
                "buyers": len(w.buyers),
                "exporters": len(w.exporters),
            },
            "government": {
                "cash": w.government.cash,
                "reserves": dict(w.government.reserves),
                "subsidies_paid": dict(w.government.subsidies_paid),
                "sale_subsidies_paid": dict(w.government.sale_subsidies_paid),
                "taxes_collected": w.government.taxes_collected,
                "export_fees_collected": dict(w.government.export_fees_collected),
            },
            "market": {
                "fx_rate": w.fx_rate,
                "fx_base": w.fx_base,
                "world_price_shock": w.world_price_shock,
            },
            "last_step": w.step_log[-1] if w.step_log else None,
        }

    # ------------------------------------------------------------------ agent serialisers
    @staticmethod
    def _farmer_dict(f) -> dict:
        return {
            "id": f.id, "type": "farmer", "name": f.name,
            "lat": f.lat, "lon": f.lon, "region_id": f.region_id,
            "climate_zone": f.climate_zone.value,
            "total_area_ha": f.total_area_ha,
            "storage_capacity_tons": f.storage_capacity_tons,
            "storage_tons": round(sum(f.storage.values()), 2),
            "storage_by_crop": {k: round(v, 2) for k, v in f.storage.items() if v > 1e-6},
            "planted_area": {str(idx): dict(alloc) for idx, alloc in f.planted_area.items()},
            "allowed_crop_ids": f.allowed_crop_ids,
            "expected_price": {k: round(v, 2) for k, v in f.expected_price.items()},
            "cash": round(f.cash, 2),
            "insolvent_months": f.insolvent_months,
            "cash_ema": round(f.cash_ema, 2) if f.cash_ema is not None else None,
        }

    @staticmethod
    def _buyer_dict(b) -> dict:
        return {
            "id": b.id, "type": "buyer", "name": b.name,
            "lat": b.lat, "lon": b.lon, "region_id": b.region_id,
            "buyer_type": b.buyer_type.value,
            "monthly_consumption": {k: round(v, 2) for k, v in b.monthly_consumption.items()},
            # Pre-shock baseline throughput — current vs this shows the price-elastic demand response.
            "monthly_consumption_baseline": {k: round(v, 2) for k, v in b.monthly_consumption_baseline.items()},
            "demand_elasticity": b.demand_elasticity,
            "target_inventory_months": b.target_inventory_months,
            # Smoothed mean-reversion price anchor driving strategic inventory.
            "expected_price": {k: round(v, 2) for k, v in b.expected_price.items()},
            "storage_capacity_tons": b.storage_capacity_tons,
            "storage_tons": round(sum(b.storage.values()), 2),
            "storage_by_crop": {k: round(v, 2) for k, v in b.storage.items() if v > 1e-6},
            "flexibility": round(b.flexibility, 2),
            "cash": round(b.cash, 2),
            "insolvent_months": b.insolvent_months,
            "cash_ema": round(b.cash_ema, 2) if b.cash_ema is not None else None,
        }

    @staticmethod
    def _exporter_dict(e) -> dict:
        return {
            "id": e.id, "type": "exporter", "name": e.name,
            "lat": e.lat, "lon": e.lon, "region_id": e.region_id,
            "destination_country": e.destination_country,
            "handled_crop_ids": e.handled_crop_ids,
            "monthly_capacity_tons": e.monthly_capacity_tons,
            # Margin-flexed volume target this month (vs the contract capacity above
            # → the price-responsive export-volume response). Empty before step 1.
            "ship_target": {k: round(v, 2) for k, v in e._ship_target.items()},
            "volume_elasticity": e.volume_elasticity,
            "reference_margin": e.reference_margin,
            "storage_tons": round(sum(e.storage.values()), 2),
            "shipped_total": {k: round(v, 2) for k, v in e.shipped_total.items()},
            "flexibility": round(e.flexibility, 2),
            "cash": round(e.cash, 2),
        }

    def agents(self) -> list[dict]:
        w = self._require()
        return (
            [self._farmer_dict(f) for f in w.farmers]
            + [self._buyer_dict(b) for b in w.buyers]
            + [self._exporter_dict(e) for e in w.exporters]
        )

    # ------------------------------------------------------------------ live agent creation
    def add_agent(self, payload) -> dict:
        """Add one agent to the running world mid-scenario and return its
        serialised dict. Raises ValueError on a bad/duplicate spec (the route
        maps that to HTTP 400)."""
        import random

        from app.simulation.scenario import (
            _buyer_from_dict,
            _exporter_from_dict,
            _farmer_from_dict,
        )

        w = self._require()
        kind = payload.kind
        spec = getattr(payload, kind, None)
        if spec is None:
            raise ValueError(f"missing '{kind}' block for kind='{kind}'")
        d = spec.model_dump(exclude_none=True)

        if not d.get("id"):
            raise ValueError("agent id is required")
        if w.find_agent(d["id"]) is not None:
            raise ValueError(f"agent id '{d['id']}' already exists")
        try:
            w.regions.get(d["region_id"])
        except KeyError as exc:
            raise ValueError(f"unknown region_id '{d['region_id']}'") from exc

        if kind == "farmer":
            agent = _farmer_from_dict(d, w.regions, random.Random())
            w.farmers.append(agent)
            w.register_agent(agent)
            return self._farmer_dict(agent)
        if kind == "buyer":
            agent = _buyer_from_dict(d, w.regions)
            w.buyers.append(agent)
            w.register_agent(agent)
            return self._buyer_dict(agent)
        agent = _exporter_from_dict(d, w.regions)
        w.exporters.append(agent)
        w.register_agent(agent)
        return self._exporter_dict(agent)

    def history(self) -> list[dict]:
        return list(self._require().step_log)

    def market_history(self) -> dict[str, dict[str, dict[str, list[float]]]]:
        """{crop_id: {region_id: {"months": [...], "prices": [...]}}} — chart fuel.

        `months` are absolute month indices (monotonic since the run's epoch) so
        the frontend can align each region's (sparse) clearing prices onto the
        full simulation timeline, not just the most recent window.
        """
        w = self._require()
        out: dict[str, dict[str, dict[str, list[float]]]] = {}
        for (region_id, crop_id), archive in w.regional_price_archive().items():
            out.setdefault(crop_id, {})[region_id] = {
                "months": list(archive["months"]),
                "prices": list(archive["prices"]),
            }
        return out

    def exports(self) -> list[dict]:
        w = self._require()
        return [
            {
                "year": rec.year, "month": rec.month, "crop_id": rec.crop_id,
                "exporter_id": rec.exporter_id, "destination": rec.destination,
                "quantity_tons": round(rec.quantity_tons, 2),
                "revenue_rub": round(rec.revenue_rub, 2),
                "duty_rub": round(rec.duty_rub, 2),
                "fee_rub": round(rec.fee_rub, 2),
            }
            for rec in w.export_history
        ]

    # ------------------------------------------------------------------ stored-run browsing (DB-backed, no live world needed)
    @staticmethod
    def list_runs() -> list[dict]:
        """Catalogue of every persisted run (newest first) for the run-picker."""
        return db.list_runs()

    @staticmethod
    def load_run(run_id: int) -> dict:
        """Full reload of a stored run (config + monthly history + latest
        cumulative state/agents/exports/market). Raises KeyError if unknown."""
        run = db.get_run(run_id)
        if run is None:
            raise KeyError(f"run {run_id} not found")
        return run

    @staticmethod
    def delete_run(run_id: int) -> None:
        """Remove a stored run and all its rows. Raises KeyError if unknown."""
        if not db.delete_run(run_id):
            raise KeyError(f"run {run_id} not found")


session = SimulationSession()
