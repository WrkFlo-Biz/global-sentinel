"use client";

import { useEffect, useState, useCallback } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";

interface DrawdownPoint {
  timestamp: number;
  equity: number;
  peak: number;
  drawdown_pct: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

function formatDate(ts: number): string {
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  } catch {
    return "";
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
  return (
    <div className="bg-[#1a1f2e] border border-[#2a3040] rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-400 mb-1">
        {new Date(d.timestamp * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Drawdown</span>
        <span className="text-red-400 font-bold">{d.drawdown_pct.toFixed(2)}%</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Equity</span>
        <span className="text-gray-200">${d.equity.toLocaleString()}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Peak</span>
        <span className="text-gray-400">${d.peak.toLocaleString()}</span>
      </div>
    </div>
  );
}

export default function DrawdownChart() {
  const [data, setData] = useState<DrawdownPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (API_KEY) headers["X-API-Key"] = API_KEY;
      const res = await fetch(`${API_BASE}/api/portfolio-history?period=1A&timeframe=1D`, { cache: "no-store", headers });
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      const hist = await res.json();
      if (hist.error) { setError(hist.error); return; }

      let peak = 0;
      const points: DrawdownPoint[] = [];
      for (let i = 0; i < hist.timestamp.length; i++) {
        const eq = hist.equity[i];
        if (eq === 0) continue;
        if (eq > peak) peak = eq;
        const dd = peak > 0 ? ((eq - peak) / peak) * 100 : 0;
        points.push({ timestamp: hist.timestamp[i], equity: eq, peak, drawdown_pct: dd });
      }
      setData(points);
      setError(null);
    } catch (e: any) {
      setError(e.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return <div className="flex items-center justify-center h-40 text-gray-600 text-xs">Loading drawdown...</div>;
  }

  if (error) {
    return <div className="text-gray-600 text-xs">{error}</div>;
  }

  if (!data.length) {
    return <div className="text-gray-600 text-xs">No drawdown data — waiting for portfolio history</div>;
  }

  const maxDD = Math.min(...data.map(d => d.drawdown_pct));
  const currentDD = data[data.length - 1].drawdown_pct;
  const atPeak = data.length <= 1 || maxDD === 0;
  const ddColor = maxDD < -10 ? "text-red-400" : maxDD < -5 ? "text-yellow-400" : "text-emerald-400";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between flex-wrap gap-2">
        {atPeak ? (
          <div className="flex items-center gap-2 text-[10px]">
            <span className="text-emerald-400 font-bold">No drawdown — account at peak</span>
            <span className="text-gray-500">Equity: ${data[data.length - 1].equity.toLocaleString()}</span>
          </div>
        ) : (
          <div className="flex items-center gap-3 text-[10px]">
            <span className="text-gray-500">Max Drawdown: <span className={`font-bold tabular-nums ${ddColor}`}>{maxDD.toFixed(2)}%</span></span>
            <span className="text-gray-500">Current: <span className="text-gray-300 font-bold tabular-nums">{currentDD.toFixed(2)}%</span></span>
          </div>
        )}
      </div>

      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="timestamp"
            tickFormatter={formatDate}
            tick={{ fill: "#6b7280", fontSize: 9 }}
            stroke="#1f2937"
          />
          <YAxis
            domain={[Math.floor(maxDD - 2), 0]}
            tickFormatter={(v) => `${v}%`}
            tick={{ fill: "#6b7280", fontSize: 9 }}
            stroke="#1f2937"
            width={40}
          />
          <Tooltip content={<ChartTooltip />} />
          <ReferenceLine y={0} stroke="#4b5563" strokeWidth={1} />
          <ReferenceLine y={-5} stroke="#f59e0b" strokeDasharray="4 4" strokeOpacity={0.4} />
          <ReferenceLine y={-10} stroke="#ef4444" strokeDasharray="4 4" strokeOpacity={0.4} />
          <Area
            type="monotone"
            dataKey="drawdown_pct"
            stroke="#ef4444"
            strokeWidth={2}
            fill="url(#ddGrad)"
            dot={false}
            activeDot={{ r: 3, fill: "#ef4444" }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
