"use client";

import { useEffect, useState, useCallback, ReactNode } from "react";
import {
  api, connectWS, normalizeControlStatusPayload,
  type Heartbeat, type Scorecard, type TimelinePoint, type ControlStatus,
  type GraduationReport, type PortfolioData, type TradeAnalysis,
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
import EvidenceLog from "@/components/EvidenceLog";
import TimeWindowBadge from "@/components/TimeWindowBadge";
import OrderFlow from "@/components/OrderFlow";
import AlertFeed from "@/components/AlertFeed";
import GraduationProgress from "@/components/GraduationProgress";
import PortfolioPanel from "@/components/PortfolioPanel";
import TradeAnalysisPanel from "@/components/TradeAnalysisPanel";
import PerformancePanel from "@/components/PerformancePanel";
import ConsciousnessPanel from "@/components/ConsciousnessPanel";
import ExecutionModePanel from "@/components/ExecutionModePanel";
import GSSSignalGraph from "@/components/GSSSignalGraph";
import PoliticianAlphaPanel from "@/components/PoliticianAlphaPanel";
import EquityCurve from "@/components/EquityCurve";
import ComponentRadar from "@/components/ComponentRadar";
import PnLWaterfall from "@/components/PnLWaterfall";
import DrawdownChart from "@/components/DrawdownChart";
import SectorExposure from "@/components/SectorExposure";
import OrderSuccessRate from "@/components/OrderSuccessRate";
import QuantumPanel from "@/components/QuantumPanel";
import LivePositionChart from "@/components/LivePositionChart";
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

// Default layout — Tetris-packed for maximum density on 1600px desktop
const DEFAULT_ROWS = [
  // Row 1 — Hero: equity curve + live position price chart + portfolio summary
  { id: "r1", widgets: [
    { id: "equity_curve", cols: 5, title: "Equity Curve", visible: true },
    { id: "live_price_chart", cols: 4, title: "Live Position", visible: true },
    { id: "portfolio", cols: 3, title: "Portfolio", visible: true },
  ]},
  // Row 2 — Execution + Performance + P&L (3 compact panels, balanced widths)
  { id: "r2", widgets: [
    { id: "execution_mode", cols: 3, title: "Execution Mode", visible: true },
    { id: "performance", cols: 3, title: "Performance", visible: true },
    { id: "pnl_waterfall", cols: 6, title: "P&L Waterfall", visible: true },
  ]},
  // Row 3 — Regime intelligence quad: gauge + radar + bars + controls
  { id: "r3", widgets: [
    { id: "regime_gauge", cols: 2, title: "Regime", visible: true },
    { id: "component_radar", cols: 4, title: "Risk Radar", visible: true },
    { id: "component_bars", cols: 4, title: "Component Scores", visible: true },
    { id: "system_controls", cols: 2, title: "Controls", visible: true },
  ]},
  // Row 4 — GSS signal (wide chart) + regime timeline (supporting chart)
  { id: "r4", widgets: [
    { id: "gss_signal_graph", cols: 7, title: "GSS Econophysics", visible: true },
    { id: "regime_timeline", cols: 5, title: "Regime Timeline", visible: true },
  ]},
  // Row 5 — Trade analysis + order flow + order success rate (all trade-related together)
  { id: "r5", widgets: [
    { id: "trade_analysis", cols: 6, title: "Trade Analysis", visible: true, badge: "SHADOW" },
    { id: "order_flow", cols: 4, title: "Order Flow", visible: true },
    { id: "order_success_rate", cols: 2, title: "Success Rate", visible: true },
  ]},
  // Row 6 — Sector + drawdown (two wide charts, equal height)
  { id: "r6", widgets: [
    { id: "sector_exposure", cols: 7, title: "Sector Exposure", visible: true },
    { id: "drawdown_chart", cols: 5, title: "Drawdown from Peak", visible: true },
  ]},
  // Row 7 — Intelligence: evidence + capitol whale + alerts + consciousness
  { id: "r7", widgets: [
    { id: "evidence_log", cols: 4, title: "Evidence Signals", visible: true },
    { id: "politician_alpha", cols: 3, title: "Capitol Whale", visible: true },
    { id: "alert_feed", cols: 3, title: "Alert Feed", visible: true },
    { id: "consciousness", cols: 2, title: "Consciousness", visible: true },
  ]},
  // Row 8 — Quantum research full-width
  { id: "r8", widgets: [
    { id: "quantum_comparison", cols: 12, title: "Quantum vs Classical", visible: true, badge: "BOUNDED SECONDARY" },
  ]},
  // Row 9 — Graduation progress full-width
  { id: "r9", widgets: [
    { id: "graduation", cols: 12, title: "Graduation Progress", visible: true },
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
  const [controlStatus, setControlStatus] = useState<ControlStatus | null>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [executionSummary, setExecutionSummary] = useState<ExecutionSummary | null>(null);
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatusResponse | null>(null);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [graduation, setGraduation] = useState<GraduationReport | null>(null);
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
      const [hb, sc, tl, ctrl, bridgeState, ord, execSummary, al, grad, port, ta, perf, cons, execMode, polAlpha, ly, qd] = await Promise.all([
        api.heartbeat().catch(() => null),
        api.latestScorecard().catch(() => null),
        api.timeline(200).catch(() => []),
        api.controlStatus().catch(() => null),
        api.bridges().catch(() => null),
        api.orders(50).catch(() => []),
        api.executionSummary(100, 100, 24).catch(() => null),
        api.alerts(30).catch(() => []),
        api.graduation().catch(() => null),
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
      if (ctrl) setControlStatus(ctrl);
      if (bridgeState) setBridgeStatus(bridgeState);
      if (Array.isArray(ord)) setOrders(ord);
      if (execSummary) setExecutionSummary(execSummary);
      if (Array.isArray(al)) setAlerts(al);
      if (grad && !("error" in grad)) setGraduation(grad);
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
      const liveControlStatus = normalizeControlStatusPayload(data);
      if (liveControlStatus) {
        setControlStatus((previous) => (previous ? { ...previous, ...liveControlStatus } : liveControlStatus));
      }
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
      case "live_price_chart":
        return <LivePositionChart />;
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
      case "component_radar":
        return <ComponentRadar scores={scorecard?.component_scores || null} />;
      case "component_bars":
        return scorecard?.component_scores
          ? <ComponentBars scores={scorecard.component_scores} />
          : <div className="text-gray-600 text-xs">No data</div>;
      case "system_controls":
        return (
          <>
            {controlStatus && (
              <ControlPanel
                controlStatus={controlStatus}
                shadowEligible={controlStatus.shadow_eligible ?? scorecard?.shadow_execution_eligible ?? false}
                fallback={controlStatus.fallback_mode ?? scorecard?.fallback_mode_status ?? false}
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
      case "evidence_log":
        return <EvidenceLog evidence={scorecard?.evidence || []} />;
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
      case "graduation":
        return graduation ? (
          <GraduationProgress
            stage={graduation.stage}
            overallPass={graduation.overall_pass}
            checks={graduation.checks}
            summary={graduation.summary}
          />
        ) : (
          <div className="text-gray-600 text-xs">
            No graduation assessment. Run: check_graduation_criteria.py
          </div>
        );
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
          {portfolio && (
            <span className="text-gray-200 font-semibold tabular-nums">
              {new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(portfolio.equity)}
            </span>
          )}
          {error && (
            <span className="text-red-400 bg-red-950/20 px-2 py-0.5 rounded">{error}</span>
          )}
          <a
            href="/trading"
            className="px-4 py-2 sm:px-3 sm:py-1.5 rounded bg-[#3b82f6] border border-[#3b82f6]/50 hover:bg-[#2563eb] active:bg-[#1d4ed8] transition cursor-pointer select-none touch-manipulation text-sm text-white font-medium min-h-[44px] sm:min-h-0 flex items-center gap-1.5"
          >
            Trading →
          </a>
          <button
            onClick={async () => { setRefreshing(true); await fetchAll(); setRefreshing(false); }}
            disabled={refreshing}
            className="px-4 py-2 sm:px-3 sm:py-1.5 rounded bg-[#1a1f2e] border border-[#2a3040] hover:bg-[#1f2537] active:bg-[#252b3d] transition cursor-pointer select-none touch-manipulation disabled:opacity-50 text-sm min-h-[44px] sm:min-h-0"
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </header>

      {/* Dynamic Grid — each row is a 12-col subgrid so widgets within a row align */}
      <div className="flex flex-col gap-3">
        {rows.map((row) => {
          const visibleWidgets = row.widgets.filter((w) => w.visible !== false);
          if (visibleWidgets.length === 0) return null;
          return (
            <div key={row.id} className="grid grid-cols-1 md:grid-cols-12 gap-3 items-stretch">
              {visibleWidgets.map((w) => (
                <div key={w.id} className="card min-w-0 overflow-hidden flex flex-col" data-cols={w.cols}>
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-xs text-gray-500 uppercase tracking-wider truncate">{w.title}</h2>
                    {w.badge && (
                      <span className="text-[10px] text-yellow-500 bg-yellow-950/20 px-2 py-0.5 rounded border border-yellow-900/30 ml-2 flex-shrink-0">
                        {w.badge}
                      </span>
                    )}
                  </div>
                  <div className="flex-1 min-h-0">{renderWidget(w)}</div>
                </div>
              ))}
            </div>
          );
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
