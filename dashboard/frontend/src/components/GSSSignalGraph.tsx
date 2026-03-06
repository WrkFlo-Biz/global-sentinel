"use client";

import { useEffect, useState, useCallback } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, ReferenceArea,
} from "recharts";
import { api, type GSSTimelinePoint, type GSSLatest } from "@/lib/api";

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
    </div>
  );
}
