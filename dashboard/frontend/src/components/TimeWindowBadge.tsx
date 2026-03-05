"use client";

const WINDOW_COLORS: Record<string, string> = {
  power_hour: "bg-emerald-400/10 text-emerald-400 border-emerald-800",
  core_session: "bg-blue-400/10 text-blue-400 border-blue-800",
  lunch_lull: "bg-gray-400/10 text-gray-400 border-gray-700",
  low_quality_chop: "bg-gray-400/10 text-gray-400 border-gray-700",
  pre_market: "bg-purple-400/10 text-purple-400 border-purple-800",
  after_hours: "bg-gray-500/10 text-gray-500 border-gray-700",
  overnight: "bg-gray-600/10 text-gray-600 border-gray-700",
};

export default function TimeWindowBadge({ window, time }: { window: string; time?: string }) {
  const colorClass = WINDOW_COLORS[window] || "bg-gray-400/10 text-gray-400 border-gray-700";

  return (
    <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded border text-xs ${colorClass}`}>
      <span>{window.replace(/_/g, " ")}</span>
      {time && <span className="text-gray-500">({time} ET)</span>}
    </div>
  );
}
