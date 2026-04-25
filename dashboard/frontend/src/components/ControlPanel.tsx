"use client";

import type { ControlStatus } from "@/lib/api";

export default function ControlPanel({
  controlStatus,
  shadowEligible,
  fallback,
}: {
  controlStatus: ControlStatus;
  shadowEligible: boolean;
  fallback: boolean;
}) {
  const items = [
    {
      label: "Kill Switch",
      active: controlStatus.kill_switch,
      danger: true,
    },
    {
      label: "Manual Veto",
      active: controlStatus.manual_veto,
      danger: true,
    },
    {
      label: "Shadow Execution",
      active: controlStatus.shadow_eligible ?? shadowEligible,
      danger: false,
    },
    {
      label: "Fallback Mode",
      active: controlStatus.fallback_mode ?? fallback,
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
            </div>
          </div>
        );
      })}
    </div>
  );
}
