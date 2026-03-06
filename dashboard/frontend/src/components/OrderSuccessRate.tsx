"use client";

import type { ExecutionLiveOrderAccountSummary, ExecutionSummary } from "@/lib/api";

function formatPct(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function labelizeAccount(label: string): string {
  if (label === "day_trade") return "Day Trade";
  if (label === "day_trade_2") return "Day Trade 2";
  if (label === "medium_long") return "Med/Long";
  return label.replace(/_/g, " ");
}

function BarSegment({ pct, color, label }: { pct: number; color: string; label: string }) {
  if (pct <= 0) return null;
  return (
    <div
      className="h-full flex items-center justify-center text-[8px] font-bold transition-all duration-500"
      style={{ width: `${Math.max(pct, 3)}%`, backgroundColor: color }}
      title={`${label}: ${pct.toFixed(1)}%`}
    >
      {pct >= 9 ? `${pct.toFixed(0)}%` : ""}
    </div>
  );
}

function StatBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-[#111827] rounded px-2.5 py-2">
      <div className="text-[9px] text-gray-500 uppercase">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${color || "text-gray-200"}`}>{value}</div>
    </div>
  );
}

function AccountCard({ label, summary }: { label: string; summary: ExecutionLiveOrderAccountSummary }) {
  return (
    <div className="bg-[#111827] rounded px-2.5 py-2 border border-[#1f2537]">
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-[10px] text-gray-500 uppercase">{labelizeAccount(label)}</span>
        <span className="text-[10px] text-gray-400">{summary.order_count_total} orders</span>
      </div>
      <div className="text-[11px] text-gray-300">
        Any fill <span className="font-semibold text-emerald-400">{formatPct(summary.fill_rate_any)}</span>
        {" · "}
        Full fill <span className="font-semibold text-cyan-400">{formatPct(summary.fill_rate_full)}</span>
      </div>
      <div className="text-[10px] text-gray-500 mt-1">
        Filled {summary.filled} · Partial {summary.partially_filled} · Open {summary.open}
      </div>
    </div>
  );
}

export default function OrderSuccessRate({ summary }: { summary: ExecutionSummary | null }) {
  if (!summary) {
    return <div className="text-gray-600 text-xs">Execution summary unavailable</div>;
  }

  const routing = summary.routing;
  const processedTotal = Math.max(
    routing.processed_candidate_count,
    routing.submit_success_count + routing.broker_rejected_count + routing.skipped_count + routing.error_count,
  );
  const blockedCount = routing.skipped_count + routing.error_count;

  const routingSubmitPct = processedTotal > 0 ? (routing.submit_success_count / processedTotal) * 100 : 0;
  const routingRejectPct = processedTotal > 0 ? (routing.broker_rejected_count / processedTotal) * 100 : 0;
  const routingSkipPct = processedTotal > 0 ? (routing.skipped_count / processedTotal) * 100 : 0;
  const routingErrorPct = processedTotal > 0 ? (routing.error_count / processedTotal) * 100 : 0;

  const liveOrders = summary.live_orders;
  const liveTotal = liveOrders.order_count_total;
  const filledPct = liveTotal > 0 ? (liveOrders.filled_count / liveTotal) * 100 : 0;
  const partialPct = liveTotal > 0 ? (liveOrders.partially_filled_count / liveTotal) * 100 : 0;
  const openPct = liveTotal > 0 ? (liveOrders.open_count / liveTotal) * 100 : 0;
  const rejectedPct = liveTotal > 0 ? (liveOrders.rejected_count / liveTotal) * 100 : 0;
  const canceledPct = liveTotal > 0 ? (liveOrders.canceled_count / liveTotal) * 100 : 0;
  const expiredPct = liveTotal > 0 ? (liveOrders.expired_count / liveTotal) * 100 : 0;

  const categoryCounts = Object.entries(routing.block_reason_category_counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const rawReasons = Object.entries(routing.raw_block_reason_counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const accountSummaries = Object.entries(liveOrders.by_account).sort((a, b) => b[1].order_count_total - a[1].order_count_total);

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] text-gray-500 uppercase tracking-wider">Routing Funnel</span>
          <span className="text-[10px] text-gray-500">{processedTotal} candidates</span>
        </div>

        <div className="grid grid-cols-2 gap-2 mb-3">
          <StatBox label="Candidate Conv." value={formatPct(routing.candidate_conversion_rate)} color="text-emerald-400" />
          <StatBox
            label="Broker Accept"
            value={routing.submit_attempt_count > 0 ? formatPct(routing.broker_accept_rate) : "n/a"}
            color={routing.submit_attempt_count > 0 ? "text-cyan-400" : "text-gray-500"}
          />
          <StatBox label="Submitted" value={`${routing.submit_success_count}`} />
          <StatBox label="Skipped/Blocked" value={`${blockedCount}`} color={blockedCount > 0 ? "text-yellow-400" : undefined} />
        </div>

        <div className="w-full h-6 bg-[#1a1f2e] rounded-full overflow-hidden flex text-white">
          <BarSegment pct={routingSubmitPct} color="#10b981" label="Submitted" />
          <BarSegment pct={routingRejectPct} color="#ef4444" label="Broker Rejected" />
          <BarSegment pct={routingSkipPct} color="#f59e0b" label="Skipped" />
          <BarSegment pct={routingErrorPct} color="#fb7185" label="Blocked/Error" />
        </div>

        <div className="flex items-center gap-3 text-[9px] text-gray-500 flex-wrap mt-2">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" /> Submitted</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" /> Broker Rejected</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-500" /> Skipped</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-rose-400" /> Blocked/Error</span>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] text-gray-500 uppercase tracking-wider">True Fill Rate</span>
          <span className="text-[10px] text-gray-500">Live broker, last {liveOrders.lookback_hours}h</span>
        </div>

        {liveOrders.status === "error" || liveOrders.status === "unavailable" ? (
          <div className="text-gray-600 text-xs">Live broker order state unavailable.</div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-2 mb-3">
              <StatBox label="Any Fill" value={formatPct(liveOrders.fill_rate_any)} color="text-emerald-400" />
              <StatBox label="Full Fill" value={formatPct(liveOrders.fill_rate_full)} color="text-cyan-400" />
              <StatBox label="Open at Broker" value={`${liveOrders.open_count}`} color={liveOrders.open_count > 0 ? "text-blue-400" : undefined} />
              <StatBox label="Recent Orders" value={`${liveTotal}`} />
            </div>

            <div className="w-full h-6 bg-[#1a1f2e] rounded-full overflow-hidden flex text-white">
              <BarSegment pct={filledPct} color="#10b981" label="Filled" />
              <BarSegment pct={partialPct} color="#06b6d4" label="Partially Filled" />
              <BarSegment pct={openPct} color="#3b82f6" label="Open" />
              <BarSegment pct={rejectedPct} color="#ef4444" label="Rejected" />
              <BarSegment pct={canceledPct} color="#6b7280" label="Canceled" />
              <BarSegment pct={expiredPct} color="#f59e0b" label="Expired" />
            </div>

            <div className="flex items-center gap-3 text-[9px] text-gray-500 flex-wrap mt-2">
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" /> Filled</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-cyan-500" /> Partial</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-500" /> Open</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" /> Rejected</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-gray-500" /> Canceled</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-500" /> Expired</span>
            </div>

            {accountSummaries.length > 0 && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-3">
                {accountSummaries.map(([label, accountSummary]) => (
                  <AccountCard key={label} label={label} summary={accountSummary} />
                ))}
              </div>
            )}
          </>
        )}

        {liveOrders.account_errors.length > 0 && (
          <div className="mt-2 rounded border border-amber-900/40 bg-amber-950/20 px-2.5 py-2 text-[10px] text-amber-200">
            {liveOrders.account_errors.map((item) => `${labelizeAccount(item.label)}: ${item.error}`).join(" | ")}
          </div>
        )}
      </div>

      {categoryCounts.length > 0 && (
        <div>
          <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Skip / Block Categories</div>
          <div className="grid grid-cols-2 gap-2">
            {categoryCounts.map(([category, count]) => (
              <div key={category} className="bg-[#111827] rounded px-2.5 py-2 flex items-center justify-between">
                <span className="text-[10px] text-gray-400">{category}</span>
                <span className="text-[11px] font-mono text-yellow-400">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {rawReasons.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] text-gray-500 uppercase tracking-wider">Top Raw Reasons</div>
          {rawReasons.map(([reason, count]) => (
            <div key={reason} className="flex items-center justify-between text-[10px] px-2 py-1 bg-[#111827] rounded">
              <span className="text-gray-400 truncate mr-2" title={reason}>{reason}</span>
              <span className="text-yellow-500 shrink-0 font-mono">{count}x</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
