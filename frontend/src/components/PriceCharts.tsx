import { useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Crop, MarketHistory, Region, StepRecord } from "../api/types";

// Palette for user-selected region overlays (kept small & distinct on purpose).
const REGION_LINE_COLORS = [
  "#c1440e", "#3a6ea5", "#8e44ad", "#d4a017", "#16a085",
  "#e74c3c", "#2980b9", "#27ae60", "#d35400", "#7d3c98",
];

const fmt = (n: number) => new Intl.NumberFormat("ru-RU").format(Math.round(n));

interface Props {
  crops: Crop[];
  regions: Region[];
  history: StepRecord[];
  marketHistory: MarketHistory;
}

/** Linear-interpolated quantile of an ascending-sorted array. */
function quantile(sortedAsc: number[], q: number): number {
  const n = sortedAsc.length;
  if (n === 0) return NaN;
  if (n === 1) return sortedAsc[0];
  const pos = (n - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  return sortedAsc[base] + (rest * (sortedAsc[base + 1] - sortedAsc[base]) || 0);
}

export default function PriceCharts({ crops, regions, history, marketHistory }: Props) {
  const availableCropIds = useMemo(() => Object.keys(marketHistory), [marketHistory]);
  const [selectedCrop, setSelectedCrop] = useState<string>(availableCropIds[0] ?? "");
  const [highlighted, setHighlighted] = useState<string[]>([]);

  const cropId = availableCropIds.includes(selectedCrop) ? selectedCrop : (availableCropIds[0] ?? "");

  // Prices are keyed by MARKET GOOD (wheat), so resolve labels from the goods,
  // not the agronomic varieties (winter_wheat).
  const goodName = useMemo(() => {
    const map = new Map(crops.map((c) => [c.market_good, c.market_good_name]));
    return (id: string) => map.get(id) ?? id;
  }, [crops]);

  const regionName = useMemo(() => {
    const map = new Map(regions.map((r) => [r.id, r.name]));
    return (id: string) => map.get(id) ?? id;
  }, [regions]);

  const nationalSeries = useMemo(
    () =>
      history.map((rec, idx) => ({
        step: idx + 1,
        label: `${rec.year}-${String(rec.month).padStart(2, "0")}`,
        price: rec.national_prices[cropId] ?? null,
      })),
    [history, cropId],
  );

  // Cross-region dispersion over the *whole* run: build a full month axis from
  // the national history, place each region's (sparse) clearing prices onto it
  // by absolute month index, then per month compute the spread
  // (min/p25/median/p75/max) plus any individually highlighted regions.
  const { rows, regionIds } = useMemo(() => {
    const byRegion = marketHistory[cropId] ?? {};
    const ids = Object.keys(byRegion).sort((a, b) => regionName(a).localeCompare(regionName(b), "ru"));

    // Full timeline axis = every simulated month, same as the national chart.
    const monthIndexOf = (rec: StepRecord) => (rec.year - 2024) * 12 + (rec.month - 1);
    const posByMonthIndex = new Map<number, number>();
    history.forEach((rec, i) => posByMonthIndex.set(monthIndexOf(rec), i));

    const built: Record<string, number | string | number[] | null>[] = history.map((rec) => ({
      month: `${rec.year}-${String(rec.month).padStart(2, "0")}`,
    }));
    const valuesPerMonth: number[][] = history.map(() => []);

    for (const id of ids) {
      const { months, prices } = byRegion[id];
      const isHighlighted = highlighted.includes(id);
      for (let k = 0; k < months.length; k++) {
        const pos = posByMonthIndex.get(months[k]);
        if (pos === undefined) continue;
        const price = prices[k];
        if (price == null || !Number.isFinite(price)) continue;
        valuesPerMonth[pos].push(price);
        if (isHighlighted) built[pos][id] = price;
      }
    }

    built.forEach((row, i) => {
      const values = valuesPerMonth[i];
      if (values.length > 0) {
        values.sort((a, b) => a - b);
        row.band = [values[0], values[values.length - 1]];
        row.iqr = [quantile(values, 0.25), quantile(values, 0.75)];
        row.median = quantile(values, 0.5);
        row.count = values.length;
      } else {
        row.band = null;
        row.iqr = null;
        row.median = null;
        row.count = 0;
      }
    });
    return { rows: built, regionIds: ids };
  }, [marketHistory, cropId, history, highlighted, regionName]);

  if (availableCropIds.length === 0) {
    return (
      <div className="panel">
        <h2>Динамика цен</h2>
        <p className="muted">Сделок пока не было — данные появятся после нескольких шагов моделирования.</p>
      </div>
    );
  }

  const cropName = goodName(cropId);
  const activeHighlighted = highlighted.filter((id) => regionIds.includes(id));
  const unselected = regionIds.filter((id) => !activeHighlighted.includes(id));

  const tooltipFormatter = (value: number | number[], name: string) => {
    if (Array.isArray(value)) return [`${fmt(value[0])} – ${fmt(value[1])} ₽/т`, name];
    return [`${fmt(value)} ₽/т`, name];
  };

  return (
    <div className="panel">
      <h2>Динамика цен</h2>
      <div className="field" style={{ maxWidth: 260 }}>
        <label>Культура</label>
        <select value={cropId} onChange={(e) => setSelectedCrop(e.target.value)}>
          {availableCropIds.map((id) => (
            <option key={id} value={id}>
              {goodName(id)}
            </option>
          ))}
        </select>
      </div>

      <p className="muted">Средняя цена по стране — {cropName}, ₽/т</p>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={nationalSeries}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eceef1" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} minTickGap={24} />
          <YAxis tick={{ fontSize: 11 }} width={70} />
          <Tooltip formatter={(value: number) => fmt(value)} />
          <Line type="monotone" dataKey="price" name="Цена, ₽/т" stroke="#2c6e49" dot={false} strokeWidth={2} connectNulls />
        </LineChart>
      </ResponsiveContainer>

      {/* ---------------------------------------------- regional dispersion */}
      <div className="region-spread-head">
        <p className="muted" style={{ margin: "16px 0 0" }}>
          Разброс цен по регионам — {cropName}, ₽/т
          <span className="muted" style={{ marginLeft: 6 }}>
            ({regionIds.length} регионов)
          </span>
        </p>
      </div>

      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={rows}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eceef1" />
          <XAxis dataKey="month" tick={{ fontSize: 11 }} minTickGap={20} />
          <YAxis tick={{ fontSize: 11 }} width={70} />
          <Tooltip formatter={tooltipFormatter} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {/* min–max envelope (lightest), then the interquartile band, then median */}
          <Area
            type="monotone" dataKey="band" name="Мин–макс по регионам"
            stroke="none" fill="#2c6e49" fillOpacity={0.1} connectNulls isAnimationActive={false}
          />
          <Area
            type="monotone" dataKey="iqr" name="Межквартильный (25–75%)"
            stroke="none" fill="#2c6e49" fillOpacity={0.22} connectNulls isAnimationActive={false}
          />
          <Line
            type="monotone" dataKey="median" name="Медиана по регионам"
            stroke="#2c6e49" dot={false} strokeWidth={2.5} connectNulls isAnimationActive={false}
          />
          {activeHighlighted.map((id, idx) => (
            <Line
              key={id}
              type="monotone"
              dataKey={id}
              name={regionName(id)}
              stroke={REGION_LINE_COLORS[idx % REGION_LINE_COLORS.length]}
              dot={false}
              strokeWidth={1.75}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>

      {/* region picker */}
      <div className="region-picker">
        <select
          value=""
          onChange={(e) => {
            const id = e.target.value;
            if (id && !highlighted.includes(id)) setHighlighted((h) => [...h, id]);
          }}
        >
          <option value="">+ выделить регион…</option>
          {unselected.map((id) => (
            <option key={id} value={id}>{regionName(id)}</option>
          ))}
        </select>
        {activeHighlighted.length > 0 && (
          <button className="link-btn" onClick={() => setHighlighted([])}>Очистить</button>
        )}
        <div className="region-chips">
          {activeHighlighted.map((id, idx) => (
            <span
              key={id}
              className="region-chip"
              style={{ borderColor: REGION_LINE_COLORS[idx % REGION_LINE_COLORS.length] }}
            >
              <span
                className="region-chip-dot"
                style={{ background: REGION_LINE_COLORS[idx % REGION_LINE_COLORS.length] }}
              />
              {regionName(id)}
              <button onClick={() => setHighlighted((h) => h.filter((x) => x !== id))}>×</button>
            </span>
          ))}
        </div>
      </div>
      <p className="muted" style={{ marginTop: 8 }}>
        Заливка показывает разброс цен между регионами, линия — медиану. Выберите регионы из списка,
        чтобы отследить их по отдельности на фоне общего разброса.
      </p>
    </div>
  );
}
