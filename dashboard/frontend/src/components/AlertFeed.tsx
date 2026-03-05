"use client";

interface Alert {
  timestamp_utc?: string;
  level?: string;
  title?: string;
  body?: string;
  event?: string;
}

const LEVEL_STYLES: Record<string, string> = {
  critical: "border-l-red-500 bg-red-950/10",
  warning: "border-l-yellow-500 bg-yellow-950/10",
  info: "border-l-blue-500 bg-blue-950/10",
};

function formatTS(ts?: string): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleString("en-US", {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
  } catch {
    return ts;
  }
}

export default function AlertFeed({ alerts }: { alerts: Alert[] }) {
  const recent = alerts.slice(-15).reverse();

  if (!recent.length) {
    return <div className="text-gray-600 text-xs">No alerts</div>;
  }

  return (
    <div className="space-y-1.5 max-h-[300px] overflow-y-auto">
      {recent.map((a, i) => {
        const style = LEVEL_STYLES[a.level || "info"] || LEVEL_STYLES.info;
        return (
          <div key={i} className={`border-l-2 ${style} px-3 py-2 rounded-r text-xs`}>
            <div className="flex items-center justify-between">
              <span className="text-gray-200 font-medium">{a.title}</span>
              <span className="text-gray-500 text-[10px]">{formatTS(a.timestamp_utc)}</span>
            </div>
            {a.body && (
              <div className="text-gray-500 mt-0.5 line-clamp-2">{a.body.slice(0, 120)}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}
