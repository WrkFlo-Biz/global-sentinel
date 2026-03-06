"use client";

import { useState } from "react";
import { api, type ExecutionModeData } from "@/lib/api";

interface Props {
  data: ExecutionModeData | null;
  onModeChange: () => void;
}

export default function ExecutionModePanel({ data, onModeChange }: Props) {
  const [toggling, setToggling] = useState<string | null>(null);

  if (!data) return <div className="text-gray-600 text-xs">Loading execution config...</div>;

  const strategies = [
    { key: "day_trade", label: "Day Trade", bot: "@mo2darkbot" },
    { key: "medium_long", label: "Medium/Long Hold", bot: "@mo2drkbot" },
  ];

  const handleToggle = async (strategy: string) => {
    const currentMode = data.execution_mode[strategy] || "manual";
    const newMode = currentMode === "auto" ? "manual" : "auto";
    setToggling(strategy);
    try {
      await api.setExecutionMode(strategy, newMode);
      onModeChange();
    } catch (e) {
      console.error("Failed to toggle mode:", e);
    } finally {
      setToggling(null);
    }
  };

  const handleApprove = async (strategy: string) => {
    try {
      await api.approveOrders(strategy, "approve");
      onModeChange();
    } catch (e) {
      console.error("Failed to approve:", e);
    }
  };

  const handleReject = async (strategy: string) => {
    try {
      await api.approveOrders(strategy, "reject");
      onModeChange();
    } catch (e) {
      console.error("Failed to reject:", e);
    }
  };

  return (
    <div className="space-y-3">
      {strategies.map(({ key, label, bot }) => {
        const mode = data.execution_mode[key] || "manual";
        const isAuto = mode === "auto";
        const cfg = data.strategies[key];
        const isToggling = toggling === key;

        return (
          <div key={key} className="bg-[#0d1117] rounded-lg p-2.5 border border-[#1e2530]">
            <div className="flex items-center justify-between gap-2 mb-1.5">
              <div className="min-w-0">
                <div className="text-xs font-medium text-gray-200 truncate">{label}</div>
                <div className="text-[10px] text-gray-500">{bot}</div>
              </div>
              <button
                onClick={() => handleToggle(key)}
                disabled={isToggling}
                className={`
                  relative inline-flex h-6 w-12 items-center rounded-full transition-colors cursor-pointer shrink-0
                  ${isAuto ? "bg-green-600" : "bg-yellow-600"}
                  ${isToggling ? "opacity-50" : ""}
                `}
              >
                <span
                  className={`
                    inline-block h-4 w-4 transform rounded-full bg-white transition-transform
                    ${isAuto ? "translate-x-6.5" : "translate-x-1"}
                  `}
                />
                <span className={`absolute text-[8px] font-bold ${isAuto ? "left-1" : "right-0.5"} text-white`}>
                  {isAuto ? "AUTO" : "MAN"}
                </span>
              </button>
            </div>
            <div className="flex items-center gap-2 text-[9px] text-gray-500 flex-wrap">
              {cfg && (
                <>
                  <span>TP:{cfg.profit_target_pct}%</span>
                  <span>SL:{cfg.stop_loss_pct}%</span>
                  <span>{cfg.holding_period}</span>
                  <span>Max:{cfg.max_positions}</span>
                </>
              )}
            </div>
            {!isAuto && (
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => handleApprove(key)}
                  className="px-3 py-1 text-xs bg-green-800 hover:bg-green-700 text-green-200 rounded border border-green-700 cursor-pointer"
                >
                  Approve Pending
                </button>
                <button
                  onClick={() => handleReject(key)}
                  className="px-3 py-1 text-xs bg-red-900 hover:bg-red-800 text-red-200 rounded border border-red-800 cursor-pointer"
                >
                  Reject Pending
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
