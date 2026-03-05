"use client";

interface OrderEvent {
  timestamp_utc?: string;
  event_type?: string;
  payload?: {
    candidate_count_in_package?: number;
    submit_attempt_count?: number;
    broker_rejected_count?: number;
    broker_acknowledged_count?: number;
    skipped_candidates?: any[];
  };
}

function formatTS(ts?: string): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  } catch {
    return ts;
  }
}

export default function OrderFlow({ orders }: { orders: OrderEvent[] }) {
  const recent = orders.slice(-20).reverse();

  if (!recent.length) {
    return <div className="text-gray-600 text-xs">No execution events</div>;
  }

  return (
    <div className="space-y-1 max-h-[300px] overflow-y-auto">
      {recent.map((o, i) => {
        const p = o.payload || {};
        const eventType = o.event_type || "unknown";
        const isComplete = eventType === "route_package_complete";
        const isSkip = eventType === "candidate_skipped";

        return (
          <div
            key={i}
            className="flex items-center justify-between text-xs py-1.5 px-2 rounded hover:bg-[#1f2537] border-l-2"
            style={{
              borderLeftColor: isComplete
                ? "#10b981"
                : isSkip
                ? "#f59e0b"
                : "#3b82f6",
            }}
          >
            <div className="flex items-center gap-2">
              <span className="text-gray-500 tabular-nums">{formatTS(o.timestamp_utc)}</span>
              <span className="text-gray-300">
                {isComplete
                  ? `Routed ${p.submit_attempt_count || 0} orders (${p.broker_acknowledged_count || 0} ack, ${p.broker_rejected_count || 0} rej)`
                  : eventType.replace(/_/g, " ")}
              </span>
            </div>
            {isComplete && p.candidate_count_in_package !== undefined && (
              <span className="text-gray-500">{p.candidate_count_in_package} candidates</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
