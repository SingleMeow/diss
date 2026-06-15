import type { Crop, ScenarioConfigIn } from "../api/types";

const MONTH_NAMES = [
  "Январь","Февраль","Март","Апрель","Май","Июнь",
  "Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь",
];

function fmt(n: number, decimals = 0): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: decimals }).format(n);
}

interface RowProps { label: string; value: string | number }
function Row({ label, value }: RowProps) {
  return (
    <tr>
      <td className="param-label">{label}</td>
      <td className="param-value">{value}</td>
    </tr>
  );
}

interface SectionProps { title: string; children: React.ReactNode }
function Section({ title, children }: SectionProps) {
  return (
    <div className="param-section">
      <div className="param-section-title">{title}</div>
      <table className="param-table">
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

interface Props {
  config: Partial<ScenarioConfigIn> | null;
  crops: Crop[];
}

export default function ParametersPanel({ config, crops }: Props) {
  if (!config) {
    return (
      <div className="panel">
        <h2>Параметры модели</h2>
        <p className="muted">Симуляция ещё не запущена.</p>
      </div>
    );
  }

  const logistics = config.logistics ?? {};
  const cropIds = Object.keys(config.world_prices ?? {});
  const worldPrices = config.world_prices ?? null;

  // World prices are keyed by MARKET GOOD ("wheat"), not agronomic variety
  // ("winter_wheat"), so resolve labels via market_good — not crops.find(c.id),
  // which would never match a good key and fall back to the raw id.
  const goodName = (id: string) =>
    crops.find((c) => c.market_good === id)?.market_good_name ?? id;

  return (
    <div className="panel">
      <h2>Параметры модели</h2>

      <div className="params-grid">
        <Section title="Инициализация">
          <Row label="Семя ГСЧ (seed)" value={config.seed ?? 42} />
          <Row label="Дата начала" value={`${MONTH_NAMES[(config.start_month ?? 1) - 1]} ${config.start_year ?? 2024}`} />
          <Row label="Фермеров" value={config.num_farmers ?? 150} />
          <Row label="Покупателей" value={config.num_buyers ?? 45} />
        </Section>

        <Section title="Рыночное масштабирование">
          <Row
            label="Масштаб рынка (market_scale)"
            value={config.market_scale != null
              ? fmt(config.market_scale, 4)
              : `Авто (${fmt((config.num_farmers ?? 150) / 10000, 4)})`}
          />
          <Row label="Макс. долг фермера" value={`${fmt(config.farmer_max_debt ?? 20_000_000)} ₽`} />
          <Row label="Макс. долг покупателя" value={`${fmt(config.buyer_max_debt ?? 50_000_000)} ₽`} />
        </Section>

        <Section title="Жизненный цикл хозяйств">
          <Row
            label="Закрытие хозяйства после"
            value={(config.farm_closure_months ?? 24) === 0
              ? "Отключено"
              : `${config.farm_closure_months ?? 24} мес. неплатёжеспособности`}
          />
          <Row
            label="Макс. вероятность входа"
            value={(config.farm_entry_rate_max ?? 0.20) === 0
              ? "Отключено"
              : `${fmt((config.farm_entry_rate_max ?? 0.20) * 100, 0)}% / год`}
          />
          <Row
            label="Порог прибыльности для входа"
            value={`${fmt(config.farm_entry_profitability_ha ?? 4000)} ₽/га/мес.`}
          />
          <Row
            label="Пост. издержки (рента, амортизация)"
            value={(config.farmer_fixed_cost_per_ha_per_year ?? 9000) === 0
              ? "Отключены"
              : `${fmt(config.farmer_fixed_cost_per_ha_per_year ?? 9000)} ₽/га/год`}
          />
        </Section>

        <Section title="Жизненный цикл покупателей">
          <Row
            label="Закрытие покупателя после"
            value={(config.buyer_closure_months ?? 24) === 0
              ? "Отключено"
              : `${config.buyer_closure_months ?? 24} мес. неплатёжеспособности`}
          />
          <Row
            label="Макс. вероятность входа"
            value={(config.buyer_entry_rate_max ?? 0.15) === 0
              ? "Отключено"
              : `${fmt((config.buyer_entry_rate_max ?? 0.15) * 100, 0)}% / год`}
          />
          <Row
            label="Порог прибыльности для входа"
            value={`${fmt(config.buyer_entry_profitability ?? 2_000_000)} ₽/мес.`}
          />
        </Section>

        <Section title="Мировой рынок (стохастика)">
          <Row label="Курс ₽/$ (база)" value={fmt(config.fx_base ?? 90, 1)} />
          <Row
            label="Волатильность курса"
            value={(config.fx_volatility ?? 0.025) === 0
              ? "Фиксированный курс"
              : `${fmt((config.fx_volatility ?? 0.025) * 100, 1)}% / мес.`}
          />
          <Row
            label="Волатильность мировых цен"
            value={(config.world_price_volatility ?? 0.04) === 0
              ? "Детерминированные"
              : `${fmt((config.world_price_volatility ?? 0.04) * 100, 1)}% / мес.`}
          />
        </Section>

        <Section title="Логистика">
          <Row label="Автомобильный тариф" value={`${fmt(logistics.road_cost_per_ton_km ?? 4.5, 1)} ₽/(т·км)`} />
          <Row label="Железнодорожный тариф" value={`${fmt(logistics.rail_cost_per_ton_km ?? 1.8, 1)} ₽/(т·км)`} />
          <Row label="Элеваторный сбор" value={`${fmt(logistics.elevator_handling_fee_per_ton ?? 350)} ₽/т`} />
          <Row label="Мин. расстояние для ЖД" value={`${fmt(logistics.rail_min_distance_km ?? 250)} км`} />
        </Section>
      </div>

      {/* World prices */}
      <div className="param-section" style={{ marginTop: 16 }}>
        <div className="param-section-title">Мировые цены (FOB, ₽/т)</div>
        {!worldPrices || cropIds.length === 0 ? (
          <p className="muted" style={{ margin: "8px 0 0" }}>
            Используется стандартная сезонная синусоидальная модель (по умолчанию).
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="param-table" style={{ minWidth: 520 }}>
              <thead>
                <tr>
                  <th>Культура</th>
                  {["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"].map(m => (
                    <th key={m}>{m}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cropIds.map(cid => (
                  <tr key={cid}>
                    <td className="param-label">{goodName(cid)}</td>
                    {(worldPrices[cid] ?? []).map((p, i) => (
                      <td key={i} style={{ textAlign: "right", fontSize: 11 }}>{fmt(p)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Crop reference table */}
      {crops.length > 0 && (
        <div className="param-section" style={{ marginTop: 16 }}>
          <div className="param-section-title">Культуры</div>
          <div style={{ overflowX: "auto" }}>
            <table className="param-table">
              <thead>
                <tr>
                  <th>Культура</th>
                  <th>Сезон</th>
                  <th>Сев → уборка</th>
                  <th>Урожайность, т/га</th>
                  <th>Стоимость сева, ₽/га</th>
                  <th>Макс. доля поля</th>
                  <th>Потери хранения, %/мес.</th>
                  <th>Климат. зоны</th>
                  <th>Группа ротации</th>
                </tr>
              </thead>
              <tbody>
                {crops.map(c => (
                  <tr key={c.id}>
                    <td className="param-label">{c.name}</td>
                    <td>{c.season === "winter" ? "озимая" : "яровая"}</td>
                    <td style={{ fontSize: 11 }}>
                      {MONTH_NAMES[(c.sowing_month ?? 5) - 1]?.slice(0, 3)} → {MONTH_NAMES[(c.harvest_month ?? 9) - 1]?.slice(0, 3)}
                      {c.harvest_month <= c.sowing_month ? " (след. год)" : ""}
                    </td>
                    <td>{fmt(c.base_yield_t_per_ha, 1)}</td>
                    <td>{fmt(c.sowing_cost_per_ha)}</td>
                    <td>{c.max_area_share != null ? `${fmt(c.max_area_share * 100)}%` : "—"}</td>
                    <td>{fmt(c.storage_loss_rate_per_month * 100, 1)}</td>
                    <td style={{ fontSize: 11 }}>{c.suitable_zones.join(", ")}</td>
                    <td>{c.rotation_group ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
