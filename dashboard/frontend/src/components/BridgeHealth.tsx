"use client";

interface BridgeProps {
  freshness: Record<string, boolean>;
  summary: Record<string, number | undefined>;
}

const BRIDGE_INFO: Record<string, { label: string; key: string }> = {
  market_microstructure: { label: "Market Data", key: "microstructure_symbols" },
  finnhub: { label: "Finnhub News", key: "finnhub_packet_count" },
  fred: { label: "FRED Macro", key: "" },
  gdelt: { label: "GDELT Geopolitics", key: "gdelt_event_count" },
  aviation_disruption: { label: "Aviation", key: "aviation_disruption_count" },
  eia: { label: "EIA Energy", key: "" },
};

export default function BridgeHealth({ freshness, summary }: BridgeProps) {
  return (
    <div className="space-y-1.5">
      {Object.entries(BRIDGE_INFO).map(([key, info]) => {
        const isFresh = freshness[key];
        const count = summary[info.key];
        return (
          <div key={key} className="flex items-center justify-between text-xs py-1 px-2 rounded hover:bg-[#1f2537]">
            <div className="flex items-center gap-2">
              <span
                className={`w-2 h-2 rounded-full ${
                  isFresh === true ? "bg-emerald-400" : isFresh === false ? "bg-red-400" : "bg-gray-600"
                }`}
              />
              <span className="text-gray-300">{info.label}</span>
            </div>
            <div className="flex items-center gap-2">
              {count !== undefined && (
                <span className="text-gray-500 tabular-nums">{count}</span>
              )}
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                isFresh === true
                  ? "bg-emerald-400/10 text-emerald-400"
                  : isFresh === false
                  ? "bg-red-400/10 text-red-400"
                  : "bg-gray-600/10 text-gray-600"
              }`}>
                {isFresh === true ? "LIVE" : isFresh === false ? "STALE" : "N/A"}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
