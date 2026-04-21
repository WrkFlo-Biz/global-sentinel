"use client";

import { useEffect, useState, useCallback } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ReferenceArea,
} from "recharts";
import { api, type GSSTimelinePoint, type GSSLatest } from "@/lib/api";

// Actionable trade correlations for each GSS signal state
const SIGNAL_ACTIONS: Record<string, { label: string; tickers: string[]; rationale: string; bias: "bull" | "bear" | "neutral" | "hedge" }[]> = {
  BLACK_SWAN_SHIELD: [
    { label: "Hedge equities", tickers: ["UVXY", "VXX", "SQQQ"], rationale: "Z>2.5 + narrative spike → vol expansion likely", bias: "hedge" },
    { label: "Safe haven flow", tickers: ["GLD", "TLT", "BIL"], rationale: "Field coherence breakdown → risk-off rotation", bias: "bear" },
    { label: "Reduce exposure", tickers: ["SPY", "QQQ"], rationale: "Exit or trim longs until Z normalizes <1.5", bias: "neutral" },
  ],
  EMERGENCY_DELEVERAGE: [
    { label: "Exit risk now", tickers: ["SPY", "QQQ", "IWM"], rationale: "Deleverage signal → forced selling cascade imminent", bias: "bear" },
    { label: "Treasury flight", tickers: ["TLT", "IEF", "BIL"], rationale: "Duration demand spikes during deleverage events", bias: "bull" },
    { label: "Short momentum", tickers: ["SQQQ", "SDS"], rationale: "Momentum reversal typically follows deleverage", bias: "bear" },
  ],
  GAMMA_SQUEEZE: [
    { label: "Momentum longs", tickers: ["QQQ", "SPY", "TQQQ"], rationale: "Dealer gamma hedging amplifies upside moves", bias: "bull" },
    { label: "Tech squeeze play", tickers: ["NVDA", "TSLA", "MSFT"], rationale: "Gamma squeeze strongest in high-OI mega-caps", bias: "bull" },
    { label: "Short vol fade", tickers: ["SVXY", "ZROZ"], rationale: "Vol compression accelerates during gamma squeeze", bias: "bull" },
  ],
  PRE_PULSE: [
    { label: "Small probe longs", tickers: ["SPY", "QQQ"], rationale: "Pre-pulse: signal building, position small (25-50% normal size)", bias: "bull" },
    { label: "Watch energy", tickers: ["XLE", "OIH", "USO"], rationale: "Pre-pulse often precedes commodity-driven regime shift", bias: "neutral" },
    { label: "Trail stops tight", tickers: ["*ALL*"], rationale: "Uncertain direction — reduce drawdown exposure", bias: "neutral" },
  ],
  NOISE_FILTER: [
    { label: "Stay flat", tickers: ["BIL", "SGOV"], rationale: "Signal incoherent — cash preserves optionality", bias: "neutral" },
    { label: "No new entries", tickers: [], rationale: "Wait for Z-score to break above 1.0 or below -0.5", bias: "neutral" },
  ],
  NEUTRAL: [
    { label: "Normal sizing", tickers: [], rationale: "No regime stress — follow primary strategy", bias: "neutral" },
    { label: "Geopolitical hedge", tickers: ["GLD", "XLE"], rationale: "Low-cost hedge given current macro backdrop", bias: "neutral" },
  ],
};

const BIAS_COLORS: Record<string, string> = {
  bull: "text-emerald-400",
  bear: "text-red-400",
  hedge: "text-yellow-400",
  neutral: "text-gray-400",
};

const BIAS_BG: Record<string, string> = {
  bull: "border-emerald-900/40 bg-emerald-950/20",
  bear: "border-red-900/40 bg-red-950/20",
  hedge: "border-yellow-900/40 bg-yellow-950/20",
  neutral: "border-gray-800/40 bg-gray-900/20",
};

const SIGNAL_COLORS: Record<string, string> = {
  BLACK_SWAN_SHIELD: "#ef4444",
  GAMMA_SQUEEZE: "#f59e0b",
  NOISE_FILTER: "#3b82f6",
  PRE_PULSE: "#a855f7",
  NEUTRAL: "#6b7280",
  EMERGENCY_DELEVERAGE: "#dc2626",
  UNAVAILABLE: "#4b5563",
  NO_DATA: "#4b5563",
};

const SIGNAL_LABELS: Record<string, string> = {
  BLACK_SWAN_SHIELD: "BLACK SWAN",
  GAMMA_SQUEEZE: "GAMMA SQUEEZE",
  NOISE_FILTER: "NOISE FILTER",
  PRE_PULSE: "PRE-PULSE",
  NEUTRAL: "NEUTRAL",
  EMERGENCY_DELEVERAGE: "DELEVERAGE",
};

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return ts;
  }
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: any[];
}

function ChartTooltip({ active, payload }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  const signalColor = SIGNAL_COLORS[d.gss_signal] || "#6b7280";
  return (
    <div className="bg-[#1a1f2e] border border-[#2a3040] rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-400 mb-1">{formatTime(d.timestamp_utc)}</div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Z-Score</span>
        <span className="text-cyan-400 font-bold">{d.z_score?.toFixed(2) ?? "—"}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Narrative</span>
        <span className="text-orange-400 font-bold">{d.narrative_velocity?.toFixed(2) ?? "—"}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">VIX</span>
        <span className="text-yellow-400 font-bold">{d.vix?.toFixed(1) ?? "—"}</span>
      </div>
      <div className="flex justify-between gap-4 mt-1 pt-1 border-t border-[#2a3040]">
        <span className="text-gray-400">Signal</span>
        <span className="font-bold" style={{ color: signalColor }}>
          {SIGNAL_LABELS[d.gss_signal] || d.gss_signal}
        </span>
      </div>
    </div>
  );
}

export default function GSSSignalGraph() {
  const [timeline, setTimeline] = useState<GSSTimelinePoint[]>([]);
  const [latest, setLatest] = useState<GSSLatest | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const [tl, lat] = await Promise.all([
        api.gssTimeline(100).catch(() => []),
        api.gssLatest().catch(() => null),
      ]);
      if (Array.isArray(tl) && tl.length > 0) setTimeline(tl);
      if (lat) setLatest(lat);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000); // refresh every 30s
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-600 text-xs">
        Loading GSS signal data...
      </div>
    );
  }

  const signalColor = latest?.gss_signal
    ? SIGNAL_COLORS[latest.gss_signal] || "#6b7280"
    : "#6b7280";
  const signalLabel = latest?.gss_signal
    ? SIGNAL_LABELS[latest.gss_signal] || latest.gss_signal
    : "NO DATA";

  return (
    <div className="space-y-3">
      {/* Current Signal Badge */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full animate-pulse" style={{ backgroundColor: signalColor }} />
          <span
            className="text-xs font-mono font-bold px-2 py-0.5 rounded"
            style={{ color: signalColor, backgroundColor: `${signalColor}15`, border: `1px solid ${signalColor}30` }}
          >
            {signalLabel}
          </span>
          {latest?.confidence != null && (
            <span className="text-[10px] text-gray-500">{(latest.confidence * 100).toFixed(0)}% conf</span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[10px] text-gray-500">
          {latest?.action && (
            <span className="font-mono" style={{ color: signalColor }}>{latest.action}</span>
          )}
        </div>
      </div>

      {/* Three-Layer Data Summary */}
      {latest?.field_data && (
        <div className="grid grid-cols-3 gap-2 text-center">
          <div>
            <div className="text-[9px] text-gray-500 uppercase">Field (Z)</div>
            <div className="text-sm font-mono text-cyan-400">{latest.field_data.z_score?.toFixed(2) ?? "—"}</div>
            <div className="text-[9px] text-gray-600">{latest.field_data.coherence_level}</div>
          </div>
          <div>
            <div className="text-[9px] text-gray-500 uppercase">Narrative</div>
            <div className="text-sm font-mono text-orange-400">{latest.narrative_data?.velocity?.toFixed(2) ?? "—"}</div>
            <div className="text-[9px] text-gray-600">{latest.narrative_data?.dominant_narrative || "—"}</div>
          </div>
          <div>
            <div className="text-[9px] text-gray-500 uppercase">VIX</div>
            <div className="text-sm font-mono text-yellow-400">{latest.execution_data?.vix?.toFixed(1) ?? "—"}</div>
            <div className="text-[9px] text-gray-600">P/C {latest.execution_data?.put_call_ratio?.toFixed(2) ?? "—"}</div>
          </div>
        </div>
      )}

      {/* Real-time Graph */}
      {timeline.length > 0 ? (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={timeline} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="timestamp_utc"
              tickFormatter={formatTime}
              tick={{ fill: "#6b7280", fontSize: 9 }}
              stroke="#1f2937"
            />
            <YAxis
              yAxisId="z"
              domain={[0, 5]}
              ticks={[0, 1, 2, 2.5, 3, 4]}
              tick={{ fill: "#6b7280", fontSize: 9 }}
              stroke="#1f2937"
              width={25}
            />
            <YAxis
              yAxisId="vix"
              orientation="right"
              domain={[10, 80]}
              tick={{ fill: "#6b7280", fontSize: 9 }}
              stroke="#1f2937"
              width={30}
            />
            <Tooltip content={<ChartTooltip />} />

            {/* Threshold zones */}
            <ReferenceArea yAxisId="z" y1={2.5} y2={5} fill="#ef4444" fillOpacity={0.05} />
            <ReferenceLine yAxisId="z" y={2.5} stroke="#ef4444" strokeDasharray="4 4" strokeOpacity={0.4} label={{ value: "Z=2.5", fill: "#ef4444", fontSize: 8, position: "right" }} />
            <ReferenceLine yAxisId="z" y={1.2} stroke="#f59e0b" strokeDasharray="4 4" strokeOpacity={0.3} />

            {/* Z-Score (Field Layer) */}
            <Line
              yAxisId="z"
              type="monotone"
              dataKey="z_score"
              stroke="#06b6d4"
              strokeWidth={2}
              dot={false}
              name="Z-Score"
            />
            {/* Narrative Velocity */}
            <Line
              yAxisId="z"
              type="monotone"
              dataKey="narrative_velocity"
              stroke="#f97316"
              strokeWidth={1.5}
              dot={false}
              name="Narrative"
              strokeDasharray="5 3"
            />
            {/* VIX */}
            <Line
              yAxisId="vix"
              type="monotone"
              dataKey="vix"
              stroke="#eab308"
              strokeWidth={1}
              dot={false}
              name="VIX"
              strokeDasharray="3 3"
              opacity={0.6}
            />
          </LineChart>
        </ResponsiveContainer>
      ) : (
        <div className="flex items-center justify-center h-40 text-gray-600 text-xs">
          Waiting for GSS signal data...
        </div>
      )}

      {/* Legend */}
      <div className="flex items-center gap-3 text-[9px] text-gray-500">
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-cyan-500 inline-block" /> Z-Score
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-orange-500 inline-block" style={{ borderTop: "1px dashed" }} /> Narrative
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-0.5 bg-yellow-500 inline-block opacity-60" style={{ borderTop: "1px dashed" }} /> VIX
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-1 bg-red-500/10 inline-block border-t border-red-500/40 border-dashed" /> Shield Zone
        </span>
      </div>

      {/* Reason (truncated) */}
      {latest?.reason && (
        <div className="text-[10px] text-gray-400 leading-tight line-clamp-2" title={latest.reason}>
          {latest.reason}
        </div>
      )}

      {/* Actionable Correlations */}
      {latest?.gss_signal && SIGNAL_ACTIONS[latest.gss_signal] && (
        <div className="pt-2 border-t border-[#1f2937]">
          <div className="text-[9px] text-gray-500 uppercase tracking-wider mb-1.5">Correlated Actions</div>
          <div className="space-y-1">
            {SIGNAL_ACTIONS[latest.gss_signal].map((a, i) => (
              <div key={i} className={`flex items-start gap-2 rounded px-2 py-1.5 border text-[10px] ${BIAS_BG[a.bias]}`}>
                <span className={`font-semibold flex-shrink-0 ${BIAS_COLORS[a.bias]}`}>{a.label}</span>
                {a.tickers.length > 0 && (
                  <span className="text-gray-400 font-mono flex-shrink-0">{a.tickers.join(" · ")}</span>
                )}
                <span className="text-gray-600 text-[9px] leading-tight">{a.rationale}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
