"use client";

import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip,
} from "recharts";
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

function riskLevel(avg: number): { label: string; color: string } {
  if (avg >= 0.75) return { label: "EXTREME", color: "#ef4444" };
  if (avg >= 0.5) return { label: "HIGH", color: "#f59e0b" };
  if (avg >= 0.25) return { label: "MODERATE", color: "#06b6d4" };
  return { label: "LOW", color: "#10b981" };
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: any[];
}

function ChartTooltip({ active, payload }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  const color = d.value >= 0.75 ? "#ef4444" : d.value >= 0.5 ? "#f59e0b" : d.value >= 0.25 ? "#06b6d4" : "#10b981";
  return (
    <div className="bg-[#1a1f2e] border border-[#2a3040] rounded-lg px-3 py-2 text-xs shadow-xl">
      <div className="text-gray-300 font-medium mb-0.5">{d.label}</div>
      <div className="flex justify-between gap-4">
        <span className="text-gray-400">Score</span>
        <span className="font-bold tabular-nums" style={{ color }}>{(d.value * 100).toFixed(0)}%</span>
      </div>
    </div>
  );
}

export default function ComponentRadar({ scores }: { scores: ComponentScores | null }) {
  if (!scores) {
    return <div className="text-gray-600 text-xs">No component data</div>;
  }

  const data = Object.entries(scores).map(([key, val]) => ({
    label: LABELS[key] || key,
    value: val,
    fullMark: 1,
  }));

  const avg = data.reduce((sum, d) => sum + d.value, 0) / data.length;
  const { label: riskLabel, color: riskColor } = riskLevel(avg);
  const maxComponent = data.reduce((max, d) => d.value > max.value ? d : max, data[0]);
  const fillColor = riskColor;

  return (
    <div className="space-y-2">
      {/* Risk summary */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className="text-[10px] font-mono font-bold px-2 py-0.5 rounded uppercase"
            style={{ color: riskColor, backgroundColor: `${riskColor}15`, border: `1px solid ${riskColor}30` }}
          >
            {riskLabel}
          </span>
          <span className="text-[10px] text-gray-500">
            Avg: <span className="text-gray-300 tabular-nums">{(avg * 100).toFixed(0)}%</span>
          </span>
        </div>
        <span className="text-[10px] text-gray-500">
          Peak: <span className="text-gray-300">{maxComponent.label}</span>{" "}
          <span className="tabular-nums" style={{ color: riskColor }}>{(maxComponent.value * 100).toFixed(0)}%</span>
        </span>
      </div>

      {/* Radar chart */}
      <ResponsiveContainer width="100%" height={220}>
        <RadarChart data={data} cx="50%" cy="50%" outerRadius="75%">
          <PolarGrid stroke="#2a3040" />
          <PolarAngleAxis
            dataKey="label"
            tick={{ fill: "#9ca3af", fontSize: 9 }}
          />
          <PolarRadiusAxis
            domain={[0, 1]}
            tick={{ fill: "#4b5563", fontSize: 8 }}
            tickCount={5}
            axisLine={false}
          />
          <Tooltip content={<ChartTooltip />} />
          <Radar
            dataKey="value"
            stroke={fillColor}
            fill={fillColor}
            fillOpacity={0.2}
            strokeWidth={2}
            dot={{ r: 3, fill: fillColor, fillOpacity: 0.8 }}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}
