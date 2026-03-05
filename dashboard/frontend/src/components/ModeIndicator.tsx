"use client";

const MODE_CONFIG: Record<string, { color: string; bg: string; label: string }> = {
  NORMAL: { color: "text-emerald-400", bg: "bg-emerald-400/10", label: "NORMAL" },
  ELEVATED: { color: "text-yellow-400", bg: "bg-yellow-400/10", label: "ELEVATED" },
  CRISIS: { color: "text-red-500", bg: "bg-red-500/10", label: "CRISIS" },
  MANUAL_REVIEW: { color: "text-orange-400", bg: "bg-orange-400/10", label: "MANUAL REVIEW" },
  UNKNOWN: { color: "text-gray-400", bg: "bg-gray-400/10", label: "UNKNOWN" },
};

export default function ModeIndicator({ mode, size = "lg" }: { mode: string; size?: "sm" | "lg" }) {
  const cfg = MODE_CONFIG[mode] || MODE_CONFIG.UNKNOWN;
  const dotSize = size === "lg" ? "w-3 h-3" : "w-2 h-2";
  const textSize = size === "lg" ? "text-2xl font-bold" : "text-sm font-medium";

  return (
    <div className={`flex items-center gap-2 ${cfg.bg} px-3 py-1.5 rounded-lg`}>
      <span className={`${dotSize} rounded-full ${cfg.color} bg-current pulse-live`} />
      <span className={`${textSize} ${cfg.color}`}>{cfg.label}</span>
    </div>
  );
}
