import { useCallback, useEffect, useRef, useState } from "react";
import { referenceApi, simulationApi } from "./api/client";
import type {
  AddAgentRequest,
  Agent,
  Crop,
  ExportRecord,
  MarketHistory,
  Region,
  ScenarioConfigIn,
  SimulationState,
  StepRecord,
} from "./api/types";
import AgentsPanel from "./components/AgentsPanel";
import ControlPanel from "./components/ControlPanel";
import ExportsPanel from "./components/ExportsPanel";
import MapView from "./components/MapView";
import ParametersPanel from "./components/ParametersPanel";
import PriceCharts from "./components/PriceCharts";
import ScenarioManipulationPanel from "./components/ScenarioManipulationPanel";
import StatsPanel from "./components/StatsPanel";

type TabId = "overview" | "map" | "agents" | "prices" | "exports" | "params" | "manipulate";

const TABS: { id: TabId; label: string; icon: string }[] = [
  { id: "overview",   label: "Обзор",      icon: "📊" },
  { id: "map",        label: "Карта",      icon: "🗺️" },
  { id: "agents",     label: "Агенты",     icon: "👥" },
  { id: "prices",     label: "Цены",       icon: "📈" },
  { id: "exports",    label: "Экспорт",    icon: "🚢" },
  { id: "manipulate", label: "Сценарий",   icon: "🎛️" },
  { id: "params",     label: "Параметры",  icon: "⚙️" },
];

export default function App() {
  const [crops, setCrops]               = useState<Crop[]>([]);
  const [regions, setRegions]           = useState<Region[]>([]);
  const [state, setState]               = useState<SimulationState | null>(null);
  const [agents, setAgents]             = useState<Agent[]>([]);
  const [history, setHistory]           = useState<StepRecord[]>([]);
  const [marketHistory, setMarketHistory] = useState<MarketHistory>({});
  const [exportRecords, setExportRecords] = useState<ExportRecord[]>([]);
  const [activeConfig, setActiveConfig] = useState<Partial<ScenarioConfigIn> | null>(null);
  const [busy, setBusy]                 = useState(false);
  const [error, setError]               = useState<string | null>(null);
  const [activeTab, setActiveTab]       = useState<TabId>("overview");
  const [autorun, setAutorun]           = useState(false);
  const [autorunSpeed, setAutorunSpeed] = useState(1500);

  // Ref to prevent stacking autorun steps when the previous one hasn't finished
  const busyRef = useRef(false);

  useEffect(() => {
    referenceApi.crops().then(setCrops).catch((err) => setError(String(err)));
    referenceApi.regions().then(setRegions).catch((err) => setError(String(err)));
  }, []);

  const refreshDerivedData = useCallback(async () => {
    const [agentsData, historyData, marketData, exportsData] = await Promise.all([
      simulationApi.agents(),
      simulationApi.history(),
      simulationApi.market(),
      simulationApi.exports(),
    ]);
    setAgents(agentsData);
    setHistory(historyData);
    setMarketHistory(marketData);
    setExportRecords(exportsData);
  }, []);

  const handleStart = useCallback(
    async (config: Partial<ScenarioConfigIn>) => {
      setBusy(true);
      busyRef.current = true;
      setError(null);
      setAutorun(false);
      try {
        const newState = await simulationApi.start(config);
        setState(newState);
        setActiveConfig(config);
        await refreshDerivedData();
      } catch (err) {
        setError(String(err));
      } finally {
        setBusy(false);
        busyRef.current = false;
      }
    },
    [refreshDerivedData],
  );

  const handleStep = useCallback(
    async (n: number) => {
      if (busyRef.current) return;
      setBusy(true);
      busyRef.current = true;
      setError(null);
      try {
        const response = await simulationApi.step(n);
        setState(response.state);
        await refreshDerivedData();
      } catch (err) {
        setError(String(err));
        setAutorun(false);
      } finally {
        setBusy(false);
        busyRef.current = false;
      }
    },
    [refreshDerivedData],
  );

  const handleAddAgent = useCallback(
    async (req: AddAgentRequest) => {
      setError(null);
      await simulationApi.addAgent(req);          // throws on 400 → surfaced by caller's catch
      const [newState] = await Promise.all([simulationApi.state(), refreshDerivedData()]);
      setState(newState);
    },
    [refreshDerivedData],
  );

  // Autorun: schedule the next step via setTimeout so we always wait for the
  // current response before firing again (adapts naturally to backend latency).
  useEffect(() => {
    if (!autorun || !state) return;
    const id = setTimeout(() => {
      if (!busyRef.current) handleStep(1);
    }, autorunSpeed);
    return () => clearTimeout(id);
  }, [autorun, state, autorunSpeed, handleStep, history]); // history changes after each step → re-arms timer

  const running = state !== null;

  return (
    <div className="app-layout">
      <header className="app-header">
        <div>
          <h1>🌾 Агропродовольственный рынок России</h1>
          <span className="header-sub">Агентная имитационная модель</span>
        </div>
        {running && (
          <div className="header-status">
            <span className={`status-dot${autorun ? " running" : ""}`} />
            {autorun
              ? `▶ авто · ${state.month < 10 ? "0" : ""}${state.month}.${state.year}`
              : `■ пауза · ${state.month < 10 ? "0" : ""}${state.month}.${state.year}`}
          </div>
        )}
      </header>

      <aside className="app-sidebar">
        <ControlPanel
          busy={busy}
          started={running}
          autorun={autorun}
          autorunSpeed={autorunSpeed}
          onStart={handleStart}
          onStep={handleStep}
          onToggleAutorun={() => setAutorun(v => !v)}
          onAutorunSpeedChange={setAutorunSpeed}
        />
      </aside>

      <main className="app-main">
        {error && (
          <div className="error-banner">
            {error}
            <button style={{ marginLeft: 12, padding: "2px 10px", fontSize: 12 }} onClick={() => setError(null)}>✕</button>
          </div>
        )}

        <nav className="tab-bar">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`tab-button${activeTab === tab.id ? " active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <span className="tab-icon">{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </nav>

        <div className="tab-content">
          {activeTab === "overview" && (
            <StatsPanel state={state} history={history} crops={crops} agents={agents} />
          )}
          {activeTab === "map" && (
            <MapView agents={agents} regions={regions} tall />
          )}
          {activeTab === "agents" && (
            <AgentsPanel
              agents={agents}
              regions={regions}
              crops={crops}
              running={running}
              busy={busy}
              onAdd={handleAddAgent}
            />
          )}
          {activeTab === "prices" && (
            <PriceCharts crops={crops} regions={regions} history={history} marketHistory={marketHistory} />
          )}
          {activeTab === "exports" && (
            <ExportsPanel exports={exportRecords} agents={agents} />
          )}
          {activeTab === "manipulate" && (
            <ScenarioManipulationPanel running={running} regions={regions} onApplied={refreshDerivedData} />
          )}
          {activeTab === "params" && (
            <ParametersPanel config={activeConfig} crops={crops} />
          )}
        </div>
      </main>
    </div>
  );
}
