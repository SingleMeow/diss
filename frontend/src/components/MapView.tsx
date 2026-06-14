import { useEffect, useMemo, useState } from "react";
import type { Layer, PathOptions, StyleFunction } from "leaflet";
import { CircleMarker, GeoJSON, MapContainer, Popup, TileLayer, Tooltip } from "react-leaflet";
import type { Agent, Region } from "../api/types";

const RUSSIA_CENTER: [number, number] = [58, 60];

const COLORS: Record<Agent["type"], string> = {
  farmer: "#2c6e49",
  buyer: "#3a6ea5",
  exporter: "#c1440e",
};

const RADIUS: Record<Agent["type"], number> = {
  farmer: 4,
  buyer: 5,
  exporter: 7,
};

const LABELS: Record<Agent["type"], string> = {
  farmer: "Фермер",
  buyer: "Покупатель",
  exporter: "Экспортёр",
};

const AGENT_TYPES = Object.keys(LABELS) as Agent["type"][];

const ZONE_LABELS: Record<string, string> = {
  south: "Юг", black_earth: "Чернозёмье", volga: "Поволжье", temperate: "Нечерноземье",
  west_siberia: "Зап. Сибирь", east_siberia: "Вост. Сибирь", far_east: "Дальний Восток", north: "Север",
};

const REGION_STYLE: PathOptions = {
  color: "#7d8ba1",
  weight: 1,
  fillColor: "#9fb3c8",
  fillOpacity: 0.12,
};

const REGION_HIGHLIGHT_STYLE: PathOptions = {
  color: "#2c6e49",
  weight: 2,
  fillColor: "#2c6e49",
  fillOpacity: 0.25,
};

// Which agent type the choropleth shades regions by (or "off" for the plain
// in-model highlight).
type DensityMetric = "farmer" | "buyer" | "off";

const DENSITY_LABELS: Record<DensityMetric, string> = {
  farmer: "Фермеры",
  buyer: "Покупатели",
  off: "Выкл.",
};

/** Quantised fill opacity for a region's agent count → light-to-dark choropleth. */
function binOpacity(count: number, max: number): number {
  if (count <= 0 || max <= 0) return 0.05;
  const r = count / max;
  if (r <= 0.25) return 0.22;
  if (r <= 0.5) return 0.42;
  if (r <= 0.75) return 0.62;
  return 0.82;
}

function formatNumber(n: number): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(n);
}

function formatPriceMap(prices: Record<string, number>): React.ReactNode {
  const entries = Object.entries(prices);
  if (entries.length === 0) return null;
  return (
    <div>
      Ценовые ожидания:{" "}
      {entries.map(([cropId, price], idx) => (
        <span key={cropId}>
          {idx > 0 && ", "}
          {cropId} — {formatNumber(price)} ₽/т
        </span>
      ))}
    </div>
  );
}

function describe(agent: Agent): React.ReactNode {
  switch (agent.type) {
    case "farmer":
      return (
        <>
          <div>Площадь: {formatNumber(agent.total_area_ha)} га</div>
          <div>
            Хранилище: {formatNumber(agent.storage_tons)} / {formatNumber(agent.storage_capacity_tons)} т
          </div>
          <div>Культуры: {agent.allowed_crop_ids.join(", ")}</div>
          {formatPriceMap(agent.expected_price)}
          <div>Баланс: {formatNumber(agent.cash)} ₽</div>
        </>
      );
    case "buyer":
      return (
        <>
          <div>Тип: {agent.buyer_type}</div>
          <div>
            Хранилище: {formatNumber(agent.storage_tons)} / {formatNumber(agent.storage_capacity_tons)} т
          </div>
          <div>Месячная потребность: {formatNumber(Object.values(agent.monthly_consumption).reduce((a, b) => a + b, 0))} т</div>
          <div>Гибкость спроса (φ): {formatNumber(agent.flexibility)}</div>
          <div>Баланс: {formatNumber(agent.cash)} ₽</div>
          {agent.insolvent_months > 0 && (
            <div style={{ color: "#c0392b" }}>Неплатёжеспособен: {agent.insolvent_months} мес.</div>
          )}
        </>
      );
    case "exporter":
      return (
        <>
          <div>Направление: {agent.destination_country}</div>
          <div>Культуры: {agent.handled_crop_ids.join(", ")}</div>
          <div>Хранилище: {formatNumber(agent.storage_tons)} т</div>
          <div>Гибкость спроса (φ): {formatNumber(agent.flexibility)}</div>
          <div>Баланс: {formatNumber(agent.cash)} ₽</div>
        </>
      );
  }
}

interface Props {
  agents: Agent[];
  regions: Region[];
  /** Render the map taller — used on the dedicated "Карта" tab. */
  tall?: boolean;
}

export default function MapView({ agents, regions, tall = false }: Props) {
  const [geoData, setGeoData] = useState<GeoJSON.FeatureCollection | null>(null);
  const [visible, setVisible] = useState<Record<Agent["type"], boolean>>({
    farmer: true,
    buyer: true,
    exporter: true,
  });
  const [density, setDensity] = useState<DensityMetric>("farmer");

  // agent counts per region id, for the active choropleth metric
  const countByRegion = useMemo(() => {
    const map = new Map<string, number>();
    if (density === "off") return map;
    for (const agent of agents) {
      if (agent.type !== density) continue;
      map.set(agent.region_id, (map.get(agent.region_id) ?? 0) + 1);
    }
    return map;
  }, [agents, density]);

  const maxCount = useMemo(() => {
    let m = 0;
    for (const v of countByRegion.values()) if (v > m) m = v;
    return m;
  }, [countByRegion]);

  // Signature that changes whenever the shading should change, so the GeoJSON
  // layer (which only restyles on remount) is forced to re-render.
  const geoKey = useMemo(() => {
    const sig = Array.from(countByRegion.entries()).sort().map(([k, v]) => `${k}${v}`).join(",");
    return `${density}|${sig}`;
  }, [density, countByRegion]);

  const densityColor = density === "buyer" ? COLORS.buyer : COLORS.farmer;

  useEffect(() => {
    let cancelled = false;
    fetch("/data/russia_regions.geojson")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled) setGeoData(data);
      })
      .catch(() => {
        if (!cancelled) setGeoData(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const regionByName = useMemo(() => {
    const map = new Map<string, Region>();
    for (const region of regions) map.set(region.name, region);
    return map;
  }, [regions]);

  const regionStyle: StyleFunction = (feature) => {
    const name = feature?.properties?.region as string | undefined;
    const region = regionByName.get(name ?? "");
    if (!region) return REGION_STYLE;                 // region not in the model
    if (density === "off") return REGION_HIGHLIGHT_STYLE;
    const count = countByRegion.get(region.id) ?? 0;
    return {
      color: count > 0 ? densityColor : "#7d8ba1",
      weight: 1,
      fillColor: densityColor,
      fillOpacity: binOpacity(count, maxCount),
    };
  };

  const onEachFeature = (feature: GeoJSON.Feature, layer: Layer) => {
    const name = (feature.properties?.region as string | undefined) ?? "Регион";
    const region = regionByName.get(name);
    if (!region) {
      layer.bindTooltip(`${name} (вне модели)`, { sticky: true });
      return;
    }
    const detail =
      density === "off"
        ? `${ZONE_LABELS[region.climate_zone] ?? region.climate_zone}`
        : `${DENSITY_LABELS[density].toLowerCase()}: ${countByRegion.get(region.id) ?? 0}`;
    layer.bindTooltip(`${name} — ${detail}`, { sticky: true });
  };

  const toggle = (type: Agent["type"]) =>
    setVisible((prev) => ({ ...prev, [type]: !prev[type] }));

  const counts = useMemo(() => {
    const out: Record<Agent["type"], number> = { farmer: 0, buyer: 0, exporter: 0 };
    for (const agent of agents) out[agent.type] += 1;
    return out;
  }, [agents]);

  return (
    <div className={tall ? "map-card map-card--tall" : "map-card"}>
      <MapContainer center={RUSSIA_CENTER} zoom={3} style={{ height: "100%", width: "100%" }}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {geoData && (
          <GeoJSON
            key={geoKey}
            data={geoData}
            style={regionStyle}
            onEachFeature={onEachFeature}
          />
        )}
        {agents
          .filter((agent) => visible[agent.type])
          .map((agent) => (
            <CircleMarker
              key={agent.id}
              center={[agent.lat, agent.lon]}
              radius={RADIUS[agent.type]}
              pathOptions={{ color: COLORS[agent.type], fillColor: COLORS[agent.type], fillOpacity: 0.75 }}
            >
              <Tooltip>{agent.name}</Tooltip>
              <Popup>
                <strong>{agent.name}</strong>
                <div className="muted">{LABELS[agent.type]} · {agent.region_id}</div>
                {describe(agent)}
              </Popup>
            </CircleMarker>
          ))}
      </MapContainer>
      <div className="legend">
        {AGENT_TYPES.map((type) => (
          <label className="legend-item legend-toggle" key={type}>
            <input type="checkbox" checked={visible[type]} onChange={() => toggle(type)} />
            <span className="legend-dot" style={{ background: COLORS[type] }} />
            {LABELS[type]} · {counts[type]}
          </label>
        ))}

        {/* Choropleth control: which agent type to shade regions by */}
        <span className="legend-item" style={{ gap: 6 }}>
          <span className="muted">Плотность по регионам:</span>
          {(["farmer", "buyer", "off"] as DensityMetric[]).map((m) => (
            <button
              key={m}
              className={`density-btn${density === m ? " active" : ""}`}
              onClick={() => setDensity(m)}
            >
              {DENSITY_LABELS[m]}
            </button>
          ))}
        </span>

        {/* Gradient scale 0 → max for the active metric */}
        {density !== "off" && maxCount > 0 && (
          <span className="legend-item" style={{ gap: 6 }}>
            <span className="muted">0</span>
            <span
              className="density-gradient"
              style={{
                background: `linear-gradient(to right, ${densityColor}1f, ${densityColor})`,
              }}
            />
            <span className="muted">{maxCount} {DENSITY_LABELS[density].toLowerCase()}</span>
          </span>
        )}

        <span className="legend-item muted">Границы регионов — из переданного geojson (Russia_regions)</span>
      </div>
    </div>
  );
}
