import { useState } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import type { Agent, BuyerAgent, Crop, ExporterAgent, FarmerAgent, SimulationState, StepRecord } from "../api/types";

const MONTH_SHORT = ["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"];

function fmt(n: number, decimals = 0): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: decimals }).format(n);
}

function fmtM(n: number): string {
  if (Math.abs(n) >= 1e9) return `${fmt(n / 1e9, 1)} млрд`;
  if (Math.abs(n) >= 1e6) return `${fmt(n / 1e6, 1)} млн`;
  return fmt(n);
}

interface StatCardProps { label: string; value: string | number; sub?: string; color?: string }
function StatCard({ label, value, sub, color }: StatCardProps) {
  return (
    <div className="stat-card" style={color ? { borderColor: color + "55", background: color + "11" } : {}}>
      <div className="label">{label}</div>
      <div className="value" style={color ? { color } : {}}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

interface Props {
  state: SimulationState | null;
  history: StepRecord[];
  crops: Crop[];
  agents: Agent[];
}

export default function StatsPanel({ state, history, crops, agents }: Props) {
  // Prices/trades are keyed by MARKET GOOD (wheat), not agronomic variety
  // (winter_wheat) — derive the goods list and their labels from the crops.
  const goods = (() => {
    const m = new Map<string, string>();
    for (const c of crops) m.set(c.market_good, c.market_good_name);
    return [...m.entries()].map(([id, name]) => ({ id, name }));
  })();
  const cropIds = goods.length > 0
    ? goods.map(g => g.id)
    : history.length > 0 ? Object.keys(history[history.length - 1].national_prices) : [];
  const goodName = (id: string) => goods.find(g => g.id === id)?.name ?? id;

  const [selectedCrop, setSelectedCrop] = useState<string>(cropIds[0] ?? "wheat");

  const activeCrop = cropIds.includes(selectedCrop) ? selectedCrop : (cropIds[0] ?? "wheat");
  const cropLabel = goodName(activeCrop);

  const priceData = history
    .filter(h => h.national_prices[activeCrop] != null)
    .map(h => ({
      label: `${MONTH_SHORT[h.month - 1]} ${h.year}`,
      price: Math.round(h.national_prices[activeCrop]),
      volume: Math.round(h.traded_volumes[activeCrop] ?? 0),
    }));

  const farmers = agents.filter((a): a is FarmerAgent => a.type === "farmer");
  const buyers  = agents.filter((a): a is BuyerAgent  => a.type === "buyer");
  const exporters = agents.filter((a): a is ExporterAgent => a.type === "exporter");

  const avgFarmerCash = farmers.length
    ? farmers.reduce((s, f) => s + f.cash, 0) / farmers.length : 0;
  const negativeCash  = farmers.filter(f => f.cash < 0).length;
  const suspended     = farmers.filter(f => f.insolvent_months > 0).length;
  const avgFarmerFill = farmers.length
    ? farmers.reduce((s, f) => s + (f.storage_capacity_tons > 0 ? f.storage_tons / f.storage_capacity_tons : 0), 0) / farmers.length
    : 0;

  const sumRec = (r: Record<string, number>) => Object.values(r).reduce((a, b) => a + b, 0);

  const avgBuyerCash  = buyers.length  ? buyers.reduce((s, b) => s + b.cash, 0) / buyers.length : 0;
  const avgBuyerFill  = buyers.length
    ? buyers.reduce((s, b) => s + (b.storage_capacity_tons > 0 ? b.storage_tons / b.storage_capacity_tons : 0), 0) / buyers.length
    : 0;
  const buyerNegativeCash = buyers.filter(b => b.cash < 0).length;
  const buyerSuspended    = buyers.filter(b => b.insolvent_months > 0).length;

  // Price-elastic demand response: current throughput vs pre-shock baseline,
  // averaged across buyers (>100% = demand expanded, <100% = contracted).
  const buyersWithBaseline = buyers.filter(b => sumRec(b.monthly_consumption_baseline) > 0);
  const avgDemandResponse = buyersWithBaseline.length
    ? buyersWithBaseline.reduce((s, b) => s + sumRec(b.monthly_consumption) / sumRec(b.monthly_consumption_baseline), 0) / buyersWithBaseline.length
    : 1;

  const exporterTotalShipped = exporters.reduce((sum, e) =>
    sum + Object.values(e.shipped_total).reduce((s, v) => s + v, 0), 0);
  // Price-responsive export volume: current margin-flexed target vs contract.
  const expContract = exporters.reduce((s, e) => s + sumRec(e.monthly_capacity_tons), 0);
  const expTarget   = exporters.reduce((s, e) => s + sumRec(e.ship_target), 0);
  const exportResponse = expContract > 0 && expTarget > 0 ? expTarget / expContract : NaN;

  const totalClosed  = history.reduce((s, h) => s + (h.farms_closed  ?? 0), 0);
  const totalSpawned = history.reduce((s, h) => s + (h.farms_spawned ?? 0), 0);
  const totalBuyersClosed  = history.reduce((s, h) => s + (h.buyers_closed  ?? 0), 0);
  const totalBuyersSpawned = history.reduce((s, h) => s + (h.buyers_spawned ?? 0), 0);

  const lastStep = state?.last_step ?? null;
  const market = state?.market ?? null;
  const fxChangePct = market ? (market.fx_rate / market.fx_base - 1) * 100 : 0;

  if (!state) {
    return (
      <div className="panel">
        <h2>Состояние симуляции</h2>
        <p className="muted">Симуляция ещё не запущена. Настройте параметры и нажмите «Запустить».</p>
      </div>
    );
  }

  return (
    <>
      {/* ── Price chart ─────────────────────────────────────────────── */}
      <div className="panel">
        <div className="chart-header">
          <h2 style={{ margin: 0 }}>Средняя цена · {cropLabel}</h2>
          <select
            className="crop-select"
            value={activeCrop}
            onChange={e => setSelectedCrop(e.target.value)}
          >
            {cropIds.map(id => (
              <option key={id} value={id}>{goodName(id)}</option>
            ))}
          </select>
        </div>

        {priceData.length < 2 ? (
          <p className="muted" style={{ marginTop: 12 }}>Данных пока недостаточно для графика.</p>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={priceData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#2c6e49" stopOpacity={0.18} />
                  <stop offset="95%" stopColor="#2c6e49" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#e3e6ea" />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 11, fill: "#6b7280" }}
                interval={Math.max(0, Math.floor(priceData.length / 8) - 1)}
              />
              <YAxis
                tick={{ fontSize: 11, fill: "#6b7280" }}
                tickFormatter={v => `${fmt(v / 1000, 0)}к`}
                width={44}
              />
              <Tooltip
                formatter={(v: number) => [`${fmt(v)} ₽/т`, "Цена"]}
                labelStyle={{ color: "#1c1f26", fontWeight: 600 }}
                contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e6ea" }}
              />
              <Area
                type="monotone"
                dataKey="price"
                stroke="#2c6e49"
                strokeWidth={2}
                fill="url(#priceGrad)"
                dot={false}
                activeDot={{ r: 4, fill: "#2c6e49" }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ── Date / step header ──────────────────────────────────────── */}
      <div className="panel">
        <h2>
          {MONTH_SHORT[state.month - 1]} {state.year} · шаг #{state.step_index}
          {totalClosed + totalSpawned > 0 && (
            <span className="lifecycle-badge">
              ↓{totalClosed} ↑{totalSpawned} хозяйств
            </span>
          )}
          {totalBuyersClosed + totalBuyersSpawned > 0 && (
            <span className="lifecycle-badge">
              ↓{totalBuyersClosed} ↑{totalBuyersSpawned} покупателей
            </span>
          )}
        </h2>

        {/* Agent counts */}
        <div className="stat-grid">
          <StatCard label="Фермеров" value={state.counts.farmers} sub={suspended > 0 ? `${suspended} с долгом` : undefined} />
          <StatCard label="Покупателей" value={state.counts.buyers} />
          <StatCard label="Экспортёров" value={state.counts.exporters} />
          <StatCard label="Регионов" value={state.counts.regions} />
          <StatCard label="Бюджет гос-ва" value={fmtM(state.government.cash) + " ₽"}
            color={state.government.cash < 0 ? "#c0392b" : undefined} />
          {market && (
            <StatCard
              label="Курс ₽/$"
              value={fmt(market.fx_rate, 1)}
              sub={`${fxChangePct >= 0 ? "+" : ""}${fmt(fxChangePct, 1)}% к базе`}
              color={Math.abs(fxChangePct) > 5 ? "#e07b39" : undefined}
            />
          )}
          {market && (
            <StatCard
              label="Мир. цены (индекс)"
              value={fmt(market.world_price_shock * 100, 0)}
              sub="100 = база"
              color={Math.abs(market.world_price_shock - 1) > 0.1 ? "#e07b39" : undefined}
            />
          )}
        </div>

        {/* Farmer stats */}
        {farmers.length > 0 && (
          <>
            <div className="section-label">Фермеры</div>
            <div className="stat-grid">
              <StatCard label="Ср. касса" value={fmtM(avgFarmerCash) + " ₽"}
                color={avgFarmerCash < 0 ? "#c0392b" : undefined} />
              <StatCard label="В убытке" value={negativeCash}
                sub={`${Math.round(negativeCash / farmers.length * 100)}%`}
                color={negativeCash > 0 ? "#e07b39" : undefined} />
              <StatCard label="Ср. заполнение" value={`${Math.round(avgFarmerFill * 100)}%`} />
              {lastStep && <StatCard label="Зерно на складах" value={fmt(lastStep.total_farmer_storage) + " т"} />}
            </div>
          </>
        )}

        {/* Buyer stats */}
        {buyers.length > 0 && (
          <>
            <div className="section-label">Покупатели</div>
            <div className="stat-grid">
              <StatCard label="Ср. касса" value={fmtM(avgBuyerCash) + " ₽"}
                color={avgBuyerCash < 0 ? "#c0392b" : undefined} />
              <StatCard label="В убытке" value={buyerNegativeCash}
                sub={buyers.length ? `${Math.round(buyerNegativeCash / buyers.length * 100)}%` : undefined}
                color={buyerNegativeCash > 0 ? "#e07b39" : undefined} />
              <StatCard label="Ср. заполнение" value={`${Math.round(avgBuyerFill * 100)}%`} />
              <StatCard
                label="Спрос к базе"
                value={`${fmt(avgDemandResponse * 100)}%`}
                sub={avgDemandResponse < 1 ? "сжатие спроса" : avgDemandResponse > 1 ? "рост спроса" : "норма"}
                color={avgDemandResponse < 0.98 ? "#c0392b" : avgDemandResponse > 1.02 ? "#2c6e49" : undefined}
              />
              {buyerSuspended > 0 && <StatCard label="Приостановлено" value={buyerSuspended} color="#e07b39" />}
              {lastStep && <StatCard label="Зерно у покупателей" value={fmt(lastStep.total_buyer_storage) + " т"} />}
            </div>
          </>
        )}

        {/* Exporter stats */}
        {exporters.length > 0 && (
          <>
            <div className="section-label">Экспортёры</div>
            <div className="stat-grid">
              {exporters.map(e => (
                <StatCard
                  key={e.id}
                  label={e.name.length > 22 ? e.name.slice(0, 20) + "…" : e.name}
                  value={fmtM(e.cash) + " ₽"}
                  sub={`Отгружено: ${fmt(Object.values(e.shipped_total).reduce((s, v) => s + v, 0))} т`}
                  color={e.cash < 0 ? "#c0392b" : undefined}
                />
              ))}
              <StatCard label="Всего отгружено" value={fmt(exporterTotalShipped) + " т"} />
              {Number.isFinite(exportResponse) && (
                <StatCard
                  label="Вывоз к контракту"
                  value={`${fmt(exportResponse * 100)}%`}
                  sub={exportResponse < 1 ? "придерживают" : exportResponse > 1 ? "наращивают" : "норма"}
                  color={exportResponse < 0.98 ? "#c0392b" : exportResponse > 1.02 ? "#2c6e49" : undefined}
                />
              )}
            </div>
          </>
        )}

        {/* Current prices table */}
        {lastStep && Object.keys(lastStep.national_prices).length > 0 && (
          <>
            <div className="section-label">Национальные цены текущего месяца</div>
            <table>
              <thead>
                <tr>
                  <th>Культура</th>
                  <th>Цена, ₽/т</th>
                  <th>Объём торгов, т</th>
                  <th>Стоимость, ₽</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(lastStep.national_prices).map(([cropId, price]) => (
                  <tr key={cropId}>
                    <td>{goodName(cropId)}</td>
                    <td>{fmt(price)}</td>
                    <td>{fmt(lastStep.traded_volumes[cropId] ?? 0)}</td>
                    <td>{fmtM(lastStep.traded_value[cropId] ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </>
  );
}
