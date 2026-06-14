import { useState } from "react";
import type { ScenarioConfigIn } from "../api/types";

interface Props {
  busy: boolean;
  started: boolean;
  autorun: boolean;
  autorunSpeed: number;
  onStart: (config: Partial<ScenarioConfigIn>) => void;
  onStep: (n: number) => void;
  onToggleAutorun: () => void;
  onAutorunSpeedChange: (ms: number) => void;
}

const DEFAULTS = {
  seed: 42,
  start_year: 2024,
  start_month: 1,
  num_farmers: 150,
  num_buyers: 45,
  farm_closure_months: 24,
  farm_entry_rate_max: 20,   // stored as % in UI, sent as fraction
  farm_entry_profitability_ha: 4000,
  buyer_closure_months: 24,
  buyer_entry_rate_max: 15,  // stored as % in UI, sent as fraction
  farmer_fixed_cost_per_ha_per_year: 9000,
  fx_base: 90,
  fx_volatility: 2.5,        // stored as % in UI, sent as fraction
  world_price_volatility: 4, // stored as % in UI, sent as fraction
};

const SPEED_OPTIONS = [
  { label: "Быстро", ms: 600 },
  { label: "Норма", ms: 1500 },
  { label: "Медленно", ms: 3000 },
];

export default function ControlPanel({
  busy, started, autorun, autorunSpeed,
  onStart, onStep, onToggleAutorun, onAutorunSpeedChange,
}: Props) {
  const [form, setForm] = useState(DEFAULTS);
  const [stepCount, setStepCount] = useState(1);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const update = (key: keyof typeof DEFAULTS) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = Number(e.target.value);
    setForm((prev) => ({ ...prev, [key]: Number.isFinite(value) ? value : prev[key] }));
  };

  const handleStart = () => {
    onStart({
      ...form,
      farm_entry_rate_max: form.farm_entry_rate_max / 100,
      buyer_entry_rate_max: form.buyer_entry_rate_max / 100,
      fx_volatility: form.fx_volatility / 100,
      world_price_volatility: form.world_price_volatility / 100,
    });
  };

  return (
    <div className="panel">
      <h2>Параметры сценария</h2>

      <div className="field-row">
        <label className="field">
          Сид ГСЧ
          <input type="number" value={form.seed} onChange={update("seed")} />
        </label>
        <label className="field">
          Год старта
          <input type="number" value={form.start_year} onChange={update("start_year")} />
        </label>
      </div>

      <div className="field-row">
        <label className="field">
          Месяц
          <input type="number" min={1} max={12} value={form.start_month} onChange={update("start_month")} />
        </label>
        <label className="field">
          Фермеров
          <input type="number" min={1} value={form.num_farmers} onChange={update("num_farmers")} />
        </label>
      </div>

      <div className="field-row">
        <label className="field">
          Покупателей
          <input type="number" min={1} value={form.num_buyers} onChange={update("num_buyers")} />
        </label>
      </div>

      <button
        className="collapse-toggle"
        onClick={() => setShowAdvanced(v => !v)}
        style={{ width: "100%", marginBottom: 8 }}
      >
        {showAdvanced ? "▲" : "▼"} Доп. параметры
      </button>

      {showAdvanced && (
        <>
          <div className="param-section-label">Жизненный цикл хозяйств</div>
          <div className="field-row">
            <label className="field">
              Закрытие (мес.)
              <input type="number" min={0} value={form.farm_closure_months} onChange={update("farm_closure_months")} />
            </label>
            <label className="field">
              Макс. вход (%)
              <input type="number" min={0} max={100} step={1} value={form.farm_entry_rate_max} onChange={update("farm_entry_rate_max")} />
            </label>
          </div>
          <div className="field-row">
            <label className="field">
              Порог входа (₽/га/мес.)
              <input type="number" min={0} value={form.farm_entry_profitability_ha} onChange={update("farm_entry_profitability_ha")} />
            </label>
            <label className="field">
              Пост. издержки (₽/га/год)
              <input type="number" min={0} value={form.farmer_fixed_cost_per_ha_per_year} onChange={update("farmer_fixed_cost_per_ha_per_year")} />
            </label>
          </div>

          <div className="param-section-label">Жизненный цикл покупателей</div>
          <div className="field-row">
            <label className="field">
              Закрытие (мес.)
              <input type="number" min={0} value={form.buyer_closure_months} onChange={update("buyer_closure_months")} />
            </label>
            <label className="field">
              Макс. вход (%)
              <input type="number" min={0} max={100} step={1} value={form.buyer_entry_rate_max} onChange={update("buyer_entry_rate_max")} />
            </label>
          </div>

          <div className="param-section-label">Мировой рынок (стохастика)</div>
          <div className="field-row">
            <label className="field">
              Курс ₽/$ (база)
              <input type="number" min={1} value={form.fx_base} onChange={update("fx_base")} />
            </label>
            <label className="field">
              Волат. курса (%/мес.)
              <input type="number" min={0} step={0.5} value={form.fx_volatility} onChange={update("fx_volatility")} />
            </label>
          </div>
          <div className="field-row">
            <label className="field">
              Волат. мир. цен (%/мес.)
              <input type="number" min={0} step={0.5} value={form.world_price_volatility} onChange={update("world_price_volatility")} />
            </label>
          </div>
        </>
      )}

      <div className="button-row" style={{ marginTop: 4 }}>
        <button onClick={handleStart} disabled={busy}>
          {started ? "Перезапустить" : "Запустить симуляцию"}
        </button>
      </div>

      {started && (
        <>
          <h2 style={{ marginTop: 20 }}>Автозапуск</h2>
          <div className="autorun-row">
            <button
              className={autorun ? "autorun-btn running" : "autorun-btn"}
              onClick={onToggleAutorun}
              disabled={busy && !autorun}
            >
              {autorun ? "⏹ Стоп" : "▶ Старт"}
            </button>
            <div className="speed-buttons">
              {SPEED_OPTIONS.map(opt => (
                <button
                  key={opt.ms}
                  className={`speed-btn${autorunSpeed === opt.ms ? " active" : ""}`}
                  onClick={() => onAutorunSpeedChange(opt.ms)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <h2 style={{ marginTop: 20 }}>Ручной шаг</h2>
          <div className="field-row">
            <label className="field">
              Месяцев
              <input
                type="number"
                min={1}
                max={240}
                value={stepCount}
                onChange={(e) => setStepCount(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
          </div>
          <div className="button-row">
            <button className="secondary" onClick={() => onStep(1)} disabled={busy}>
              +1 мес.
            </button>
            <button className="secondary" onClick={() => onStep(stepCount)} disabled={busy}>
              +{stepCount} мес.
            </button>
            <button className="secondary" onClick={() => onStep(12)} disabled={busy}>
              +1 год
            </button>
          </div>
        </>
      )}

      {!started && <p className="muted" style={{ marginTop: 10 }}>Запустите сценарий для начала моделирования.</p>}
    </div>
  );
}
