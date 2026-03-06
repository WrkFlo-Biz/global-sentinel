"use client";

import type { PerformanceData, PortfolioData } from "@/lib/api";

function formatUSD(val: number): string {
  const sign = val >= 0 ? "+" : "";
  return `${sign}$${Math.abs(val).toFixed(2)}`;
}

export default function PnLWaterfall({ data, portfolio }: { data: PerformanceData | null; portfolio?: PortfolioData | null }) {
  let entries: { symbol: string; pnl: number; trades: number; wins: number }[];
  let isOpen = false;
  let totalPnl = 0;
  let winsCount = 0;
  let lossesCount = 0;
  let profitFactor: number | null = null;

  if (data && !data.error && data.total_trades > 0 && Object.keys(data.by_symbol).length > 0) {
    entries = Object.entries(data.by_symbol)
      .map(([symbol, d]) => ({ symbol, pnl: d.pnl, trades: d.trades, wins: d.wins }))
      .sort((a, b) => b.pnl - a.pnl);
    totalPnl = data.total_pnl;
    winsCount = data.wins;
    lossesCount = data.losses;
    profitFactor = data.profit_factor;
  } else if (portfolio && portfolio.positions && portfolio.positions.length > 0) {
    isOpen = true;
    entries = portfolio.positions
      .map((p) => ({ symbol: p.symbol, pnl: p.unrealized_pl, trades: 1, wins: p.unrealized_pl >= 0 ? 1 : 0 }))
      .sort((a, b) => b.pnl - a.pnl);
    totalPnl = entries.reduce((sum, e) => sum + e.pnl, 0);
    winsCount = entries.filter(e => e.pnl >= 0).length;
    lossesCount = entries.length - winsCount;
  } else {
    return <div className="text-gray-600 text-xs">No trade data yet</div>;
  }

  if (!entries.length) {
    return <div className="text-gray-600 text-xs">No per-symbol data available</div>;
  }

  // Build waterfall: running cumulative
  let cumulative = 0;
  const bars = entries.map((e) => {
    const start = cumulative;
    cumulative += e.pnl;
    return { ...e, start, end: cumulative };
  });

  const allValues = bars.flatMap(b => [b.start, b.end]);
  const minVal = Math.min(0, ...allValues);
  const maxVal = Math.max(0, ...allValues);
  const range = maxVal - minVal || 1;

  const chartHeight = 180;
  const barWidth = Math.min(40, Math.max(16, Math.floor(300 / entries.length)));
  const chartWidth = Math.max(entries.length * (barWidth + 6) + 60, 300);

  // Map value to Y pixel (top = maxVal, bottom = minVal)
  const toY = (val: number) => {
    return ((maxVal - val) / range) * (chartHeight - 40) + 20;
  };

  const zeroY = toY(0);

  return (
    <div className="space-y-2">
      {/* Summary */}
      <div className="flex items-center justify-between text-[10px]">
        <div className="flex items-center gap-3">
          {isOpen && (
            <span className="text-yellow-500 bg-yellow-950/20 px-1.5 py-0.5 rounded border border-yellow-900/30">
              UNREALIZED
            </span>
          )}
          <span className={`font-bold tabular-nums ${totalPnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            Net: {formatUSD(totalPnl)}
          </span>
          <span className="text-gray-500">
            {winsCount}W / {lossesCount}L across {entries.length} symbols
          </span>
        </div>
        {profitFactor !== null && (
          <span className="text-gray-500">
            PF: <span className="text-gray-300 tabular-nums">{profitFactor.toFixed(2)}</span>
          </span>
        )}
      </div>

      {/* Waterfall chart */}
      <div className="overflow-x-auto">
        <svg width={chartWidth} height={chartHeight + 30} className="font-mono">
          {/* Zero line */}
          <line x1="40" y1={zeroY} x2={chartWidth} y2={zeroY} stroke="#4b5563" strokeWidth="1" strokeDasharray="4 2" />

          {/* Y-axis labels */}
          {[minVal, 0, maxVal].map((val) => {
            const y = toY(val);
            return (
              <text key={val} x="36" y={y + 3} textAnchor="end" fill="#6b7280" fontSize="8">
                {val >= 0 ? "+" : ""}{val >= 1000 || val <= -1000 ? `${(val / 1000).toFixed(1)}k` : val.toFixed(0)}
              </text>
            );
          })}

          {/* Bars + connectors */}
          {bars.map((bar, i) => {
            const x = 44 + i * (barWidth + 6);
            const isPositive = bar.pnl >= 0;
            const top = toY(Math.max(bar.start, bar.end));
            const bottom = toY(Math.min(bar.start, bar.end));
            const height = Math.max(bottom - top, 1);
            const fill = isPositive ? "#10b981" : "#ef4444";
            const fillOpacity = 0.8;

            // Connector line from previous bar end to this bar start
            const connectorY = toY(bar.start);

            return (
              <g key={bar.symbol}>
                {/* Connector from prev bar */}
                {i > 0 && (
                  <line
                    x1={44 + (i - 1) * (barWidth + 6) + barWidth}
                    y1={connectorY}
                    x2={x}
                    y2={connectorY}
                    stroke="#4b556340"
                    strokeWidth="1"
                    strokeDasharray="2 2"
                  />
                )}

                {/* Bar */}
                <rect
                  x={x}
                  y={top}
                  width={barWidth}
                  height={height}
                  fill={fill}
                  fillOpacity={fillOpacity}
                  rx="2"
                />

                {/* Value label on bar */}
                <text
                  x={x + barWidth / 2}
                  y={isPositive ? top - 3 : bottom + 10}
                  textAnchor="middle"
                  fill={fill}
                  fontSize="7"
                  fontWeight="bold"
                >
                  {bar.pnl >= 0 ? "+" : ""}{Math.abs(bar.pnl) >= 1000 ? `${(bar.pnl / 1000).toFixed(1)}k` : bar.pnl.toFixed(0)}
                </text>

                {/* Symbol label */}
                <text
                  x={x + barWidth / 2}
                  y={chartHeight + 12}
                  textAnchor="middle"
                  fill="#9ca3af"
                  fontSize="8"
                  transform={entries.length > 8 ? `rotate(-45 ${x + barWidth / 2} ${chartHeight + 12})` : undefined}
                >
                  {bar.symbol}
                </text>

                {/* Trade count */}
                <text
                  x={x + barWidth / 2}
                  y={chartHeight + 24}
                  textAnchor="middle"
                  fill="#4b5563"
                  fontSize="7"
                >
                  {bar.trades}t
                </text>
              </g>
            );
          })}

          {/* Cumulative end marker */}
          {bars.length > 0 && (() => {
            const last = bars[bars.length - 1];
            const endY = toY(last.end);
            const endX = 44 + (bars.length - 1) * (barWidth + 6) + barWidth + 8;
            const color = last.end >= 0 ? "#10b981" : "#ef4444";
            return (
              <>
                <line x1={endX - 4} y1={endY} x2={endX + 20} y2={endY} stroke={color} strokeWidth="2" />
                <text x={endX + 22} y={endY + 3} fill={color} fontSize="9" fontWeight="bold">
                  {last.end >= 0 ? "+" : ""}{Math.abs(last.end) >= 1000 ? `$${(last.end / 1000).toFixed(1)}k` : `$${last.end.toFixed(0)}`}
                </text>
              </>
            );
          })()}
        </svg>
      </div>
    </div>
  );
}
