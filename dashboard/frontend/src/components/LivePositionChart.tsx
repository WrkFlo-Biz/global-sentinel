"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from "recharts";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

interface PricePoint {
  time: number;
  price: number;
  bid: number;
  ask: number;
}

interface PositionData {
  symbol: string;
  underlying: string;
  is_option: boolean;
  strike: number | null;
  opt_type: string | null;
  expiry: string | null;
  qty: number;
  entry_price: number;
  current_price: number;
  bid: number;
  ask: number;
  pnl: number;
  pnl_pct: number;
  market_value: number;
  source: string;
  account: string;
  history: PricePoint[];
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: any[];
  entryPrice?: number;
}

function ChartTooltip({ active, payload, entryPrice }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  const plFromEntry = entryPrice ? d.price - entryPrice : 0;
  const plPct = entryPrice ? ((d.price / entryPrice) - 1) * 100 : 0;
  const plColor = plFromEntry >= 0 ? "#10b981" : "#ef4444";
  return (
    <div className="bg-[#1a1f2e] border border-[#2a3040] rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-400 mb-1">
        {new Date(d.time * 1000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true })}
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Price</span>
        <span className="text-white font-bold">${d.price.toFixed(d.price < 1 ? 4 : 2)}</span>
      </div>
      {d.bid > 0 && (
        <div className="flex justify-between gap-4">
          <span className="text-gray-400">Bid/Ask</span>
          <span className="text-gray-300">${d.bid.toFixed(2)} / ${d.ask.toFixed(2)}</span>
        </div>
      )}
      {entryPrice ? (
        <div className="flex justify-between gap-4">
          <span className="text-gray-400">vs Entry</span>
          <span className="font-bold" style={{ color: plColor }}>
            {plFromEntry >= 0 ? "+" : ""}{plFromEntry.toFixed(d.price < 1 ? 4 : 2)} ({plPct >= 0 ? "+" : ""}{plPct.toFixed(1)}%)
          </span>
        </div>
      ) : null}
    </div>
  );
}

async function fetchPositionPrices(): Promise<PositionData[]> {
  const headers: Record<string, string> = {};
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  try {
    const res = await fetch(`${API_BASE}/api/position-prices`, { cache: "no-store", headers });
    if (!res.ok) return [];
    const data = await res.json();
    return data.positions || [];
  } catch {
    return [];
  }
}

export default function LivePositionChart() {
  const [positions, setPositions] = useState<PositionData[]>([]);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  // Accumulate history client-side across polls (backend history resets on restart)
  const localHistoryRef = useRef<Record<string, PricePoint[]>>({});

  const load = useCallback(async () => {
    const pos = await fetchPositionPrices();
    // Merge server history into local accumulator
    for (const p of pos) {
      if (!localHistoryRef.current[p.symbol]) {
        localHistoryRef.current[p.symbol] = [];
      }
      const local = localHistoryRef.current[p.symbol];
      const serverHistory = p.history || [];
      // Append new points from server that we don't have locally
      const lastLocalTime = local.length > 0 ? local[local.length - 1].time : 0;
      for (const pt of serverHistory) {
        if (pt.time > lastLocalTime) {
          local.push(pt);
        }
      }
      // Also append current price if newer
      if (p.current_price > 0) {
        const now = Math.floor(Date.now() / 1000);
        if (local.length === 0 || now - local[local.length - 1].time >= 10) {
          local.push({ time: now, price: p.current_price, bid: p.bid, ask: p.ask });
        }
      }
      // Cap at 2000 points
      if (local.length > 2000) {
        localHistoryRef.current[p.symbol] = local.slice(-2000);
      }
    }
    setPositions(pos);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 15000); // poll every 15s
    return () => clearInterval(iv);
  }, [load]);

  if (loading && positions.length === 0) {
    return <div className="flex items-center justify-center h-48 text-gray-600 text-xs">Loading positions...</div>;
  }

  if (positions.length === 0) {
    return <div className="text-gray-600 text-xs">No open positions to track</div>;
  }

  const pos = positions[selectedIdx] || positions[0];
  const history = localHistoryRef.current[pos.symbol] || pos.history || [];
  const entry = pos.entry_price;
  const isUp = pos.pnl >= 0;
  const strokeColor = isUp ? "#10b981" : "#ef4444";

  // Format helpers
  const fmtPrice = (v: number) => v < 1 ? `$${v.toFixed(4)}` : v < 10 ? `$${v.toFixed(2)}` : `$${v.toFixed(2)}`;
  const fmtPnl = (v: number) => v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;

  // Chart domain
  const prices = history.map(h => h.price).filter(p => p > 0);
  if (entry > 0) prices.push(entry);
  const minP = prices.length > 0 ? Math.min(...prices) : 0;
  const maxP = prices.length > 0 ? Math.max(...prices) : 1;
  const pad = (maxP - minP) * 0.12 || 0.01;

  // Label for position
  const posLabel = pos.is_option
    ? `${pos.underlying} $${pos.strike} ${pos.opt_type} ${pos.expiry ? pos.expiry.slice(5) : ""}`
    : pos.symbol;

  return (
    <div className="space-y-2">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1">
        <div className="flex items-center gap-2 flex-wrap">
          {positions.length > 1 ? (
            <select
              value={selectedIdx}
              onChange={(e) => setSelectedIdx(Number(e.target.value))}
              className="bg-[#1a1f2e] text-gray-200 text-xs font-bold border border-[#2a3040] rounded px-2 py-0.5"
            >
              {positions.map((p, i) => (
                <option key={p.symbol} value={i}>
                  {p.is_option ? `${p.underlying} $${p.strike} ${p.opt_type}` : p.symbol}
                  {` (${p.account})`}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-sm font-bold text-gray-200">{posLabel}</span>
          )}
          <span className="text-sm text-gray-300 tabular-nums">{fmtPrice(pos.current_price)}</span>
          <span className={`text-xs font-bold tabular-nums ${isUp ? "text-emerald-400" : "text-red-400"}`}>
            {fmtPnl(pos.pnl)} ({pos.pnl_pct >= 0 ? "+" : ""}{pos.pnl_pct.toFixed(1)}%)
          </span>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-gray-500">
          {pos.bid > 0 && <span>Bid ${pos.bid.toFixed(2)} / Ask ${pos.ask.toFixed(2)}</span>}
          <span>{history.length} pts</span>
        </div>
      </div>

      {/* Position detail strip */}
      <div className="flex items-center gap-3 text-[10px] text-gray-400 bg-[#0d1117] rounded px-2 py-1 border border-[#1a1f2e]">
        <span className="text-gray-300 font-medium">{pos.symbol}</span>
        <span>Qty: {pos.qty}</span>
        <span>Entry: {fmtPrice(entry)}</span>
        <span>Now: {fmtPrice(pos.current_price)}</span>
        <span>Val: ${pos.market_value.toFixed(2)}</span>
        <span className={isUp ? "text-emerald-400 font-bold" : "text-red-400 font-bold"}>
          P/L: {fmtPnl(pos.pnl)} ({pos.pnl_pct >= 0 ? "+" : ""}{pos.pnl_pct.toFixed(1)}%)
        </span>
        <span className="text-gray-600">{pos.source}</span>
      </div>

      {/* Chart */}
      {history.length < 2 ? (
        <div className="flex items-center justify-center h-40 text-gray-600 text-xs">
          Accumulating price data... ({history.length} point{history.length !== 1 ? "s" : ""})
          <br />Chart will appear after a few polling cycles (~30s)
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={history} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="posGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={strokeColor} stopOpacity={0.25} />
                <stop offset="95%" stopColor={strokeColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="time"
              tickFormatter={(ts) => {
                try {
                  return new Date(ts * 1000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false });
                } catch { return ""; }
              }}
              tick={{ fill: "#6b7280", fontSize: 9 }}
              stroke="#1f2937"
            />
            <YAxis
              domain={[minP - pad, maxP + pad]}
              tickFormatter={(v) => `$${v < 1 ? v.toFixed(2) : v.toFixed(2)}`}
              tick={{ fill: "#6b7280", fontSize: 9 }}
              stroke="#1f2937"
              width={50}
            />
            <Tooltip content={<ChartTooltip entryPrice={entry} />} />
            {/* Entry price reference line */}
            {entry > 0 && (
              <ReferenceLine
                y={entry}
                stroke="#f59e0b"
                strokeDasharray="6 3"
                strokeWidth={1.5}
                label={{ value: `Entry $${entry < 1 ? entry.toFixed(2) : entry.toFixed(2)}`, position: "right", fill: "#f59e0b", fontSize: 9 }}
              />
            )}
            <Area
              type="monotone"
              dataKey="price"
              stroke={strokeColor}
              strokeWidth={2}
              fill="url(#posGrad)"
              dot={false}
              activeDot={{ r: 3, fill: strokeColor }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
