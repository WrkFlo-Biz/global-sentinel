"use client";

import type { TradeAnalysis, TradeIdea, SectorRotation, HistoricalExample } from "@/lib/api";

function formatUSD(val: number): string {
  return `$${val.toFixed(2)}`;
}

function formatAge(ageSeconds?: number | null): string {
  if (ageSeconds === undefined || ageSeconds === null) return "unknown";
  if (ageSeconds < 60) return `${ageSeconds}s`;
  if (ageSeconds < 3600) return `${Math.floor(ageSeconds / 60)}m`;
  if (ageSeconds < 86400) return `${Math.floor(ageSeconds / 3600)}h`;
  return `${Math.floor(ageSeconds / 86400)}d`;
}

function IdeaRow({ idea }: { idea: TradeIdea }) {
  const sideColor = idea.side === "long" ? "text-emerald-400" : "text-red-400";
  const sideBg = idea.side === "long" ? "bg-emerald-400/5" : "bg-red-400/5";

  return (
    <div className={`${sideBg} rounded px-3 py-2 mb-1.5`}>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="font-bold text-gray-200 text-sm">{idea.symbol}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium uppercase ${sideColor} ${sideBg} border ${
            idea.side === "long" ? "border-emerald-800" : "border-red-800"
          }`}>
            {idea.side}
          </span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium uppercase ${
            idea.holding_period === "day"
              ? "text-orange-400 bg-orange-950/20 border border-orange-900/30"
              : "text-cyan-400 bg-cyan-950/20 border border-cyan-900/30"
          }`}>
            {idea.holding_period === "day" ? "DAY" : "SWING"}
          </span>
          <span className="text-gray-500 text-[10px]">
            {Math.round(idea.historical_win_rate * 100)}% hist. win
          </span>
        </div>
        <span className="text-xs text-gray-400 tabular-nums">
          Score: {idea.confidence_adjusted_score}
        </span>
      </div>
      <div className="text-xs text-gray-400 mb-1">{idea.reason}</div>
      {idea.entry !== undefined && (
        <div className="flex items-center gap-3 text-[10px] text-gray-500">
          <span>Entry: <span className="text-gray-300 tabular-nums">{formatUSD(idea.entry)}</span></span>
          <span>Target: <span className="text-emerald-400 tabular-nums">{formatUSD(idea.target!)}</span></span>
          <span>Stop: <span className="text-red-400 tabular-nums">{formatUSD(idea.stop!)}</span></span>
          <span>R:R <span className="text-gray-300 tabular-nums">{idea.risk_reward}</span></span>
          {idea.daily_vol_pct !== undefined && (
            <span>Vol: <span className="text-gray-300 tabular-nums">{idea.daily_vol_pct}%</span></span>
          )}
        </div>
      )}
    </div>
  );
}

function SectorCard({ sector }: { sector: SectorRotation }) {
  const signalColor = sector.signal === "bullish" ? "text-emerald-400" : "text-red-400";
  return (
    <div className="bg-[#111827] rounded px-3 py-2 mb-1.5">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-medium text-gray-200">{sector.sector}</span>
        <span className={`text-[10px] font-medium uppercase ${signalColor}`}>{sector.signal}</span>
      </div>
      <div className="text-[10px] text-gray-500 mb-1">{sector.rationale}</div>
      <div className="flex gap-1 flex-wrap">
        {sector.symbols.map((s) => (
          <span key={s} className="text-[10px] px-1.5 py-0.5 bg-[#1a1f2e] rounded text-gray-400">{s}</span>
        ))}
      </div>
    </div>
  );
}

export default function TradeAnalysisPanel({ data }: { data: TradeAnalysis | null }) {
  if (!data || data.error) {
    return <div className="text-gray-600 text-xs">Trade analysis unavailable</div>;
  }

  return (
    <div>
      <div className="text-[10px] text-gray-500 mb-3">
        Source {data.source_freshness || "unknown"}
        {data.source_age_seconds !== undefined && data.source_age_seconds !== null ? ` · age ${formatAge(data.source_age_seconds)}` : ""}
      </div>
      {/* Thesis */}
      <div className="bg-blue-950/20 border border-blue-900/30 rounded px-3 py-2 mb-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-blue-400 uppercase font-medium">
            {data.transition.replace(/_/g, " ")}
          </span>
          <span className="text-[10px] text-gray-500">
            Regime P: {(data.regime_p * 100).toFixed(1)}%
          </span>
        </div>
        <div className="text-xs text-gray-300">{data.playbook_thesis}</div>
      </div>

      {/* Risk Assessment */}
      <div className="flex items-center gap-2 mb-3 text-[10px]">
        <span className="px-1.5 py-0.5 rounded bg-[#111827] text-gray-400">
          {data.risk_assessment.position_sizing}
        </span>
        {data.risk_assessment.risk_factors.map((f, i) => (
          <span key={i} className="px-1.5 py-0.5 rounded bg-yellow-950/20 text-yellow-500 border border-yellow-900/30">
            {f.length > 50 ? f.slice(0, 50) + "..." : f}
          </span>
        ))}
      </div>

      {/* Two columns: Ideas + Sectors */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Trade Ideas</h3>
          <div className="max-h-[400px] overflow-y-auto">
            {data.trade_ideas.map((idea, i) => (
              <IdeaRow key={i} idea={idea} />
            ))}
          </div>
        </div>

        <div>
          <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Sector Rotation</h3>
          <div className="max-h-[250px] overflow-y-auto mb-3">
            {data.sector_analysis.map((s, i) => (
              <SectorCard key={i} sector={s} />
            ))}
          </div>

          {/* Historical Examples */}
          {data.historical_examples.length > 0 && (
            <>
              <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Historical Precedents</h3>
              <div className="space-y-1">
                {data.historical_examples.map((ex, i) => (
                  <div key={i} className="text-[10px] px-2 py-1.5 bg-[#111827] rounded">
                    <span className="text-gray-300">{ex.event}</span>
                    <span className="text-gray-500 ml-2">{ex.result}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
