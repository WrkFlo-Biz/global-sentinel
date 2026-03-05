"use client";

import type { ComponentScores } from "@/lib/api";

const LABELS: Record<string, string> = {
  geopolitical_tension: "Geopolitical",
  market_volatility: "Volatility",
  currency_stress: "Currency",
  commodity_shock: "Commodity",
  policy_uncertainty: "Policy",
  labor_disruption: "Labor",
  credit_spread: "Credit",
  liquidity_stress: "Liquidity",
};

const WEIGHTS: Record<string, number> = {
  geopolitical_tension: 0.25,
  market_volatility: 0.20,
  currency_stress: 0.15,
  commodity_shock: 0.15,
  policy_uncertainty: 0.10,
  labor_disruption: 0.05,
  credit_spread: 0.05,
  liquidity_stress: 0.05,
};

function barColor(val: number): string {
  if (val >= 0.75) return "bg-red-500";
  if (val >= 0.5) return "bg-yellow-500";
  if (val >= 0.25) return "bg-cyan-500";
  return "bg-emerald-500";
}

export default function ComponentBars({ scores }: { scores: ComponentScores }) {
  const sorted = Object.entries(scores)
    .sort(([, a], [, b]) => b - a);

  return (
    <div className="space-y-2">
      {sorted.map(([key, val]) => (
        <div key={key} className="flex items-center gap-2 text-xs">
          <span className="w-20 text-gray-400 truncate">{LABELS[key] || key}</span>
          <div className="flex-1 h-3 bg-[#1a1f2e] rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${barColor(val)} transition-all duration-700`}
              style={{ width: `${Math.max(val * 100, 1)}%` }}
            />
          </div>
          <span className="w-10 text-right text-gray-300 tabular-nums">{(val * 100).toFixed(0)}%</span>
          <span className="w-8 text-right text-gray-600 tabular-nums">{(WEIGHTS[key] || 0) * 100}w</span>
        </div>
      ))}
    </div>
  );
}
