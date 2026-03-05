"use client";

import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";
import type { TimelinePoint } from "@/lib/api";

function formatTime(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return ts;
  }
}

function formatDate(ts: string): string {
  try {
    const d = new Date(ts);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  } catch {
    return ts;
  }
}

const MODE_COLORS: Record<string, string> = {
  NORMAL: "#10b981",
  ELEVATED: "#f59e0b",
  CRISIS: "#ef4444",
  MANUAL_REVIEW: "#f97316",
};

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
  label?: string;
}

function ChartTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div className="bg-[#1a1f2e] border border-[#2a3040] rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-400 mb-1">{formatDate(d.timestamp_utc)} {formatTime(d.timestamp_utc)}</div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Regime P</span>
        <span className="text-white font-bold">{(d.regime_p * 100).toFixed(1)}%</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Confidence</span>
        <span className="text-gray-300">{(d.confidence * 100).toFixed(1)}%</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Mode</span>
        <span style={{ color: MODE_COLORS[d.mode] || "#9ca3af" }}>{d.mode}</span>
      </div>
    </div>
  );
}

export default function RegimeChart({ data }: { data: TimelinePoint[] }) {
  if (!data.length) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-600 text-sm">
        Waiting for scorecard data...
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="regimeGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="confGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.15} />
            <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis
          dataKey="timestamp_utc"
          tickFormatter={formatTime}
          tick={{ fill: "#6b7280", fontSize: 10 }}
          stroke="#1f2937"
        />
        <YAxis
          domain={[0, 1]}
          ticks={[0, 0.25, 0.45, 0.75, 1.0]}
          tick={{ fill: "#6b7280", fontSize: 10 }}
          stroke="#1f2937"
          width={35}
        />
        <Tooltip content={<ChartTooltip />} />
        {/* Threshold lines */}
        <ReferenceLine y={0.45} stroke="#f59e0b" strokeDasharray="6 3" strokeOpacity={0.5} />
        <ReferenceLine y={0.75} stroke="#ef4444" strokeDasharray="6 3" strokeOpacity={0.5} />
        {/* Confidence area */}
        <Area
          type="monotone"
          dataKey="confidence"
          stroke="#8b5cf6"
          strokeWidth={1}
          fill="url(#confGrad)"
          dot={false}
        />
        {/* Regime probability area */}
        <Area
          type="monotone"
          dataKey="regime_p"
          stroke="#3b82f6"
          strokeWidth={2}
          fill="url(#regimeGrad)"
          dot={false}
          activeDot={{ r: 4, fill: "#3b82f6" }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
