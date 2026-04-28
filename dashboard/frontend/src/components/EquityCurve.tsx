"use client";

import { useEffect, useState, useCallback } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";

interface EquityPoint {
  timestamp: number;
  equity: number;
  profit_loss: number;
  profit_loss_pct: number;
  base_value: number;
}

interface PortfolioHistory {
  timestamp: number[];
  equity: number[];
  profit_loss: number[];
  profit_loss_pct: number[];
  base_value: number;
  timeframe: string;
  error?: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

// Period config: label, Alpaca period param, Alpaca timeframe param, isIntraday
const PERIODS: { label: string; period: string; timeframe: string; intraday: boolean }[] = [
  { label: "1H", period: "1D", timeframe: "1H", intraday: true },
  { label: "6H", period: "1D", timeframe: "1H", intraday: true },
  { label: "12H", period: "1D", timeframe: "1H", intraday: true },
  { label: "1D", period: "1D", timeframe: "1H", intraday: true },
  { label: "1W", period: "1W", timeframe: "1D", intraday: false },
  { label: "1M", period: "1M", timeframe: "1D", intraday: false },
  { label: "3M", period: "3M", timeframe: "1D", intraday: false },
  { label: "1A", period: "1A", timeframe: "1D", intraday: false },
];

function formatTick(ts: number, intraday: boolean): string {
  try {
    const d = new Date(ts * 1000);
    if (intraday) {
      return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
    }
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function formatUSD(val: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(val);
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: any[];
}

function ChartTooltip({ active, payload }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  const plColor = d.profit_loss >= 0 ? "#10b981" : "#ef4444";
  return (
    <div className="bg-[#1a1f2e] border border-[#2a3040] rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-400 mb-1">
        {new Date(d.timestamp * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
        {" "}
        {new Date(d.timestamp * 1000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })}
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Equity</span>
        <span className="text-white font-bold">{formatUSD(d.equity)}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">P&L</span>
        <span className="font-bold" style={{ color: plColor }}>
          {d.profit_loss >= 0 ? "+" : ""}{formatUSD(d.profit_loss)}
        </span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Return</span>
        <span className="font-bold" style={{ color: plColor }}>
          {d.profit_loss_pct >= 0 ? "+" : ""}{(d.profit_loss_pct * 100).toFixed(2)}%
        </span>
      </div>
    </div>
  );
}

export default function EquityCurve() {
  const [data, setData] = useState<EquityPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedLabel, setSelectedLabel] = useState<string>("1M");
  const [error, setError] = useState<string | null>(null);

  const selected = PERIODS.find(p => p.label === selectedLabel) || PERIODS[5];

  const fetchHistory = useCallback(async () => {
    try {
      const headers: Record<string, string> = {};
      if (API_KEY) headers["X-API-Key"] = API_KEY;
      const res = await fetch(`${API_BASE}/api/portfolio-history?period=${selected.period}&timeframe=${selected.timeframe}`, { cache: "no-store", headers });
      if (!res.ok) throw new Error(`API error: ${res.status}`);
      const hist: PortfolioHistory = await res.json();
      if (hist.error) {
        setError(hist.error);
        return;
      }
      const points: EquityPoint[] = [];
      for (let i = 0; i < hist.timestamp.length; i++) {
        points.push({
          timestamp: hist.timestamp[i],
          equity: hist.equity[i],
          profit_loss: hist.profit_loss[i],
          profit_loss_pct: hist.profit_loss_pct[i],
          base_value: hist.base_value,
        });
      }
      setData(points);
      setError(null);
    } catch (e: any) {
      setError(e.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    setLoading(true);
    fetchHistory();
  }, [fetchHistory]);

  if (loading) {
    return <div className="flex items-center justify-center h-48 text-gray-600 text-xs">Loading equity curve...</div>;
  }

  if (error || !data.length) {
    return <div className="text-gray-600 text-xs">{error || "No portfolio history available"}</div>;
  }

  // For sub-day views, filter to last N hours from the data
  let displayData = data;
  if (selectedLabel === "1H" && data.length > 1) {
    const cutoff = data[data.length - 1].timestamp - 3600;
    displayData = data.filter(d => d.timestamp >= cutoff);
  } else if (selectedLabel === "6H" && data.length > 6) {
    const cutoff = data[data.length - 1].timestamp - 6 * 3600;
    displayData = data.filter(d => d.timestamp >= cutoff);
  } else if (selectedLabel === "12H" && data.length > 12) {
    const cutoff = data[data.length - 1].timestamp - 12 * 3600;
    displayData = data.filter(d => d.timestamp >= cutoff);
  }
  if (displayData.length === 0) displayData = data;

  const latest = displayData[displayData.length - 1];
  const first = displayData[0];
  const totalReturn = latest.equity - first.equity;
  const totalReturnPct = first.equity > 0 ? ((latest.equity - first.equity) / first.equity) * 100 : 0;
  const returnColor = totalReturn >= 0 ? "text-emerald-400" : "text-red-400";
  const gradientColor = totalReturn >= 0 ? "#10b981" : "#ef4444";
  const strokeColor = totalReturn >= 0 ? "#10b981" : "#ef4444";

  const minEquity = Math.min(...displayData.map(d => d.equity));
  const maxEquity = Math.max(...displayData.map(d => d.equity));
  const padding = (maxEquity - minEquity) * 0.1 || 1000;

  return (
    <div className="space-y-2">
      {/* Summary stats */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          <span className="text-base sm:text-lg font-bold text-gray-200 tabular-nums">{formatUSD(latest.equity)}</span>
          <span className={`text-xs sm:text-sm font-bold tabular-nums ${returnColor}`}>
            {totalReturn >= 0 ? "+" : ""}{formatUSD(totalReturn)} ({totalReturnPct >= 0 ? "+" : ""}{totalReturnPct.toFixed(2)}%)
          </span>
        </div>
        <div className="flex gap-1 flex-wrap">
          {PERIODS.map((p) => (
            <button
              key={p.label}
              onClick={() => setSelectedLabel(p.label)}
              className={`px-1.5 sm:px-2 py-0.5 text-[9px] sm:text-[10px] rounded transition min-w-[28px] ${
                selectedLabel === p.label
                  ? "bg-blue-600 text-white"
                  : "bg-[#1a1f2e] text-gray-500 hover:text-gray-300"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={displayData} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={gradientColor} stopOpacity={0.25} />
              <stop offset="95%" stopColor={gradientColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="timestamp"
            tickFormatter={(ts) => formatTick(ts, selected.intraday)}
            tick={{ fill: "#6b7280", fontSize: 9 }}
            stroke="#1f2937"
          />
          <YAxis
            domain={[minEquity - padding, maxEquity + padding]}
            tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
            tick={{ fill: "#6b7280", fontSize: 9 }}
            stroke="#1f2937"
            width={40}
          />
          <Tooltip content={<ChartTooltip />} />
          <ReferenceLine
            y={first.equity}
            stroke="#4b5563"
            strokeDasharray="4 4"
            strokeOpacity={0.5}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke={strokeColor}
            strokeWidth={2}
            fill="url(#equityGrad)"
            dot={false}
            activeDot={{ r: 3, fill: strokeColor }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
