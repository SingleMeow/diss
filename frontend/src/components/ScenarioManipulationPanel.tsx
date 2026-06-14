import { useCallback, useEffect, useState } from "react";
import { simulationApi } from "../api/client";
import type { InterveneRequest, Levers, Region } from "../api/types";

interface Props {
  running: boolean;
  regions: Region[];
  /** Called after a shock is applied, so the parent can refresh dependent views. */
  onApplied?: () => void;
}

/** Per-variety agronomy row (yield/cost are properties of the crop variety). */
interface VarietyDraft {
  name: string;
  good: string;                // market good this variety sells as
  base_yield_t_per_ha: number;
  yield_volatility: number;
  sowing_cost_per_ha: number;
  subsidy_per_ha: number;      // RUB/ha, paid at sowing on this variety's area
}

/** Per-market-good commercial row (price, trade policy — shared by all the
 * varieties that sell as this good, e.g. winter+spring wheat -> "wheat"). */
interface GoodDraft {
  name: string;
  export_duty_pct: number;     // shown as %, stored as fraction on the server
  export_fee_per_ton: number;  // RUB/ton
  subsidy_per_ton: number;     // RUB/ton sold
  world_price: number;         // RUB/ton (flat FOB level)
  export_factor: number;       // multiplier on baseline export volume
  capacity_tons: number;       // read-only: current effective monthly capacity
}

interface Draft {
  direct_tax_pct: number;      // shown as %, stored as fraction on the server
  byVariety: Record<string, VarietyDraft>;
  varietyOrder: string[];
  byGood: Record<string, GoodDraft>;
  goodOrder: string[];
  weather_national_pct: number;               // % of normal yield (100 = normal)
  weather_regional: Record<string, number>;   // region_id -> % of normal yield
}

function leversToDraft(lev: Levers): Draft {
  const gp = lev.government_policy;

  const byVariety: Record<string, VarietyDraft> = {};
  const varietyOrder: string[] = [];
  const byGood: Record<string, GoodDraft> = {};
  const goodOrder: string[] = [];

  for (const c of lev.crops) {
    varietyOrder.push(c.id);
    byVariety[c.id] = {
      name: c.name,
      good: c.market_good,
      base_yield_t_per_ha: c.base_yield_t_per_ha,
      yield_volatility: c.yield_volatility,
      sowing_cost_per_ha: c.sowing_cost_per_ha,
      subsidy_per_ha: gp.subsidy_per_ha[c.id] ?? 0,
    };
    if (!byGood[c.market_good]) {
      goodOrder.push(c.market_good);
      const vol = lev.export_volumes[c.market_good];
      byGood[c.market_good] = {
        name: c.market_good_name,
        export_duty_pct: (gp.export_duty_rate[c.market_good] ?? 0) * 100,
        export_fee_per_ton: gp.export_fee_per_ton[c.market_good] ?? 0,
        subsidy_per_ton: gp.subsidy_per_ton[c.market_good] ?? 0,
        world_price: Math.round(lev.world_prices[c.market_good] ?? 0),
        export_factor: vol?.factor ?? 1,
        capacity_tons: vol?.capacity_tons ?? 0,
      };
    }
  }

  const weather_regional: Record<string, number> = {};
  for (const [rid, factor] of Object.entries(lev.weather?.regional_factors ?? {})) {
    weather_regional[rid] = Math.round(factor * 100);
  }

  return {
    direct_tax_pct: gp.direct_tax_rate * 100,
    byVariety, varietyOrder, byGood, goodOrder,
    weather_national_pct: Math.round((lev.weather?.national_factor ?? 1) * 100),
    weather_regional,
  };
}

function fmt(n: number, decimals = 0): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: decimals }).format(n);
}

/** Compact numeric input for table cells. */
function NumCell({ value, onChange, step, min, width = 90 }: {
  value: number; onChange: (v: number) => void; step?: number; min?: number; width?: number;
}) {
  return (
    <input
      type="number"
      className="manip-input"
      style={{ width }}
      value={Number.isFinite(value) ? value : 0}
      step={step}
      min={min}
      onChange={(e) => {
        const v = Number(e.target.value);
        onChange(Number.isFinite(v) ? v : 0);
      }}
    />
  );
}

export default function ScenarioManipulationPanel({ running, regions, onApplied }: Props) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  // Regional weather-shock picker (which region + what yield level before adding).
  const [shockRegion, setShockRegion] = useState<string>("");
  const [shockPct, setShockPct] = useState<number>(70);

  const load = useCallback(async () => {
    try {
      setDraft(leversToDraft(await simulationApi.levers()));
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    if (running) void load();
    else setDraft(null);
  }, [running, load]);

  const setVariety = (id: string, key: keyof VarietyDraft, value: number) => {
    setDraft((d) => d && ({ ...d, byVariety: { ...d.byVariety, [id]: { ...d.byVariety[id], [key]: value } } }));
  };
  const setGood = (id: string, key: keyof GoodDraft, value: number) => {
    setDraft((d) => d && ({ ...d, byGood: { ...d.byGood, [id]: { ...d.byGood[id], [key]: value } } }));
  };

  const apply = useCallback(async (patch: InterveneRequest, label: string) => {
    setBusy(true);
    setError(null);
    try {
      const lev = await simulationApi.intervene(patch);
      setDraft(leversToDraft(lev));
      setFlash(`${label} применено`);
      setTimeout(() => setFlash(null), 2500);
      onApplied?.();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }, [onApplied]);

  if (!running) {
    return (
      <div className="panel">
        <h2>Манипуляция сценарием</h2>
        <p className="muted">Запустите симуляцию, чтобы менять правила «на ходу».</p>
      </div>
    );
  }
  if (!draft) {
    return (
      <div className="panel">
        <h2>Манипуляция сценарием</h2>
        <p className="muted">{error ?? "Загрузка текущих параметров…"}</p>
      </div>
    );
  }

  const vids = draft.varietyOrder;
  const gids = draft.goodOrder;

  // Market & fiscal levers operate per market good (one wheat market for winter+spring).
  const applyMarket = () => apply({
    government_policy: {
      direct_tax_rate: draft.direct_tax_pct / 100,
      export_duty_rate: Object.fromEntries(gids.map((id) => [id, draft.byGood[id].export_duty_pct / 100])),
      export_fee_per_ton: Object.fromEntries(gids.map((id) => [id, draft.byGood[id].export_fee_per_ton])),
      subsidy_per_ton: Object.fromEntries(gids.map((id) => [id, draft.byGood[id].subsidy_per_ton])),
    },
    world_prices: Object.fromEntries(gids.map((id) => [id, draft.byGood[id].world_price])),
    export_volume_factors: Object.fromEntries(gids.map((id) => [id, draft.byGood[id].export_factor])),
  }, "Рынок и налоги");

  // Agronomy + sowing subsidy operate per variety (winter vs spring differ).
  const applyAgronomy = () => apply({
    crops: vids.map((id) => ({
      id,
      base_yield_t_per_ha: draft.byVariety[id].base_yield_t_per_ha,
      yield_volatility: draft.byVariety[id].yield_volatility,
      sowing_cost_per_ha: draft.byVariety[id].sowing_cost_per_ha,
    })),
    government_policy: {
      subsidy_per_ha: Object.fromEntries(vids.map((id) => [id, draft.byVariety[id].subsidy_per_ha])),
    },
  }, "Агрономия и субсидии (га)");

  // ---- weather ----
  const farmRegions = regions
    .filter((r) => !r.is_border)
    .sort((a, b) => a.name.localeCompare(b.name, "ru"));
  const regionName = (id: string) => regions.find((r) => r.id === id)?.name ?? id;

  const applyNationalWeather = () => apply({
    weather: { national_factor: draft.weather_national_pct / 100 },
  }, "Погода (страна)");
  const addRegionalShock = () => {
    if (!shockRegion) return;
    apply({ weather: { regional_factors: { [shockRegion]: shockPct / 100 } } }, "Региональный шок");
  };
  const resetRegionalShock = (rid: string) =>
    apply({ weather: { regional_factors: { [rid]: 1.0 } } }, "Сброс регионального шока");

  return (
    <div className="panel">
      <h2>Манипуляция сценарием</h2>
      <p className="muted" style={{ marginTop: -8 }}>
        Изменения вступают в силу со следующего шага модели — инструмент для анализа шоков и резких изменений.
        Озимые и яровые сорта одной культуры торгуются на одном рынке (напр. «Пшеница»), поэтому цены, экспорт и
        пошлины задаются по рыночному товару, а урожайность и затраты — по сорту.
      </p>

      {error && <div className="error-banner" style={{ margin: "8px 0" }}>{error}</div>}
      {flash && <div className="manip-flash">✓ {flash}</div>}

      {/* ----------------------------------------------------------- 1. Market & fiscal (per market good) */}
      <div className="param-section" style={{ marginTop: 16 }}>
        <div className="param-section-title">1. Рынок и налоги (по рыночному товару)</div>
        <div className="field-row" style={{ maxWidth: 280, marginBottom: 10 }}>
          <label className="field">
            Прямой налог со сделки, %
            <input
              type="number" min={0} max={100} step={0.5}
              value={draft.direct_tax_pct}
              onChange={(e) => setDraft((d) => d && ({ ...d, direct_tax_pct: Number(e.target.value) || 0 }))}
            />
          </label>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="param-table manip-table" style={{ minWidth: 720 }}>
            <thead>
              <tr>
                <th>Товар</th>
                <th>Мировая цена, ₽/т</th>
                <th>Множитель экспорта, ×</th>
                <th>Мощность, т/мес.</th>
                <th>Эксп. пошлина, %</th>
                <th>Эксп. сбор, ₽/т</th>
                <th>Субсидия, ₽/т</th>
              </tr>
            </thead>
            <tbody>
              {gids.map((id) => {
                const g = draft.byGood[id];
                return (
                  <tr key={id}>
                    <td className="param-label">{g.name}</td>
                    <td><NumCell value={g.world_price} min={0} step={500} width={110} onChange={(v) => setGood(id, "world_price", v)} /></td>
                    <td><NumCell value={g.export_factor} min={0} step={0.1} onChange={(v) => setGood(id, "export_factor", v)} /></td>
                    <td style={{ textAlign: "right", color: "var(--muted)" }}>{fmt(g.capacity_tons)}</td>
                    <td><NumCell value={g.export_duty_pct} min={0} step={1} onChange={(v) => setGood(id, "export_duty_pct", v)} /></td>
                    <td><NumCell value={g.export_fee_per_ton} min={0} step={100} onChange={(v) => setGood(id, "export_fee_per_ton", v)} /></td>
                    <td><NumCell value={g.subsidy_per_ton} min={0} step={100} onChange={(v) => setGood(id, "subsidy_per_ton", v)} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="button-row" style={{ marginTop: 10 }}>
          <button onClick={applyMarket} disabled={busy}>Применить</button>
        </div>
      </div>

      {/* ----------------------------------------------------------- 2. Agronomy (per variety) */}
      <div className="param-section" style={{ marginTop: 16 }}>
        <div className="param-section-title">2. Сорта: урожайность, затраты и субсидия на гектар</div>
        <div style={{ overflowX: "auto" }}>
          <table className="param-table manip-table" style={{ minWidth: 640 }}>
            <thead>
              <tr>
                <th>Сорт</th>
                <th>Рынок</th>
                <th>Урожайность, т/га</th>
                <th>Волатильность</th>
                <th>Стоимость сева, ₽/га</th>
                <th>Субсидия, ₽/га</th>
              </tr>
            </thead>
            <tbody>
              {vids.map((id) => {
                const v = draft.byVariety[id];
                return (
                  <tr key={id}>
                    <td className="param-label">{v.name}</td>
                    <td style={{ color: "var(--muted)", fontSize: 11 }}>{draft.byGood[v.good]?.name ?? v.good}</td>
                    <td><NumCell value={v.base_yield_t_per_ha} min={0} step={0.1} onChange={(x) => setVariety(id, "base_yield_t_per_ha", x)} /></td>
                    <td><NumCell value={v.yield_volatility} min={0} step={0.01} onChange={(x) => setVariety(id, "yield_volatility", x)} /></td>
                    <td><NumCell value={v.sowing_cost_per_ha} min={0} step={500} width={110} onChange={(x) => setVariety(id, "sowing_cost_per_ha", x)} /></td>
                    <td><NumCell value={v.subsidy_per_ha} min={0} step={500} onChange={(x) => setVariety(id, "subsidy_per_ha", x)} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="button-row" style={{ marginTop: 10 }}>
          <button onClick={applyAgronomy} disabled={busy}>Применить</button>
        </div>
      </div>

      {/* ----------------------------------------------------------- 3. Weather shocks */}
      <div className="param-section" style={{ marginTop: 16 }}>
        <div className="param-section-title">3. Погодные шоки (урожайность)</div>
        <p className="muted" style={{ margin: "0 0 8px" }}>
          Множитель урожайности будущих уборок: 100% = норма, 70% = недород −30%,
          120% = рекордный урожай. Действует до сброса. Национальный множитель
          применяется ко всем регионам, региональный — поверх него к выбранному.
        </p>

        <div className="field-row" style={{ maxWidth: 320, marginBottom: 6 }}>
          <label className="field">
            Урожайность по стране, % от нормы
            <input
              type="number" min={0} step={5}
              value={draft.weather_national_pct}
              onChange={(e) => setDraft((d) => d && ({ ...d, weather_national_pct: Number(e.target.value) || 0 }))}
            />
          </label>
        </div>
        <div className="button-row" style={{ marginBottom: 14 }}>
          <button onClick={applyNationalWeather} disabled={busy}>Применить по стране</button>
        </div>

        <div className="field-row" style={{ alignItems: "flex-end", gap: 8, flexWrap: "wrap" }}>
          <label className="field" style={{ minWidth: 200 }}>
            Регион
            <select className="crop-select" value={shockRegion} onChange={(e) => setShockRegion(e.target.value)}>
              <option value="">— выберите регион —</option>
              {farmRegions.map((r) => (
                <option key={r.id} value={r.id}>{r.name}</option>
              ))}
            </select>
          </label>
          <label className="field" style={{ maxWidth: 150 }}>
            Урожайность, %
            <input type="number" min={0} step={5} value={shockPct} onChange={(e) => setShockPct(Number(e.target.value) || 0)} />
          </label>
          <button onClick={addRegionalShock} disabled={busy || !shockRegion} style={{ height: 36 }}>
            Применить к региону
          </button>
        </div>

        {Object.keys(draft.weather_regional).length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div className="param-section-label">Активные региональные шоки</div>
            <table className="param-table manip-table" style={{ maxWidth: 420 }}>
              <thead>
                <tr><th>Регион</th><th>Урожайность</th><th></th></tr>
              </thead>
              <tbody>
                {Object.entries(draft.weather_regional).map(([rid, pct]) => (
                  <tr key={rid}>
                    <td className="param-label">{regionName(rid)}</td>
                    <td style={{ color: pct < 100 ? "#c0392b" : pct > 100 ? "#2c6e49" : undefined }}>{pct}%</td>
                    <td>
                      <button className="secondary" disabled={busy} onClick={() => resetRegionalShock(rid)}>Сброс</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
