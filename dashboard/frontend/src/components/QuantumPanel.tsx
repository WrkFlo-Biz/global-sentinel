"use client";

import { useEffect, useState, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

interface BackendResult {
  backend: string;
  status: string;
  objective_value?: number;
  algorithm?: string;
  selected_candidates?: string[];
  runtime_seconds?: number;
  error?: string;
}

interface ComparisonData {
  request_hash: string;
  backends_attempted: string[];
  backends_succeeded: string[];
  backends_failed: string[];
  timestamp_utc?: string;
  results: Record<string, any>;
}

interface OvernightBatch {
  status: string;
  timestamp_utc: string;
  iterations_completed: number;
  iterations_requested: number;
  results: Array<{
    iteration: number;
    timestamp_utc: string;
    objective_values: Record<string, number>;
    best_objective_backend: string;
    quantum_vs_strong_classical_delta: number;
    backends_succeeded: string[];
    backends_failed: string[];
  }>;
}

interface QuantumSummary {
  evaluation_count: number;
  quantum_win_rate: number;
  classical_win_rate: number;
  tie_rate: number;
  avg_quantum_overlap_score: number;
  avg_classical_overlap_score: number;
}

export interface QuantumData {
  schema_version: string;
  timestamp_utc: string;
  source_timestamp_utc?: string;
  source_age_seconds?: number | null;
  source_freshness?: "live" | "degraded" | "stale" | "unknown";
  latest_artifact_timestamp_utc?: string | null;
  latest_comparison: ComparisonData | null;
  overnight_batch: OvernightBatch | null;
  summary: QuantumSummary | null;
  comparison_count: number;
  scorecard_quantum?: {
    quantum_stage: string;
    quantum_influence_cap: number;
  };
  error?: string;
}

const BACKEND_COLORS: Record<string, string> = {
  qpanda3: "#a855f7",
  qiskit_finance: "#6366f1",
  pennylane_vqc: "#8b5cf6",
  classical_strong: "#10b981",
  classical_greedy: "#06b6d4",
  classical_baseline: "#64748b",
  classical_fallback: "#64748b",
};

const STATUS_COLORS: Record<string, string> = {
  success: "#10b981",
  error: "#ef4444",
  timeout: "#f59e0b",
};

function formatAge(ageSeconds?: number | null): string {
  if (ageSeconds === undefined || ageSeconds === null) return "unknown";
  if (ageSeconds < 60) return `${ageSeconds}s`;
  if (ageSeconds < 3600) return `${Math.floor(ageSeconds / 60)}m`;
  if (ageSeconds < 86400) return `${Math.floor(ageSeconds / 3600)}h`;
  return `${Math.floor(ageSeconds / 86400)}d`;
}

function BackendBadge({ name, status }: { name: string; status: string }) {
  const color = BACKEND_COLORS[name] || "#6b7280";
  const isQuantum = name.startsWith("q") || name.startsWith("pennylane");
  return (
    <span
      className="text-[10px] font-mono px-1.5 py-0.5 rounded inline-flex items-center gap-1"
      style={{
        color,
        backgroundColor: `${color}15`,
        border: `1px solid ${color}30`,
      }}
    >
      {isQuantum && <span className="text-[8px]">Q</span>}
      {name.replace(/_/g, " ")}
      <span
        className="w-1.5 h-1.5 rounded-full inline-block"
        style={{ backgroundColor: STATUS_COLORS[status] || "#6b7280" }}
      />
    </span>
  );
}

function ObjectiveBar({
  label,
  value,
  maxVal,
  color,
  isBest,
}: {
  label: string;
  value: number;
  maxVal: number;
  color: string;
  isBest: boolean;
}) {
  const pct = maxVal > 0 ? Math.max(0, Math.min((value / maxVal) * 100, 100)) : 0;
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] text-gray-500 w-24 truncate font-mono" title={label}>
        {label.replace(/_/g, " ")}
      </span>
      <div className="flex-1 h-3 bg-[#1a1f2e] rounded-full overflow-hidden relative">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
        {isBest && (
          <span className="absolute right-1 top-0 text-[8px] text-yellow-400 leading-3">
            BEST
          </span>
        )}
      </div>
      <span className="text-[10px] text-gray-300 font-mono w-14 text-right">
        {value.toFixed(3)}
      </span>
    </div>
  );
}

export default function QuantumPanel({ data }: { data: QuantumData | null }) {
  if (!data) {
    return <div className="text-gray-600 text-xs">No quantum data available</div>;
  }

  if (data.error) {
    return <div className="text-red-400 text-xs">{data.error}</div>;
  }

  const batch = data.overnight_batch;
  const latestResult = batch?.results?.[batch.results.length - 1];
  const comparison = data.latest_comparison;
  const summary = data.summary;
  const stage = data.scorecard_quantum;

  // Compute objective values for bar chart
  const objectiveValues: Record<string, number> = latestResult?.objective_values || {};
  const maxObj = Math.max(...Object.values(objectiveValues).map(Math.abs), 0.01);
  const bestBackend = latestResult?.best_objective_backend || "";

  // Backend status from comparison
  const succeeded = comparison?.backends_succeeded || [];
  const failed = comparison?.backends_failed || [];
  const attempted = comparison?.backends_attempted || [];

  return (
    <div className="space-y-3">
      <div className="text-[10px] text-gray-500">
        Latest artifact {data.source_freshness || "unknown"}
        {data.source_age_seconds !== undefined && data.source_age_seconds !== null ? ` · age ${formatAge(data.source_age_seconds)}` : ""}
      </div>
      {/* Stage & Influence Cap */}
      {stage && (
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-500 uppercase">Stage</span>
            <span
              className="text-xs font-mono font-semibold px-2 py-0.5 rounded"
              style={{
                color: "#a855f7",
                backgroundColor: "#a855f715",
                border: "1px solid #a855f730",
              }}
            >
              {stage.quantum_stage}
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-500 uppercase">Influence Cap</span>
            <span className="text-xs font-mono text-gray-300">
              {(stage.quantum_influence_cap * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      )}

      {/* Backend Status Grid */}
      <div className="space-y-1">
        <span className="text-[10px] text-gray-500 uppercase">Backends</span>
        <div className="flex flex-wrap gap-1">
          {attempted.map((b) => (
            <BackendBadge
              key={b}
              name={b}
              status={succeeded.includes(b) ? "success" : "error"}
            />
          ))}
        </div>
        <div className="flex items-center gap-3 text-[10px] text-gray-500 mt-0.5">
          <span>
            <span className="text-green-400">{succeeded.length}</span> / {attempted.length} online
          </span>
          {failed.length > 0 && (
            <span className="text-red-400">{failed.length} failed</span>
          )}
          <span>{data.comparison_count} comparisons total</span>
        </div>
      </div>

      {/* Objective Values Bar Chart */}
      {latestResult && Object.keys(objectiveValues).length > 0 && (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-gray-500 uppercase">
              Objective Values — Latest Run
            </span>
            <span className="text-[10px] text-gray-600 font-mono">
              Q vs Classical: {latestResult.quantum_vs_strong_classical_delta > 0 ? "+" : ""}
              {latestResult.quantum_vs_strong_classical_delta.toFixed(3)}
            </span>
          </div>
          <div className="space-y-1.5">
            {Object.entries(objectiveValues)
              .sort(([, a], [, b]) => b - a)
              .map(([backend, value]) => (
                <ObjectiveBar
                  key={backend}
                  label={backend}
                  value={value}
                  maxVal={maxObj}
                  color={BACKEND_COLORS[backend] || "#6b7280"}
                  isBest={backend === bestBackend}
                />
              ))}
          </div>
        </div>
      )}

      {/* Win Rate Summary */}
      {summary && summary.evaluation_count > 0 && (
        <div className="space-y-1">
          <span className="text-[10px] text-gray-500 uppercase">
            Win Rate ({summary.evaluation_count} evaluations)
          </span>
          <div className="flex items-center gap-1 h-4">
            {summary.quantum_win_rate > 0 && (
              <div
                className="h-full rounded-l flex items-center justify-center text-[9px] font-mono text-white"
                style={{
                  width: `${summary.quantum_win_rate * 100}%`,
                  backgroundColor: "#a855f7",
                  minWidth: "30px",
                }}
              >
                Q {(summary.quantum_win_rate * 100).toFixed(0)}%
              </div>
            )}
            {summary.tie_rate > 0 && (
              <div
                className="h-full flex items-center justify-center text-[9px] font-mono text-white"
                style={{
                  width: `${summary.tie_rate * 100}%`,
                  backgroundColor: "#64748b",
                  minWidth: "24px",
                }}
              >
                Tie
              </div>
            )}
            {summary.classical_win_rate > 0 && (
              <div
                className="h-full rounded-r flex items-center justify-center text-[9px] font-mono text-white"
                style={{
                  width: `${summary.classical_win_rate * 100}%`,
                  backgroundColor: "#10b981",
                  minWidth: "30px",
                }}
              >
                C {(summary.classical_win_rate * 100).toFixed(0)}%
              </div>
            )}
          </div>
          <div className="flex items-center gap-4 text-[10px] text-gray-500">
            <span>
              Overlap — Q:{" "}
              <span className="text-purple-400">
                {summary.avg_quantum_overlap_score.toFixed(3)}
              </span>{" "}
              C:{" "}
              <span className="text-green-400">
                {summary.avg_classical_overlap_score.toFixed(3)}
              </span>
            </span>
          </div>
        </div>
      )}

      {/* Overnight Batch Status */}
      {batch && (
        <div className="space-y-1">
          <span className="text-[10px] text-gray-500 uppercase">Overnight Batch</span>
          <div className="flex items-center gap-3 text-[10px]">
            <span
              className="font-mono px-1.5 py-0.5 rounded"
              style={{
                color: batch.status === "success" ? "#10b981" : "#f59e0b",
                backgroundColor:
                  batch.status === "success" ? "#10b98115" : "#f59e0b15",
                border: `1px solid ${batch.status === "success" ? "#10b98130" : "#f59e0b30"}`,
              }}
            >
              {batch.status}
            </span>
            <span className="text-gray-500">
              {batch.iterations_completed}/{batch.iterations_requested} iterations
            </span>
            <span className="text-gray-600 font-mono">
              {new Date(batch.timestamp_utc).toLocaleTimeString()}
            </span>
          </div>
        </div>
      )}

      {/* Timestamp */}
      <div className="text-[10px] text-gray-600 text-right font-mono">
        Updated: {data.timestamp_utc ? new Date(data.timestamp_utc).toLocaleString() : "—"}
      </div>
    </div>
  );
}
