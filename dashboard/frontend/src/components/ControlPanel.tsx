"use client";

import type { Controls } from "@/lib/api";

export default function ControlPanel({
  controls,
  shadowEligible,
  fallback,
}: {
  controls: Controls;
  shadowEligible: boolean;
  fallback: boolean;
}) {
  const items = [
    {
      label: "Kill Switch",
      active: controls.kill_switch?.active,
      danger: true,
      detail: controls.kill_switch?.reason,
    },
    {
      label: "Manual Veto",
      active: controls.manual_veto?.active,
      danger: true,
      detail: controls.manual_veto?.reason,
    },
    {
      label: "Shadow Execution",
      active: shadowEligible,
      danger: false,
    },
    {
      label: "Fallback Mode",
      active: fallback,
      danger: true,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-2">
      {items.map((item) => {
        const isOk = item.danger ? !item.active : item.active;
        return (
          <div
            key={item.label}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs ${
              isOk
                ? "border-emerald-900/50 bg-emerald-950/20"
                : "border-red-900/50 bg-red-950/20"
            }`}
          >
            <span className={`w-2 h-2 rounded-full ${isOk ? "bg-emerald-400" : "bg-red-400 pulse-live"}`} />
            <div>
              <div className={isOk ? "text-emerald-400" : "text-red-400"}>{item.label}</div>
              {!isOk && item.detail && (
                <div className="text-gray-500 text-[10px] mt-0.5 truncate max-w-[120px]">{item.detail}</div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
