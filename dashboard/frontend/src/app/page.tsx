"use client";

import { useEffect, useState, useCallback, ReactNode } from "react";
import {
  api, connectWS,
  type Heartbeat, type Scorecard, type TimelinePoint, type Controls,
  type PortfolioData, type TradeAnalysis,
  type ConsciousnessData, type ExecutionModeData, type PoliticianAlphaData,
  type DashboardLayout, type DashboardWidget, type ExecutionSummary,
  type BridgeStatusResponse,
} from "@/lib/api";
import ModeIndicator from "@/components/ModeIndicator";
import RegimeGauge from "@/components/RegimeGauge";
import ComponentBars from "@/components/ComponentBars";
import RegimeChart from "@/components/RegimeChart";
import BridgeHealth from "@/components/BridgeHealth";
import ControlPanel from "@/components/ControlPanel";
import TimeWindowBadge from "@/components/TimeWindowBadge";
import OrderFlow from "@/components/OrderFlow";
import AlertFeed from "@/components/AlertFeed";
import PortfolioPanel from "@/components/PortfolioPanel";
import TradeAnalysisPanel from "@/components/TradeAnalysisPanel";
import PerformancePanel from "@/components/PerformancePanel";
import ConsciousnessPanel from "@/components/ConsciousnessPanel";
import ExecutionModePanel from "@/components/ExecutionModePanel";
import GSSSignalGraph from "@/components/GSSSignalGraph";
import PoliticianAlphaPanel from "@/components/PoliticianAlphaPanel";
import EquityCurve from "@/components/EquityCurve";
import PnLWaterfall from "@/components/PnLWaterfall";
import DrawdownChart from "@/components/DrawdownChart";
import SectorExposure from "@/components/SectorExposure";
import OrderSuccessRate from "@/components/OrderSuccessRate";
import QuantumPanel from "@/components/QuantumPanel";
import type { QuantumData } from "@/components/QuantumPanel";

function timeAgo(ts?: string): string {
  if (!ts) return "never";
  try {
    const diff = Date.now() - new Date(ts).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ${mins % 60}m ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return ts;
  }
}

// Default layout used when API is unavailable
const DEFAULT_ROWS = [
  { id: "row_equity_portfolio", widgets: [
    { id: "equity_curve", cols: 7, title: "Equity Curve", visible: true },
    { id: "portfolio", cols: 5, title: "Portfolio", visible: true },
  ]},
  { id: "row_sector_exposure", widgets: [
    { id: "sector_exposure", cols: 12, title: "Sector & Asset Class Exposure", visible: true },
  ]},
  { id: "row_exec_perf_pnl", widgets: [
    { id: "execution_mode", cols: 3, title: "Execution Mode", visible: true },
    { id: "performance", cols: 3, title: "Performance", visible: true },
    { id: "pnl_waterfall", cols: 6, title: "P&L Waterfall — By Symbol", visible: true },
  ]},
  { id: "row_trades_orders", widgets: [
    { id: "trade_analysis", cols: 7, title: "Trade Analysis & Orders", visible: true, badge: "ADVISORY ONLY — Shadow Mode" },
    { id: "order_flow", cols: 5, title: "Order Flow", visible: true },
  ]},
  { id: "row_regime_radar_controls", widgets: [
    { id: "regime_gauge", cols: 4, title: "Regime Probability", visible: true },
    { id: "component_bars", cols: 4, title: "Component Scores", visible: true },
    { id: "system_controls", cols: 4, title: "System Controls", visible: true },
  ]},
  { id: "row_gss_regime_timeline", widgets: [
    { id: "gss_signal_graph", cols: 6, title: "GSS Econophysics — Three-Layer Signal Graph", visible: true },
    { id: "regime_timeline", cols: 6, title: "Regime Probability Timeline", visible: true },
  ]},
  { id: "row_alpha_alerts", widgets: [
    { id: "politician_alpha", cols: 6, title: "Capitol Whale — Politician Alpha", visible: true },
    { id: "alert_feed", cols: 6, title: "Alert Feed", visible: true },
  ]},
  { id: "row_drawdown_consciousness_orders", widgets: [
    { id: "drawdown_chart", cols: 5, title: "Drawdown from Peak", visible: true },
    { id: "consciousness", cols: 3, title: "Consciousness", visible: true },
    { id: "order_success_rate", cols: 4, title: "Order Success Rate", visible: true },
  ]},
  { id: "row_quantum", widgets: [
    { id: "quantum_comparison", cols: 12, title: "Quantum vs Classical — Optimization Research", visible: true, badge: "BOUNDED SECONDARY SIGNAL" },
  ]},
];

function mergeLayoutRows(layoutRows?: DashboardLayout["rows"] | null): DashboardLayout["rows"] {
  if (!layoutRows || layoutRows.length === 0) return DEFAULT_ROWS;

  const defaultWidgets = new Map(
    DEFAULT_ROWS.flatMap((row) => row.widgets.map((widget) => [widget.id, widget] as const)),
  );
  const seen = new Set<string>();

  const mergedRows = layoutRows.map((row) => {
    const widgets = row.widgets
      .map((widget) => {
        const fallback = defaultWidgets.get(widget.id);
        seen.add(widget.id);
        return fallback ? { ...fallback, ...widget } : widget;
      })
      .filter(Boolean);
    return { ...row, widgets };
  });

  for (const defaultRow of DEFAULT_ROWS) {
    const missingWidgets = defaultRow.widgets
      .filter((widget) => !seen.has(widget.id))
      .map((widget) => ({ ...widget }));
    if (missingWidgets.length > 0) {
      mergedRows.push({
        id: `${defaultRow.id}_upgrade`,
        widgets: missingWidgets,
      });
    }
  }

  return mergedRows;
}

const PORTFOLIO_REFRESH_EVENT = "gs:portfolio-refresh";

export default function Dashboard() {
  const REFRESH_MS = 10000;
  const [heartbeat, setHeartbeat] = useState<Heartbeat | null>(null);
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [controls, setControls] = useState<Controls | null>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [executionSummary, setExecutionSummary] = useState<ExecutionSummary | null>(null);
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatusResponse | null>(null);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [tradeAnalysis, setTradeAnalysis] = useState<TradeAnalysis | null>(null);
  const [performance, setPerformance] = useState<any>(null);
  const [consciousness, setConsciousness] = useState<ConsciousnessData | null>(null);
  const [executionMode, setExecutionMode] = useState<ExecutionModeData | null>(null);
  const [politicianAlpha, setPoliticianAlpha] = useState<PoliticianAlphaData | null>(null);
  const [quantum, setQuantum] = useState<QuantumData | null>(null);
  const [layout, setLayout] = useState<DashboardLayout | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const [refreshing, setRefreshing] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [hb, sc, tl, ctrl, bridgeState, ord, execSummary, al, port, ta, perf, cons, execMode, polAlpha, ly, qd] = await Promise.all([
        api.heartbeat().catch(() => null),
        api.latestScorecard().catch(() => null),
        api.timeline(200).catch(() => []),
        api.controls().catch(() => null),
        api.bridges().catch(() => null),
        api.orders(50).catch(() => []),
        api.executionSummary(100, 100, 24).catch(() => null),
        api.alerts(30).catch(() => []),
        api.portfolio().catch(() => null),
        api.tradeAnalysis().catch(() => null),
        api.performance().catch(() => null),
        api.consciousness().catch(() => null),
        api.executionMode().catch(() => null),
        api.politicianAlpha().catch(() => null),
        api.dashboardLayout().catch(() => null),
        api.quantum().catch(() => null),
      ]);
      if (hb) setHeartbeat(hb);
      if (sc && !("error" in sc)) setScorecard(sc);
      if (Array.isArray(tl)) setTimeline(tl);
      if (ctrl) setControls(ctrl);
      if (bridgeState) setBridgeStatus(bridgeState);
      if (Array.isArray(ord)) setOrders(ord);
      if (execSummary) setExecutionSummary(execSummary);
      if (Array.isArray(al)) setAlerts(al);
      if (port && !port.error) setPortfolio(port);
      if (ta && !ta.error) setTradeAnalysis(ta);
      if (perf && !perf.error) setPerformance(perf);
      if (cons && !("error" in cons)) setConsciousness(cons);
      if (execMode) setExecutionMode(execMode);
      if (polAlpha) setPoliticianAlpha(polAlpha);
      if (ly && !ly.error && ly.rows) setLayout(ly);
      if (qd && !qd.error) setQuantum(qd);
      setError(null);
      setLastRefresh(new Date());
    } catch (e: any) {
      setError(e.message || "Failed to connect");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, REFRESH_MS);

    const ws = connectWS((data) => {
      if (data.heartbeat) setHeartbeat(data.heartbeat);
      if (data.scorecard) setScorecard(data.scorecard);
      if (data.controls) setControls(data.controls);
      if (data.execution_mode) setExecutionMode(data.execution_mode);
      if (data.portfolio && !data.portfolio.error) {
        setPortfolio(data.portfolio);
      }
      if (data.portfolio || data.portfolio_history_intraday) {
        window.dispatchEvent(new CustomEvent(PORTFOLIO_REFRESH_EVENT, {
          detail: {
            portfolio: data.portfolio || null,
            portfolio_history_intraday: data.portfolio_history_intraday || null,
            received_at_utc: new Date().toISOString(),
          },
        }));
      }
      setLastRefresh(new Date());
    });

    return () => {
      clearInterval(interval);
      ws?.close();
    };
  }, [fetchAll]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <div className="text-gray-500 text-sm">Connecting to Global Sentinel...</div>
        </div>
      </div>
    );
  }

  const mode = scorecard?.mode || heartbeat?.mode || "UNKNOWN";
  const regimeP = scorecard?.regime_shift_probability || 0;
  const confidence = scorecard?.confidence || 0;
  const cycle = scorecard?.cycle || heartbeat?.cycle || 0;
  const tw = scorecard?.time_window;

  // Widget renderer — maps widget ID to its React component
  function renderWidget(w: DashboardWidget): ReactNode {
    if (!w.visible) return null;

    switch (w.id) {
      case "equity_curve":
        return <EquityCurve />;
      case "portfolio":
        return <PortfolioPanel data={portfolio} />;
      case "execution_mode":
        return <ExecutionModePanel data={executionMode} onModeChange={fetchAll} />;
      case "performance":
        return <PerformancePanel data={performance} portfolio={portfolio} />;
      case "pnl_waterfall":
        return <PnLWaterfall data={performance} portfolio={portfolio} />;
      case "trade_analysis":
        return <TradeAnalysisPanel data={tradeAnalysis} />;
      case "order_flow":
        return <OrderFlow orders={orders} />;
      case "regime_gauge":
        return <RegimeGauge regimeP={regimeP} confidence={confidence} />;
      case "component_bars":
        return scorecard?.component_scores
          ? <ComponentBars scores={scorecard.component_scores} />
          : <div className="text-gray-600 text-xs">No data</div>;
      case "system_controls":
        return (
          <>
            {controls && (
              <ControlPanel
                controls={controls}
                shadowEligible={scorecard?.shadow_execution_eligible || false}
                fallback={scorecard?.fallback_mode_status || false}
              />
            )}
            <div className="mt-3">
              <h3 className="text-xs text-gray-500 uppercase tracking-wider mb-2">Bridge Health</h3>
              <BridgeHealth
                bridges={bridgeStatus?.bridges}
                freshness={(scorecard?.data_freshness_status || {}) as Record<string, boolean>}
                summary={(scorecard?.bridge_summary || {}) as Record<string, number | undefined>}
              />
            </div>
          </>
        );
      case "gss_signal_graph":
        return <GSSSignalGraph />;
      case "regime_timeline":
        return (
          <>
            <div className="flex items-center justify-between mb-2 -mt-1">
              <div />
              <div className="flex items-center gap-3 text-[10px] text-gray-500">
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-blue-500 inline-block" /> Regime P
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-purple-500 inline-block" /> Confidence
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-yellow-500 inline-block opacity-50" style={{ borderTop: "1px dashed" }} /> Elevated
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-red-500 inline-block opacity-50" style={{ borderTop: "1px dashed" }} /> Crisis
                </span>
              </div>
            </div>
            <RegimeChart data={timeline} />
          </>
        );
      case "politician_alpha":
        return <PoliticianAlphaPanel data={politicianAlpha} />;
      case "alert_feed":
        return <AlertFeed alerts={alerts} />;
      case "drawdown_chart":
        return <DrawdownChart />;
      case "consciousness":
        return <ConsciousnessPanel data={consciousness} />;
      case "order_success_rate":
        return <OrderSuccessRate summary={executionSummary} />;
      case "sector_exposure":
        return <SectorExposure portfolio={portfolio} />;
      case "quantum_comparison":
        return <QuantumPanel data={quantum} />;
      default:
        return <div className="text-gray-600 text-xs">Unknown widget: {w.id}</div>;
    }
  }

  const rows = mergeLayoutRows(layout?.rows);

  return (
    <div className="min-h-screen p-2 sm:p-4 max-w-[1600px] mx-auto">
      {/* Header */}
      <header className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-4">
        <div className="flex items-center gap-2 sm:gap-4 flex-wrap">
          <h1 className="text-base sm:text-lg font-bold text-gray-200 tracking-tight">GLOBAL SENTINEL</h1>
          <a
            href="/trading"
            className="px-3 py-1.5 rounded text-xs font-medium bg-blue-600/20 text-blue-400 border border-blue-500/30 hover:bg-blue-600/30 transition"
          >
            Trading
          </a>
          <ModeIndicator mode={mode} />
          {tw && (
            <TimeWindowBadge
              window={tw.current_window}
              time={tw.timestamp_et_hhmm}
            />
          )}
        </div>
        <div className="flex items-center gap-2 sm:gap-4 text-xs text-gray-500">
          <span>Cycle #{cycle}</span>
          <span>Updated {timeAgo(scorecard?.timestamp_utc)}</span>
          {error && (
            <span className="text-red-400 bg-red-950/20 px-2 py-0.5 rounded">{error}</span>
          )}
          <button
            onClick={async () => { setRefreshing(true); await fetchAll(); setRefreshing(false); }}
            disabled={refreshing}
            className="px-4 py-2 sm:px-3 sm:py-1.5 rounded bg-[#1a1f2e] border border-[#2a3040] hover:bg-[#1f2537] active:bg-[#252b3d] transition cursor-pointer select-none touch-manipulation disabled:opacity-50 text-sm min-h-[44px] sm:min-h-0"
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </header>

      {/* Dynamic Grid */}
      <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
        {rows.map((row) => {
          const visibleWidgets = row.widgets.filter((w) => w.visible !== false);
          if (visibleWidgets.length === 0) return null;
          return visibleWidgets.map((w) => (
            <div key={w.id} className="card" data-cols={w.cols}>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-xs text-gray-500 uppercase tracking-wider">{w.title}</h2>
                {w.badge && (
                  <span className="text-[10px] text-yellow-500 bg-yellow-950/20 px-2 py-0.5 rounded border border-yellow-900/30">
                    {w.badge}
                  </span>
                )}
              </div>
              {renderWidget(w)}
            </div>
          ));
        })}
      </div>

      {/* Footer */}
      <footer className="mt-4 text-center text-[10px] text-gray-700">
        Global Sentinel V5.1 | Shadow Mode | Last refresh: {lastRefresh.toLocaleTimeString()}
        {layout && <span> | Layout v{layout.version}</span>}
      </footer>
    </div>
  );
}
