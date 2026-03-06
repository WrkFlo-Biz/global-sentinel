"use client";

import { useEffect, useState, useCallback } from "react";
import { api, type PortfolioHistoryData } from "@/lib/api";
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

const PORTFOLIO_REFRESH_EVENT = "gs:portfolio-refresh";

function formatDate(ts: number): string {
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function formatFreshness(sourceTimestampUtc?: string, cacheStatus?: string, cacheAgeMs?: number): string {
  if (!sourceTimestampUtc) return "Waiting for first refresh";
  try {
    const ageSeconds = Math.max(0, Math.floor((Date.now() - new Date(sourceTimestampUtc).getTime()) / 1000));
    const ageLabel = ageSeconds < 5
      ? "Source updated just now"
      : ageSeconds < 60
        ? `Source updated ${ageSeconds}s ago`
        : `Source updated ${Math.floor(ageSeconds / 60)}m ago`;
    const cacheLabel = cacheStatus ? ` · cache ${cacheStatus}` : "";
    const ageMsLabel = typeof cacheAgeMs === "number" ? ` · age ${Math.round(cacheAgeMs)}ms` : "";
    return `${ageLabel}${cacheLabel}${ageMsLabel}`;
  } catch {
    return "Source freshness unavailable";
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
  const REFRESH_MS = 60000;
  const [data, setData] = useState<DrawdownPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sourceTimestampUtc, setSourceTimestampUtc] = useState<string | undefined>(undefined);
  const [cacheStatus, setCacheStatus] = useState<string | undefined>(undefined);
  const [cacheAgeMs, setCacheAgeMs] = useState<number | undefined>(undefined);
  const freshnessLabel = formatFreshness(sourceTimestampUtc, cacheStatus, cacheAgeMs);

  const fetchData = useCallback(async () => {
    try {
      // Try progressively shorter periods until we get data
      const periods = ["1A", "3M", "1M", "1W"];
      let hist: PortfolioHistoryData | null = null;
      for (const period of periods) {
        const h = await api.portfolioHistory(period, "1D", "all").catch(() => null);
        if (!h) continue;
        if (h.error) continue;
        if (h.timestamp && h.equity && h.equity.some((e: number) => e > 0)) {
          hist = h;
          break;
        }
      }

      if (!hist) { setError("No portfolio history available yet"); return; }

      let peak = 0;
      const points: DrawdownPoint[] = [];
      for (let i = 0; i < hist.timestamp.length; i++) {
        const eq = hist.equity[i];
        if (eq === 0 || eq === null || eq === undefined) continue;
        if (eq > peak) peak = eq;
        const dd = peak > 0 ? ((eq - peak) / peak) * 100 : 0;
        points.push({ timestamp: hist.timestamp[i], equity: eq, peak, drawdown_pct: dd });
      }
      setData(points);
      setError(null);
      setSourceTimestampUtc(hist.source_timestamp_utc || hist.latest_source_timestamp_utc || hist.timestamp_utc);
      setCacheStatus(hist.cache_status);
      setCacheAgeMs(hist.cache_age_ms);
    } catch (e: any) {
      setError(e.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, REFRESH_MS);
    const handlePortfolioRefresh = () => {
      fetchData();
    };
    window.addEventListener(PORTFOLIO_REFRESH_EVENT, handlePortfolioRefresh);
    return () => {
      clearInterval(interval);
      window.removeEventListener(PORTFOLIO_REFRESH_EVENT, handlePortfolioRefresh);
    };
  }, [fetchData]);

  useEffect(() => {
    const interval = setInterval(() => {
      setCacheAgeMs(prev => (typeof prev === "number" ? prev + 1000 : prev));
    }, 1000);
    return () => clearInterval(interval);
  }, []);

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
        <span className="text-[10px] text-gray-500">
          {freshnessLabel} · refresh {Math.round(REFRESH_MS / 1000)}s
        </span>
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
