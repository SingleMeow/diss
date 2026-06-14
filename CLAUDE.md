# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Agent-based simulation of the Russian agri-food market (grain + oilseeds), monthly time step. Farmers, domestic buyers (mills/feed/processors/traders), exporters and the government trade via a **decentralised search-and-match market** (no central exchange: each buyer shops the whole country for the cheapest *delivered* grain). Backend = Python/FastAPI; frontend = React/TypeScript (Leaflet map, Recharts). This is a single-user local research tool / dissertation project (`diss`). Primary docs are in **Russian** (`README.md`); code and comments are English.

## Commands

Backend (Windows, venv lives at `backend/.venv`, Python 3.14):
```
cd backend
.venv/Scripts/python.exe -m pytest            # run tests (29, ~7s)
.venv/Scripts/python.exe -m pytest tests/test_engine.py -q
.venv/Scripts/python.exe -m uvicorn app.main:app --reload   # API on :8000, docs at /docs
```
Frontend (Node 24):
```
cd frontend
npx tsc --noEmit        # typecheck (vite build does NOT typecheck — always run this)
npx vite build          # production build
npm run dev             # dev server on :5173, proxies /api -> :8000
```
Always run `npx tsc --noEmit` after frontend edits — `vite build` uses esbuild and silently passes type errors.

## Architecture — the monthly step (the heart of the model)

`SimulationEngine.step()` in `backend/app/simulation/engine.py` runs a fixed phase order each month:

0. **Market dynamics** — advance stochastic global commodity-price shock + RUB/USD FX (`World.advance_market_dynamics`)
1. **Harvest** (before planting!) — crops reaching their harvest month yield tonnage (correlated weather shock); storage overflow becomes forced sales
2. **Planting** — sow crops whose sowing month is now, on *currently-free* land; pay per-ha sowing subsidies
3. **Spoilage + fixed costs** — storage loss; monthly share of annual fixed operating cost
4. **Price expectations** — Nerlove adaptive update (per market good)
5. **Collect offers/requests** — farmers post `SupplyOffer`s; buyers/exporters/government post `DemandRequest`s
6. **Search-and-match clearing** — `market/search_market.py`; buyers (random order) fill from cheapest delivered (ask + transport), surplus-split pricing
7. **Settlement** — cash/goods move; trades bucketed into one volume-weighted clearing price per (region, market good)
8. **Consumption + exports** — buyers process; exporters ship at world price net of duty/fee
9. **Lifecycle** (yearly, January) — close chronically insolvent farms *and* buyers; admit entrants by regional profitability
10. **Bookkeeping** — price/volume history, national averages, per-step log

`World` (`world.py`) is the mutable run state (registries, agents, RNGs, price history). `scenario.py:build_world(ScenarioConfig)` constructs a run from config + JSON data. `core/session.py` holds the single live run and bridges to the API; `core/database.py` persists config + step log to `simulation.db` (SQLite).

## Key domain concepts & invariants (READ before editing model logic)

- **Crop variety vs market good** (the most important distinction): a *variety* (`crop_id`, e.g. `winter_wheat`) is agronomy — sowing/harvest months, yield, cost, rotation. A *market good* (`crop.market_good`, e.g. `wheat`) is commerce — storage, selling, **price**, buyers, exporters, world price, duties. 18 varieties → 15 goods (wheat/barley/rapeseed each merge a winter + spring variety; rest 1:1). After harvest, grain is stored/sold **as the market good**; both varieties of a good feed **one price**. So: planting reads `expected_price[crop.market_good]`; storage/offers/clearing/buyers/exporters/world_prices/national_prices are all keyed by **market good**. `CropRegistry.market_goods()`, `.good_name()`, `.production_cost_per_ton(good)`, `.storage_loss_rate(good)` are the helpers.
- **Agro-climatic zones** (`geo.py:ClimateZone`, 8 zones: south, black_earth, volga, temperate, west_siberia, east_siberia, far_east, north). Defined in `agent_parameters.json:climate_zones` with `yield_multiplier` (scales every crop's yield in the zone — south 1.15 … north 0.55), `cost_multiplier` (scales sowing+fixed costs — lower in Siberia so low-yield zones stay viable, NOT all bankrupt), and `farmer_area_ha_range`. The farmer carries `zone_yield_multiplier`/`zone_cost_multiplier`; both feed harvest tonnage and planting margins. `regions.json:climate_zone` assigns each region; `crops.json:suitable_zones` gates where each crop grows. NOTE: the yield multiplier is **flat per zone** (scales all crops equally), so it changes cross-zone output and economics but NOT within-zone crop ranking — per-crop-per-zone overrides are a possible extension.
- **Calendar is per-crop, not per-zone** (`calendar.py`, `CropType.season/sowing_month/harvest_month`): spring crops sow ~May → harvest ~Sep/Oct same year; winter crops sow ~Sep → harvest ~Jul next year (`harvest_year_offset = 1`). Climate zone only gates `suitable_zones`. Winter+spring crops compete for the same land (available = total − standing pending harvests), so a farm growing both reaps twice/year. **Harvest runs before planting** so a Sept spring harvest frees land for Sept winter sowing.
- **Correlated weather** (`weather.py`): yield shock = √w_nat·z_national + √w_reg·z_regional + √w_id·z_idiosyncratic (weights sum to 1). Shared factors create aggregate (national/regional) supply shocks. Key-seeded RNG → reproducible & order-independent. Live override: `weather.national_factor` / `regional_factors` (manipulation API).
- **Stochastic world market** (`world.py`): seasonal base world price × global commodity AR(1) shock × FX(RUB/USD) AR(1). Both default-on; set volatilities to 0 for deterministic. A manually-pinned world price (`set_world_price`) bypasses the multipliers.
- **Agent placement** (`scenario.py:_apportion`, largest-remainder/Hamilton): farmers ∝ `region.sown_area_ha`, buyers ∝ `region.population`. Counts match config exactly; **zero-data regions get zero agents** (no farms in the North). Don't reintroduce a `max(1, round(...))` floor.
- **Crop mix / planting** (`farmer.py:_decide_planting`): each farm allocates free land by *greedy water-filling* on expected per-ha margin, capped by each crop's `max_area_share` (agronomic rotation limit, e.g. sunflower ≤ 0.25), then blended with last year's mix via `acreage_inertia` (Nerlovian partial adjustment). Crop menus are drawn weighted by `prevalence` (~ national area share), and the `staple_market_goods` (wheat) is forced onto every farm. Net effect: cereals dominate (~60 %), wheat ~37 %, sunflower ~14 % — matching Rosstat 2024. Tune the mix via `crops.json` `max_area_share`/`prevalence` and `agent_parameters.json:scenario_defaults.staple_market_goods`, NOT by hacking the allocation. (`crop_rotation_group_weight` is legacy/unused.)
- **Reproducibility streams**: `world.rng` (market shuffle), `world.weather` (key-seeded), `world._market_rng` (FX/price). Keep these separate so adding agents doesn't perturb weather/FX paths.

## Where to change what

- **Numeric model parameters** → `backend/app/data/agent_parameters.json` is the single source of truth (read at import by `scenario.py`). Editing it changes behaviour with no code change. Don't hardcode constants in code; thread them through `ScenarioConfig` / `World` fields from JSON.
- **Crops** → `data/crops.json` (variety: season, months, yield, cost, `market_good`, `market_good_name`, `suitable_zones`, `rotation_group`).
- **Regions** → `data/regions.json` (real Rosstat-grounded `sown_area_ha` + total `population`; North/Far-East have 0 sown area).
- **Export hubs** → `data/border_points.json` (handled crops are **market goods**, not varieties).
- **Government policy / live shocks** → `GovernmentPolicy` + the manipulation API (`session.intervene` / `InterveneRequest`). Per-good: world price, export factor, duty, fee, per-ton subsidy, interventions, weather. Per-variety: yield/volatility/sowing cost, per-ha subsidy.
- **API responses** are plain dicts assembled in `core/session.py` (not Pydantic). Request bodies are Pydantic in `schemas/simulation.py`. Frontend types mirror these in `frontend/src/api/types.ts` — keep them in sync.

## Gotchas

- **Cyrillic in the Bash tool**: the Git-Bash terminal renders UTF-8 Russian as mojibake (`������`). Files are fine. To inspect/edit data with Cyrillic names, use Python with `encoding="utf-8"` rather than `cat`/`grep` in bash. For bulk data edits prefer a throwaway Python script (delete it after) over hand-editing mojibake.
- **Frontend keys by market good, not variety**: price tabs (`StatsPanel`, `PriceCharts`) must label via `market_good` → `market_good_name` (derive a goods list from `crops`), NOT `crops.find(c => c.id === ...)`. `ParametersPanel` shows varieties; `ScenarioManipulationPanel` has two tables (per-good market/fiscal, per-variety agronomy).
- **Cold-start price seed**: a market good's first expectation seeds from the *cheapest* variety's production cost, so a higher-cost variety (spring wheat) can look briefly unprofitable in year 1 until real prices appear. Tests that need a good to clear should run ≥ 24 months (winter crops only reap the following July).
- **`month_index` epoch is hardcoded to 2024** (`world.py`): `start_year < 2024` breaks world-price series indexing. If supporting earlier years, store the epoch from `start_year`.

## Testing

`pytest` from `backend/`. Suites: `test_farmer` (planting/rotation/harvest/expectations), `test_engine` (full-run invariants, taxes/fees/subsidies/interventions), `test_logistics`, `test_manipulation` (live levers), `test_model_extensions` (weather/fixed-cost/FX/buyer-lifecycle). Unit tests construct crops via `CropRegistry.from_dicts` (defaults: spring, sow 5, harvest 9, `market_good = id`). No frontend tests.

## Known limitations / non-goals (don't "fix" without being asked)

- Crop **shares are national, not regional** — `max_area_share`/`prevalence` are global, so the aggregate mix matches Rosstat but a given region's mix may not (per-region crop structure is the next calibration step, needs Rosstat regional data).
- Price volatility is microstructure-driven, not supply-driven (2-month inventory buffers + demand contraction absorb harvest shocks) — a deliberate, documented finding.
- National-average price can include stale per-region prices when trading is sparse (harmless in the dense default scenario).
- Exporters/government accumulate cash one-way; transport cost leaves the modelled economy (not conserved).

## Repo status

Not yet under version control (`git init` recommended for a dissertation). `backend/simulation.db` is a generated SQLite artifact and can be deleted/regenerated.
