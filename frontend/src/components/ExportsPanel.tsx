import type { ExportRecord } from "../api/types";

function formatNumber(n: number): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(n);
}

interface Props {
  exports: ExportRecord[];
}

export default function ExportsPanel({ exports: records }: Props) {
  const recent = records.slice(-15).reverse();

  return (
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
  );
}
