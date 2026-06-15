import type { Agent, ExporterAgent, ExportRecord } from "../api/types";

function formatNumber(n: number): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(n);
}

const sumValues = (rec: Record<string, number>) => Object.values(rec).reduce((a, b) => a + b, 0);

interface Props {
  exports: ExportRecord[];
  agents: Agent[];
}

export default function ExportsPanel({ exports: records, agents }: Props) {
  const recent = records.slice(-15).reverse();
  const exporters = agents.filter((a): a is ExporterAgent => a.type === "exporter");

  return (
    <>
      {/* Price-responsive export volume: contract vs the margin-flexed target. */}
      {exporters.length > 0 && (
        <div className="panel">
          <h2>Эластичность вывоза (план vs контракт)</h2>
          <p className="muted" style={{ marginTop: -8 }}>
            Объём вывоза реагирует на экспортную маржу (мировая цена нетто пошлин/сборов минус внутренняя цена):
            высокая маржа тянет больше зерна из страны, низкая — придерживает.
          </p>
          <div style={{ overflowX: "auto" }}>
            <table className="param-table" style={{ minWidth: 560 }}>
              <thead>
                <tr>
                  <th>Экспортёр</th>
                  <th>Направление</th>
                  <th className="num">Контракт, т/мес.</th>
                  <th className="num">План вывоза, т/мес.</th>
                  <th className="num">К контракту</th>
                </tr>
              </thead>
              <tbody>
                {exporters.map((e) => {
                  const contract = sumValues(e.monthly_capacity_tons);
                  const target = sumValues(e.ship_target);
                  const pct = contract > 0 && target > 0 ? (target / contract) * 100 : NaN;
                  const color = pct > 102 ? "#2c6e49" : pct < 98 ? "#c0392b" : "var(--muted)";
                  return (
                    <tr key={e.id}>
                      <td className="param-label" style={{ color: "var(--ink)" }}>{e.name}</td>
                      <td>{e.destination_country}</td>
                      <td className="num">{formatNumber(contract)}</td>
                      <td className="num">{target > 0 ? formatNumber(target) : "—"}</td>
                      <td className="num" style={{ color, fontWeight: 600 }}>
                        {Number.isFinite(pct) ? `${formatNumber(pct)}%` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

    <div className="panel">
      <h2>Экспортные поставки (последние)</h2>
      {recent.length === 0 ? (
        <p className="muted">Экспортных сделок пока не было.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Период</th>
              <th>Культура</th>
              <th>Экспортёр</th>
              <th>Направление</th>
              <th>Объём, т</th>
              <th>Выручка, ₽</th>
              <th>Пошлина, ₽</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((rec, idx) => (
              <tr key={`${rec.exporter_id}-${rec.crop_id}-${rec.year}-${rec.month}-${idx}`}>
                <td>{rec.year}-{String(rec.month).padStart(2, "0")}</td>
                <td>{rec.crop_id}</td>
                <td>{rec.exporter_id}</td>
                <td>{rec.destination}</td>
                <td>{formatNumber(rec.quantity_tons)}</td>
                <td>{formatNumber(rec.revenue_rub)}</td>
                <td>{formatNumber(rec.duty_rub)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
    </>
  );
}
