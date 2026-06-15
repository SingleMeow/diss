import type {
  AddAgentRequest,
  Agent,
  BorderPoint,
  Crop,
  ExportRecord,
  InterveneRequest,
  Levers,
  LoadedRun,
  MarketHistory,
  Region,
  RunSummary,
  ScenarioConfigIn,
  SimulationState,
  StepRecord,
  StepResponse,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${init?.method ?? "GET"} ${path} -> ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const referenceApi = {
  regions: () => request<Region[]>("/reference/regions"),
  crops: () => request<Crop[]>("/reference/crops"),
  borderPoints: () => request<BorderPoint[]>("/reference/border-points"),
};

export const simulationApi = {
  start: (config: Partial<ScenarioConfigIn>) =>
    request<SimulationState>("/simulation/start", {
      method: "POST",
      body: JSON.stringify(config),
    }),
  step: (n: number) =>
    request<StepResponse>("/simulation/step", {
      method: "POST",
      body: JSON.stringify({ n }),
    }),
  state: () => request<SimulationState>("/simulation/state"),
  agents: () => request<Agent[]>("/simulation/agents"),
  addAgent: (req: AddAgentRequest) =>
    request<Agent>("/simulation/agents", {
      method: "POST",
      body: JSON.stringify(req),
    }),
  history: () => request<StepRecord[]>("/simulation/history"),
  market: () => request<MarketHistory>("/simulation/market"),
  exports: () => request<ExportRecord[]>("/simulation/exports"),
  levers: () => request<Levers>("/simulation/levers"),
  intervene: (patch: InterveneRequest) =>
    request<Levers>("/simulation/intervene", {
      method: "POST",
      body: JSON.stringify(patch),
    }),
  // Stored-run browsing (persisted history; no active run required).
  runs: () => request<RunSummary[]>("/simulation/runs"),
  run: (id: number) => request<LoadedRun>(`/simulation/runs/${id}`),
  deleteRun: (id: number) =>
    request<{ deleted: number }>(`/simulation/runs/${id}`, { method: "DELETE" }),
};
