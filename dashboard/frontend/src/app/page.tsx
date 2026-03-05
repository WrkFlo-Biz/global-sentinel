"use client";

import { useEffect, useState, useCallback } from "react";
import {
  api, connectWS,
  type Heartbeat, type Scorecard, type TimelinePoint, type Controls,
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

export default function Dashboard() {
  const [heartbeat, setHeartbeat] = useState<Heartbeat | null>(null);
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [controls, setControls] = useState<Controls | null>(null);
  const [orders, setOrders] = useState<any[]>([]);
  const [alerts, setAlerts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());

  const fetchAll = useCallback(async () => {
    try {
      const [hb, sc, tl, ctrl, ord, al] = await Promise.all([
        api.heartbeat().catch(() => null),
        api.latestScorecard().catch(() => null),
        api.timeline(200).catch(() => []),
        api.controls().catch(() => null),
        api.orders(50).catch(() => []),
        api.alerts(30).catch(() => []),
      ]);
      if (hb) setHeartbeat(hb);
      if (sc && !("error" in sc)) setScorecard(sc);
      if (Array.isArray(tl)) setTimeline(tl);
      if (ctrl) setControls(ctrl);
      if (Array.isArray(ord)) setOrders(ord);
      if (Array.isArray(al)) setAlerts(al);
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
    const interval = setInterval(fetchAll, 30000);

    const ws = connectWS((data) => {
      if (data.heartbeat) setHeartbeat(data.heartbeat);
      if (data.scorecard) setScorecard(data.scorecard);
      if (data.controls) setControls(data.controls);
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

  return (
    <div className="min-h-screen p-4 max-w-[1600px] mx-auto">
      {/* Header */}
      <header className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-bold text-gray-200 tracking-tight">GLOBAL SENTINEL</h1>
          <ModeIndicator mode={mode} />
          {tw && (
            <TimeWindowBadge
              window={tw.current_window}
              time={tw.timestamp_et_hhmm}
            />
          )}
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span>Cycle #{cycle}</span>
          <span>Updated {timeAgo(scorecard?.timestamp_utc)}</span>
          {error && (
            <span className="text-red-400 bg-red-950/20 px-2 py-0.5 rounded">{error}</span>
          )}
          <button
            onClick={fetchAll}
            className="px-2 py-1 rounded bg-[#1a1f2e] border border-[#2a3040] hover:bg-[#1f2537] transition"
          >
            Refresh
          </button>
        </div>
      </header>

      {/* Main Grid */}
      <div className="grid grid-cols-12 gap-3">

        {/* Row 1: Regime Gauge + Component Bars + Controls */}
        <div className="col-span-3 card">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Regime Probability</h2>
          <RegimeGauge regimeP={regimeP} confidence={confidence} />
        </div>

        <div className="col-span-5 card">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Component Scores</h2>
          {scorecard?.component_scores ? (
            <ComponentBars scores={scorecard.component_scores} />
          ) : (
            <div className="text-gray-600 text-xs">No data</div>
          )}
        </div>

        <div className="col-span-4 card">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">System Controls</h2>
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
              freshness={(scorecard?.data_freshness_status || {}) as Record<string, boolean>}
              summary={(scorecard?.bridge_summary || {}) as Record<string, number | undefined>}
            />
          </div>
        </div>

        {/* Row 2: Regime Timeline Chart */}
        <div className="col-span-8 card">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-xs text-gray-500 uppercase tracking-wider">Regime Probability Timeline</h2>
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
        </div>

        {/* Row 2 Right: Evidence */}
        <div className="col-span-4 card">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Evidence Signals</h2>
          <EvidenceLog evidence={scorecard?.evidence || []} />
        </div>

        {/* Row 3: Order Flow + Alerts */}
        <div className="col-span-7 card">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Shadow Execution Flow</h2>
          <OrderFlow orders={orders} />
        </div>

        <div className="col-span-5 card">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Alert Feed</h2>
          <AlertFeed alerts={alerts} />
        </div>
      </div>

      {/* Footer */}
      <footer className="mt-4 text-center text-[10px] text-gray-700">
        Global Sentinel V5.1 | Shadow Mode | Last refresh: {lastRefresh.toLocaleTimeString()}
      </footer>
    </div>
  );
}
