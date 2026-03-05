"use client";

import type { GraduationCheck } from "@/lib/api";

const CHECK_LABELS: Record<string, string> = {
  observation_period: "Observation Period",
  min_bound_order_attempts: "Min Bound Orders",
  broker_reject_rate: "Broker Reject Rate",
  submit_success_rate: "Submit Success Rate",
  risk_gate_block_rate: "Risk Gate Block Rate",
  fallback_mode_rate: "Fallback Mode Rate",
  bridge_quorum_pass_rate: "Bridge Quorum Pass",
  kill_switch_activations: "Kill Switch Events",
  incident_mode_activations: "Incident Events",
};

export default function GraduationProgress({
  stage,
  overallPass,
  checks,
  summary,
}: {
  stage: string;
  overallPass: boolean;
  checks: GraduationCheck[];
  summary: { total_checks: number; passed: number; failed: number };
}) {
  const pct = summary.total_checks > 0
    ? Math.round((summary.passed / summary.total_checks) * 100)
    : 0;

  return (
    <div>
      {/* Progress header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">{stage.replace(/_/g, " ")}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded ${
            overallPass
              ? "bg-emerald-400/10 text-emerald-400"
              : "bg-yellow-400/10 text-yellow-400"
          }`}>
            {overallPass ? "ELIGIBLE" : "IN PROGRESS"}
          </span>
        </div>
        <span className="text-sm font-bold text-gray-200">{summary.passed}/{summary.total_checks}</span>
      </div>

      {/* Overall progress bar */}
      <div className="h-2 bg-[#1a1f2e] rounded-full overflow-hidden mb-3">
        <div
          className={`h-full rounded-full transition-all duration-700 ${
            overallPass ? "bg-emerald-400" : "bg-blue-500"
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Individual checks */}
      <div className="space-y-1">
        {checks.map((c) => (
          <div key={c.check} className="flex items-center justify-between text-xs py-1 px-1.5 rounded hover:bg-[#1f2537]">
            <div className="flex items-center gap-2">
              <span className={`text-[10px] ${
                c.pass ? "text-emerald-400" : c.insufficient_data ? "text-gray-500" : "text-red-400"
              }`}>
                {c.pass ? "\u2713" : c.insufficient_data ? "?" : "\u2717"}
              </span>
              <span className="text-gray-300">{CHECK_LABELS[c.check] || c.check}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-gray-400 tabular-nums">{String(c.actual)}</span>
              <span className="text-gray-600">/</span>
              <span className="text-gray-500 tabular-nums">{String(c.required)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
