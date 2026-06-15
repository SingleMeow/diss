import { useMemo, useState } from "react";
import type {
  AddAgentRequest, Agent, BuyerAgent, Crop, ExporterAgent, FarmerAgent, Region,
} from "../api/types";

const TYPE_LABEL: Record<Agent["type"], string> = {
  farmer: "Фермер",
  buyer: "Покупатель",
  exporter: "Экспортёр",
};

const BUYER_TYPES = [
  { id: "flour_mill", label: "Мукомольный комбинат" },
  { id: "feed_producer", label: "Комбикормовый завод" },
  { id: "food_processor", label: "Пищевой комбинат" },
  { id: "trader", label: "Торговый дом / элеватор" },
];

const fmt = (n: number, d = 0) =>
  new Intl.NumberFormat("ru-RU", { maximumFractionDigits: d }).format(n);
const sumValues = (rec: Record<string, number>) => Object.values(rec).reduce((a, b) => a + b, 0);

type SortKey = "name" | "type" | "region" | "cash" | "storage" | "size";

function agentSize(a: Agent): number {
  if (a.type === "farmer") return a.total_area_ha;
  if (a.type === "buyer") return sumValues(a.monthly_consumption);
  return sumValues(a.monthly_capacity_tons);
}

interface Props {
  agents: Agent[];
  regions: Region[];
  crops: Crop[];
  running: boolean;
  busy: boolean;
  onAdd: (req: AddAgentRequest) => Promise<void>;
}

export default function AgentsPanel({ agents, regions, crops, running, busy, onAdd }: Props) {
  const [typeFilter, setTypeFilter] = useState<Agent["type"] | "all">("all");
  const [regionFilter, setRegionFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("cash");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [showAdd, setShowAdd] = useState(false);

  const regionName = useMemo(() => {
    const m = new Map(regions.map((r) => [r.id, r.name]));
    return (id: string) => m.get(id) ?? id;
  }, [regions]);

  // Regions that actually have agents — for the filter dropdown.
  const regionsWithAgents = useMemo(() => {
    const ids = new Set(agents.map((a) => a.region_id));
    return regions.filter((r) => ids.has(r.id)).sort((a, b) => a.name.localeCompare(b.name, "ru"));
  }, [agents, regions]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = agents.filter((a) => {
      if (typeFilter !== "all" && a.type !== typeFilter) return false;
      if (regionFilter !== "all" && a.region_id !== regionFilter) return false;
      if (q && !a.name.toLowerCase().includes(q) && !a.id.toLowerCase().includes(q)) return false;
      return true;
    });
    const dir = sortDir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "name": cmp = a.name.localeCompare(b.name, "ru"); break;
        case "type": cmp = a.type.localeCompare(b.type); break;
        case "region": cmp = regionName(a.region_id).localeCompare(regionName(b.region_id), "ru"); break;
        case "cash": cmp = a.cash - b.cash; break;
        case "storage": cmp = a.storage_tons - b.storage_tons; break;
        case "size": cmp = agentSize(a) - agentSize(b); break;
      }
      return cmp * dir;
    });
    return rows;
  }, [agents, typeFilter, regionFilter, search, sortKey, sortDir, regionName]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir(key === "name" || key === "region" || key === "type" ? "asc" : "desc"); }
  };
  const arrow = (key: SortKey) => (sortKey === key ? (sortDir === "asc" ? " ▲" : " ▼") : "");

  if (!running) {
    return (
      <div className="panel">
        <h2>Агенты</h2>
        <p className="muted">Запустите симуляцию, чтобы просматривать и добавлять агентов.</p>
      </div>
    );
  }

  const counts = {
    farmer: agents.filter((a) => a.type === "farmer").length,
    buyer: agents.filter((a) => a.type === "buyer").length,
    exporter: agents.filter((a) => a.type === "exporter").length,
  };

  return (
    <div className="panel">
      <div className="chart-header">
        <h2 style={{ margin: 0 }}>
          Агенты · {filtered.length} из {agents.length}
        </h2>
        <button onClick={() => setShowAdd((v) => !v)}>{showAdd ? "× Отмена" : "+ Добавить агента"}</button>
      </div>

      {showAdd && (
        <AddAgentForm
          regions={regions}
          crops={crops}
          busy={busy}
          onAdd={async (req) => { await onAdd(req); setShowAdd(false); }}
        />
      )}

      {/* filters */}
      <div className="agents-filters">
        <div className="agents-type-tabs">
          {(["all", "farmer", "buyer", "exporter"] as const).map((t) => (
            <button
              key={t}
              className={`density-btn${typeFilter === t ? " active" : ""}`}
              onClick={() => setTypeFilter(t)}
            >
              {t === "all" ? `Все · ${agents.length}` : `${TYPE_LABEL[t]} · ${counts[t]}`}
            </button>
          ))}
        </div>
        <select className="crop-select" value={regionFilter} onChange={(e) => setRegionFilter(e.target.value)}>
          <option value="all">Все регионы</option>
          {regionsWithAgents.map((r) => (
            <option key={r.id} value={r.id}>{r.name}</option>
          ))}
        </select>
        <input
          className="crop-select"
          style={{ flex: 1, minWidth: 140 }}
          placeholder="Поиск по имени / id…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div style={{ overflowX: "auto", marginTop: 12 }}>
        <table className="param-table" style={{ minWidth: 760 }}>
          <thead>
            <tr>
              <th className="sortable" onClick={() => toggleSort("name")}>Название{arrow("name")}</th>
              <th className="sortable" onClick={() => toggleSort("type")}>Тип{arrow("type")}</th>
              <th className="sortable" onClick={() => toggleSort("region")}>Регион{arrow("region")}</th>
              <th className="sortable num" onClick={() => toggleSort("cash")}>Баланс, ₽{arrow("cash")}</th>
              <th className="sortable num" onClick={() => toggleSort("storage")}>Склад, т{arrow("storage")}</th>
              <th className="sortable num" onClick={() => toggleSort("size")}>Объём{arrow("size")}</th>
              <th>Доп.</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((a) => <AgentRow key={a.id} agent={a} regionName={regionName} />)}
          </tbody>
        </table>
      </div>
      {filtered.length === 0 && <p className="muted" style={{ marginTop: 10 }}>Нет агентов под фильтр.</p>}
    </div>
  );
}

function AgentRow({ agent, regionName }: { agent: Agent; regionName: (id: string) => string }) {
  const cashColor = agent.cash < 0 ? "#c0392b" : undefined;
  let size = "";
  let extra: React.ReactNode = null;

  if (agent.type === "farmer") {
    const f = agent as FarmerAgent;
    size = `${fmt(f.total_area_ha)} га`;
    extra = f.insolvent_months > 0
      ? <span style={{ color: "#c0392b" }}>долг {f.insolvent_months} мес.</span>
      : <span className="muted">{f.allowed_crop_ids.length} культур</span>;
  } else if (agent.type === "buyer") {
    const b = agent as BuyerAgent;
    const cur = sumValues(b.monthly_consumption);
    const base = sumValues(b.monthly_consumption_baseline);
    const pct = base > 0 ? (cur / base) * 100 : NaN;
    size = `${fmt(cur)} т/мес.`;
    const color = pct > 102 ? "#2c6e49" : pct < 98 ? "#c0392b" : "var(--muted)";
    extra = (
      <span>
        {b.buyer_type} · спрос <strong style={{ color }}>{Number.isFinite(pct) ? `${fmt(pct)}%` : "—"}</strong>
        {b.insolvent_months > 0 && <span style={{ color: "#c0392b" }}> · долг {b.insolvent_months} мес.</span>}
      </span>
    );
  } else {
    const e = agent as ExporterAgent;
    const contract = sumValues(e.monthly_capacity_tons);
    const target = sumValues(e.ship_target);
    const pct = contract > 0 && target > 0 ? (target / contract) * 100 : NaN;
    size = `${fmt(contract)} т/мес.`;
    const color = pct > 102 ? "#2c6e49" : pct < 98 ? "#c0392b" : "var(--muted)";
    extra = (
      <span>
        {e.destination_country} · вывоз{" "}
        <strong style={{ color }}>{Number.isFinite(pct) ? `${fmt(pct)}%` : "—"}</strong>
      </span>
    );
  }

  return (
    <tr>
      <td className="param-label" style={{ color: "var(--ink)", fontWeight: 500 }}>{agent.name}</td>
      <td>{TYPE_LABEL[agent.type]}</td>
      <td>{regionName(agent.region_id)}</td>
      <td className="num" style={{ color: cashColor, fontWeight: 600 }}>{fmt(agent.cash)}</td>
      <td className="num">{fmt(agent.storage_tons)}</td>
      <td className="num">{size}</td>
      <td style={{ fontSize: 11 }}>{extra}</td>
    </tr>
  );
}

// ------------------------------------------------------------------ add form

interface CropAmount { crop: string; amount: number }

function AddAgentForm({ regions, crops, busy, onAdd }: {
  regions: Region[];
  crops: Crop[];
  busy: boolean;
  onAdd: (req: AddAgentRequest) => Promise<void>;
}) {
  const [kind, setKind] = useState<Agent["type"]>("farmer");
  const [name, setName] = useState("");
  const [regionId, setRegionId] = useState("");
  const [error, setError] = useState<string | null>(null);

  // farmer
  const [areaHa, setAreaHa] = useState(3000);
  const [farmerCrops, setFarmerCrops] = useState<string[]>([]);
  // buyer
  const [buyerType, setBuyerType] = useState("flour_mill");
  const [flexibility, setFlexibility] = useState(0.6);
  const [elasticity, setElasticity] = useState(0.3);
  // buyer/exporter shared crop+amount rows
  const [rows, setRows] = useState<CropAmount[]>([{ crop: "", amount: 1000 }]);
  // exporter
  const [destination, setDestination] = useState("");

  const goods = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of crops) m.set(c.market_good, c.market_good_name);
    return [...m.entries()].map(([id, label]) => ({ id, label }));
  }, [crops]);

  const sortedRegions = useMemo(
    () => [...regions].sort((a, b) => a.name.localeCompare(b.name, "ru")),
    [regions],
  );

  const setRow = (i: number, patch: Partial<CropAmount>) =>
    setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)));
  const addRow = () => setRows((rs) => [...rs, { crop: "", amount: 1000 }]);
  const removeRow = (i: number) => setRows((rs) => rs.filter((_, k) => k !== i));

  const submit = async () => {
    setError(null);
    if (!regionId) return setError("Выберите регион.");
    const id = `${kind}-user-${Date.now().toString(36)}`;
    const finalName = name.trim() || undefined;

    let req: AddAgentRequest;
    if (kind === "farmer") {
      if (farmerCrops.length === 0) return setError("Выберите хотя бы одну культуру.");
      if (areaHa <= 0) return setError("Площадь должна быть больше нуля.");
      req = { kind, farmer: { id, name: finalName, region_id: regionId, total_area_ha: areaHa, allowed_crop_ids: farmerCrops } };
    } else {
      const map: Record<string, number> = {};
      for (const r of rows) if (r.crop && r.amount > 0) map[r.crop] = r.amount;
      if (Object.keys(map).length === 0) return setError("Добавьте хотя бы одну культуру с объёмом.");
      if (kind === "buyer") {
        req = { kind, buyer: { id, name: finalName, region_id: regionId, buyer_type: buyerType, monthly_consumption: map, flexibility, demand_elasticity: elasticity } };
      } else {
        req = { kind, exporter: { id, name: finalName, region_id: regionId, destination_country: destination.trim() || "—", handled_crop_ids: Object.keys(map), monthly_capacity_tons: map } };
      }
    }
    try {
      await onAdd(req);
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <div className="param-section" style={{ marginBottom: 14 }}>
      <div className="param-section-title">Новый агент</div>

      <div className="agents-type-tabs" style={{ marginBottom: 10 }}>
        {(["farmer", "buyer", "exporter"] as const).map((k) => (
          <button key={k} className={`density-btn${kind === k ? " active" : ""}`} onClick={() => setKind(k)}>
            {TYPE_LABEL[k]}
          </button>
        ))}
      </div>

      <div className="field-row">
        <label className="field">
          Название (необязательно)
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Авто-имя, если пусто" />
        </label>
        <label className="field">
          Регион
          <select value={regionId} onChange={(e) => setRegionId(e.target.value)}>
            <option value="">— выберите —</option>
            {sortedRegions.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        </label>
      </div>

      {kind === "farmer" && (
        <>
          <div className="field-row">
            <label className="field">
              Площадь, га
              <input type="number" min={1} value={areaHa} onChange={(e) => setAreaHa(Number(e.target.value) || 0)} />
            </label>
          </div>
          <label className="field">
            Культуры (сорта) — можно несколько
            <select
              multiple
              size={Math.min(8, Math.max(3, crops.length))}
              value={farmerCrops}
              onChange={(e) => setFarmerCrops(Array.from(e.target.selectedOptions, (o) => o.value))}
            >
              {crops.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </label>
        </>
      )}

      {kind === "buyer" && (
        <div className="field-row">
          <label className="field">
            Тип покупателя
            <select value={buyerType} onChange={(e) => setBuyerType(e.target.value)}>
              {BUYER_TYPES.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
            </select>
          </label>
          <label className="field">
            Гибкость φ (0–1)
            <input type="number" min={0} max={1} step={0.05} value={flexibility} onChange={(e) => setFlexibility(Number(e.target.value) || 0)} />
          </label>
          <label className="field">
            Эластичность ε
            <input type="number" min={0} step={0.05} value={elasticity} onChange={(e) => setElasticity(Number(e.target.value) || 0)} />
          </label>
        </div>
      )}

      {kind === "exporter" && (
        <div className="field-row">
          <label className="field">
            Страна назначения
            <input value={destination} onChange={(e) => setDestination(e.target.value)} placeholder="напр. Египет" />
          </label>
        </div>
      )}

      {kind !== "farmer" && (
        <>
          <div className="param-section-label">
            {kind === "buyer" ? "Потребление по культурам (т/мес.)" : "Контракт по культурам (т/мес.)"}
          </div>
          {rows.map((r, i) => (
            <div className="field-row" key={i} style={{ alignItems: "flex-end" }}>
              <label className="field" style={{ flex: 2 }}>
                Культура
                <select value={r.crop} onChange={(e) => setRow(i, { crop: e.target.value })}>
                  <option value="">— выберите —</option>
                  {goods.map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}
                </select>
              </label>
              <label className="field" style={{ flex: 1 }}>
                т/мес.
                <input type="number" min={0} step={100} value={r.amount} onChange={(e) => setRow(i, { amount: Number(e.target.value) || 0 })} />
              </label>
              <button className="secondary" style={{ marginBottom: 12 }} onClick={() => removeRow(i)} disabled={rows.length <= 1}>×</button>
            </div>
          ))}
          <button className="collapse-toggle" style={{ marginBottom: 10 }} onClick={addRow}>+ ещё культура</button>
        </>
      )}

      {error && <div className="error-banner" style={{ margin: "8px 0" }}>{error}</div>}

      <div className="button-row">
        <button onClick={submit} disabled={busy}>Добавить в симуляцию</button>
      </div>
      <p className="muted" style={{ marginTop: 8 }}>
        Агент появится со следующего шага. Распределение цен и ожиданий он подхватит из текущего состояния рынка.
      </p>
    </div>
  );
}
