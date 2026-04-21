"use client";

interface Account {
  label: string;
  is_live: boolean;
  display_label?: string;
  broker?: string;
}

interface Props {
  accounts: Account[];
  selected: string;
  onSelect: (label: string) => void;
}

const LABELS: Record<string, string> = {
  all: "All Accounts",
  day_trade: "Day Trade (Paper)",
  medium_long: "Medium-Long (Paper)",
  live: "Live ($125)",
};

export default function AccountSwitcher({ accounts, selected, onSelect }: Props) {
  return (
    <div className="flex items-center gap-1 bg-[#111827] rounded-lg p-1 border border-[#2a3040]">
      <button
        onClick={() => onSelect("all")}
        className={`px-3 py-1.5 rounded text-xs font-medium transition-all cursor-pointer ${
          selected === "all"
            ? "bg-[#3b82f6] text-white shadow-lg shadow-blue-500/20"
            : "text-gray-400 hover:text-gray-200 hover:bg-[#1a1f2e]"
        }`}
      >
        Unified
      </button>
      {accounts.map((acct) => {
        const isActive = selected === acct.label;
        const isLive = acct.is_live;
        return (
          <button
            key={acct.label}
            onClick={() => onSelect(acct.label)}
            className={`px-3 py-1.5 rounded text-xs font-medium transition-all cursor-pointer flex items-center gap-1.5 ${
              isActive
                ? isLive
                  ? "bg-green-600 text-white shadow-lg shadow-green-500/20"
                  : "bg-[#3b82f6] text-white shadow-lg shadow-blue-500/20"
                : "text-gray-400 hover:text-gray-200 hover:bg-[#1a1f2e]"
            }`}
          >
            {isLive && (
              <span className={`w-1.5 h-1.5 rounded-full ${isActive ? "bg-white" : "bg-green-400"} pulse-live`} />
            )}
            {acct.display_label || LABELS[acct.label] || acct.label}
          </button>
        );
      })}
    </div>
  );
}
