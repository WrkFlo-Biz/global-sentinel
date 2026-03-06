"use client";

import type { PoliticianAlphaData } from "@/lib/api";

const SENTIMENT_CONFIG: Record<string, { color: string; bg: string; border: string }> = {
  bullish: { color: "#10b981", bg: "#10b98115", border: "#10b98130" },
  bearish: { color: "#ef4444", bg: "#ef444415", border: "#ef444430" },
  neutral: { color: "#6b7280", bg: "#6b728015", border: "#6b728030" },
};

function ScoreBar({ symbol, score, maxAbs }: { symbol: string; score: number; maxAbs: number }) {
  const pct = maxAbs > 0 ? Math.abs(score) / maxAbs : 0;
  const isPositive = score >= 0;
  const color = isPositive ? "#10b981" : "#ef4444";

  return (
    <div className="flex items-center gap-2 text-[10px]">
      <span className="w-10 text-gray-400 font-mono text-right shrink-0">{symbol}</span>
      <div className="flex-1 h-3 bg-[#2a3040] rounded-full overflow-hidden relative">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${Math.min(pct * 100, 100)}%`,
            backgroundColor: color,
            marginLeft: isPositive ? "50%" : undefined,
            marginRight: !isPositive ? "50%" : undefined,
            position: "absolute",
            left: isPositive ? "50%" : undefined,
            right: !isPositive ? "50%" : undefined,
          }}
        />
        <div
          className="absolute top-0 bottom-0 w-px bg-gray-600"
          style={{ left: "50%" }}
        />
      </div>
      <span
        className="w-12 text-right font-mono shrink-0"
        style={{ color }}
      >
        {score > 0 ? "+" : ""}{score.toFixed(1)}
      </span>
    </div>
  );
}

export default function PoliticianAlphaPanel({ data }: { data: PoliticianAlphaData | null }) {
  if (!data || data.error || (!data.fresh && data.total_trades_analyzed === 0)) {
    const msg = data?.error || data?.reason || "No politician alpha data available";
    return (
      <div className="text-gray-600 text-xs space-y-1">
        <div>{msg}</div>
        {msg.includes("FMP_API_KEY") && (
          <div className="text-[10px] text-gray-700">
            Set FMP_API_KEY env var with a key from financialmodelingprep.com
          </div>
        )}
      </div>
    );
  }

  const sentimentCfg = SENTIMENT_CONFIG[data.aggregate_sentiment] || SENTIMENT_CONFIG.neutral;
  const scores = data.political_alpha_scores || {};
  const sortedScores = Object.entries(scores)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, 12);
  const maxAbs = sortedScores.length > 0
    ? Math.max(...sortedScores.map(([, s]) => Math.abs(s)), 1)
    : 1;

  return (
    <div className="space-y-3">
      {/* Header: Sentiment Badge + Stats */}
      <div className="flex items-center justify-between">
        <span
          className="text-xs font-mono font-semibold px-2 py-0.5 rounded uppercase"
          style={{
            color: sentimentCfg.color,
            backgroundColor: sentimentCfg.bg,
            border: `1px solid ${sentimentCfg.border}`,
          }}
        >
          {data.aggregate_sentiment}
        </span>
        <div className="flex items-center gap-3 text-[10px] text-gray-500">
          <span>{data.total_trades_analyzed} trades</span>
          <span>{data.tracked_symbols_with_activity} symbols</span>
          {!data.fresh && (
            <span className="text-yellow-600">stale</span>
          )}
        </div>
      </div>

      {/* Political Alpha Scores Heatmap */}
      {sortedScores.length > 0 && (
        <div className="space-y-1">
          <span className="text-[10px] text-gray-500 uppercase">Political Alpha Scores</span>
          <div className="space-y-0.5">
            {sortedScores.map(([symbol, score]) => (
              <ScoreBar key={symbol} symbol={symbol} score={score} maxAbs={maxAbs} />
            ))}
          </div>
        </div>
      )}

      {/* Top Whale Trades */}
      {data.top_whale_trades.length > 0 && (
        <div className="space-y-1">
          <span className="text-[10px] text-gray-500 uppercase">Top Whale Trades</span>
          <div className="max-h-[250px] overflow-y-auto space-y-1">
            {data.top_whale_trades.slice(0, 8).map((trade, i) => {
              const isBuy = trade.transaction_type.toLowerCase().includes("purchase");
              const tradeColor = isBuy ? "#10b981" : "#ef4444";
              return (
                <div
                  key={i}
                  className="flex items-center justify-between text-[10px] font-mono px-1.5 py-1 rounded bg-[#1e2333]"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ backgroundColor: tradeColor }}
                    />
                    <span className="text-gray-300 truncate" title={trade.politician}>
                      {trade.politician.trim() || "Unknown"}
                    </span>
                    <span className="text-gray-500">|</span>
                    <span style={{ color: tradeColor }} className="font-semibold shrink-0">
                      {trade.symbol}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    <span className="text-gray-500 truncate max-w-[80px]" title={trade.amount}>
                      {trade.amount}
                    </span>
                    <span className="text-gray-600">
                      {trade.transaction_date?.slice(0, 10)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Committee Signals */}
      {data.committee_signals.length > 0 && (
        <div className="space-y-1">
          <span className="text-[10px] text-gray-500 uppercase">Committee-Ticker Clusters</span>
          <div className="flex flex-wrap gap-1">
            {data.committee_signals.slice(0, 8).map((sig, i) => {
              const weight = sig.influence_weight;
              const color = weight >= 1.8 ? "#f59e0b" : weight >= 1.5 ? "#06b6d4" : "#6b7280";
              return (
                <span
                  key={i}
                  className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                  style={{
                    color,
                    backgroundColor: `${color}15`,
                    border: `1px solid ${color}30`,
                  }}
                  title={`${sig.committee} | ${sig.trade_count} trades | weight: ${weight}x`}
                >
                  {sig.symbol} x{sig.trade_count}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Source info */}
      {data.reason && (
        <div className="text-[10px] text-gray-600 italic">{data.reason}</div>
      )}
    </div>
  );
}
