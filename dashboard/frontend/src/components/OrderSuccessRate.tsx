"use client";

interface OrderEvent {
  timestamp_utc?: string;
  event_type?: string;
  payload?: {
    submit_attempt_count?: number;
    broker_rejected_count?: number;
    broker_acknowledged_count?: number;
    candidate_count_in_package?: number;
    skipped_candidates?: any[];
    error?: string;
    reason?: string;
    symbol?: string;
  };
}

interface Stats {
  submitted: number;
  acknowledged: number;
  rejected: number;
  skipped: number;
  errors: number;
}

function BarSegment({ pct, color, label }: { pct: number; color: string; label: string }) {
  if (pct <= 0) return null;
  return (
    <div
      className="h-full flex items-center justify-center text-[8px] font-bold transition-all duration-500"
      style={{ width: `${Math.max(pct, 3)}%`, backgroundColor: color }}
      title={`${label}: ${pct.toFixed(1)}%`}
    >
      {pct >= 8 ? `${pct.toFixed(0)}%` : ""}
    </div>
  );
}

export default function OrderSuccessRate({ orders }: { orders: OrderEvent[] }) {
  if (!orders.length) {
    return <div className="text-gray-600 text-xs">No order data</div>;
  }

  const stats: Stats = { submitted: 0, acknowledged: 0, rejected: 0, skipped: 0, errors: 0 };
  const rejectReasons = new Map<string, number>();

  for (const o of orders) {
    const p = o.payload || {};
    if (o.event_type === "route_package_complete") {
      stats.submitted += p.submit_attempt_count || 0;
      stats.acknowledged += p.broker_acknowledged_count || 0;
      stats.rejected += p.broker_rejected_count || 0;
      if (p.skipped_candidates) {
        for (const skip of p.skipped_candidates) {
          stats.skipped++;
          const reason = skip?.reason || skip?.error || "unknown";
          const short = reason.length > 40 ? reason.slice(0, 40) + "..." : reason;
          rejectReasons.set(short, (rejectReasons.get(short) || 0) + 1);
        }
      }
    } else if (o.event_type === "candidate_skipped") {
      stats.skipped++;
      const reason = p.error || p.reason || "skipped";
      const short = reason.length > 40 ? reason.slice(0, 40) + "..." : reason;
      rejectReasons.set(short, (rejectReasons.get(short) || 0) + 1);
    }
  }

  const total = stats.submitted + stats.skipped;
  if (total === 0) {
    return <div className="text-gray-600 text-xs">No orders attempted</div>;
  }

  const ackPct = (stats.acknowledged / total) * 100;
  const rejPct = (stats.rejected / total) * 100;
  const skipPct = (stats.skipped / total) * 100;
  const pendingPct = Math.max(0, 100 - ackPct - rejPct - skipPct);

  const topReasons = Array.from(rejectReasons.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <div className="space-y-3">
      {/* Stats row */}
      <div className="flex items-center gap-3 sm:gap-4 text-[10px] flex-wrap">
        <span className="text-gray-500">Total: <span className="text-gray-200 font-bold">{total}</span></span>
        <span className="text-emerald-400">Filled: <span className="font-bold">{stats.acknowledged}</span></span>
        <span className="text-red-400">Rejected: <span className="font-bold">{stats.rejected}</span></span>
        <span className="text-yellow-400">Skipped: <span className="font-bold">{stats.skipped}</span></span>
        <span className="text-gray-400">
          Success: <span className={`font-bold ${ackPct >= 50 ? "text-emerald-400" : ackPct >= 20 ? "text-yellow-400" : "text-red-400"}`}>
            {ackPct.toFixed(1)}%
          </span>
        </span>
      </div>

      {/* Stacked bar */}
      <div className="w-full h-6 bg-[#1a1f2e] rounded-full overflow-hidden flex text-white">
        <BarSegment pct={ackPct} color="#10b981" label="Filled" />
        <BarSegment pct={pendingPct} color="#3b82f6" label="Pending" />
        <BarSegment pct={rejPct} color="#ef4444" label="Rejected" />
        <BarSegment pct={skipPct} color="#f59e0b" label="Skipped" />
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 text-[9px] text-gray-500 flex-wrap">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" /> Filled</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-blue-500" /> Pending</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" /> Rejected</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-yellow-500" /> Skipped</span>
      </div>

      {/* Top rejection reasons */}
      {topReasons.length > 0 && (
        <div className="space-y-0.5">
          <span className="text-[9px] text-gray-500 uppercase">Top Skip/Reject Reasons</span>
          {topReasons.map(([reason, count], i) => (
            <div key={i} className="flex items-center justify-between text-[10px] px-2 py-1 bg-[#111827] rounded">
              <span className="text-gray-400 truncate mr-2" title={reason}>{reason}</span>
              <span className="text-yellow-500 shrink-0 font-mono">{count}x</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
