"use client";

import type { BridgeOperatorStatus } from "@/lib/api";

interface BridgeProps {
  bridges?: Record<string, BridgeOperatorStatus>;
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
  gcp_consciousness: { label: "GCP Consciousness", key: "" },
  narrative_velocity: { label: "Narrative Velocity", key: "" },
  options_greeks: { label: "Options Greeks", key: "put_call_ratio" },
  politician_alpha: { label: "Politician Alpha", key: "" },
  fed_board: { label: "Fed Board", key: "" },
  treasury_ofac: { label: "Treasury OFAC", key: "" },
  whitehouse_policy: { label: "White House Policy", key: "" },
  bls_releases: { label: "BLS Releases", key: "" },
  exa_search: { label: "Exa Search", key: "exa_packet_count" },
};

const STATUS_STYLES: Record<string, { dot: string; badge: string }> = {
  live: { dot: "bg-emerald-400", badge: "bg-emerald-400/10 text-emerald-400" },
  source_live: { dot: "bg-sky-400", badge: "bg-sky-400/10 text-sky-400" },
  empty: { dot: "bg-amber-400", badge: "bg-amber-400/10 text-amber-400" },
  snapshot_only: { dot: "bg-cyan-400", badge: "bg-cyan-400/10 text-cyan-400" },
  no_snapshot: { dot: "bg-yellow-400", badge: "bg-yellow-400/10 text-yellow-400" },
  stale: { dot: "bg-red-400", badge: "bg-red-400/10 text-red-400" },
  unknown: { dot: "bg-gray-600", badge: "bg-gray-600/10 text-gray-600" },
};

function formatAge(ageMin?: number | null): string | null {
  if (ageMin === undefined || ageMin === null) return null;
  if (ageMin < 1) return "<1m";
  if (ageMin < 60) return `${Math.round(ageMin)}m`;
  const hours = ageMin / 60;
  if (hours < 24) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

export default function BridgeHealth({ bridges, freshness, summary }: BridgeProps) {
  return (
    <div className="space-y-1.5">
      {Object.entries(BRIDGE_INFO).map(([key, info]) => {
        const bridge = bridges?.[key];
        const status = bridge?.status || (freshness[key] === true ? "live" : freshness[key] === false ? "stale" : "unknown");
        const styles = STATUS_STYLES[status] || STATUS_STYLES.unknown;
        const count = bridge?.count ?? summary[info.key];
        const age = formatAge(bridge?.latest_age_min);
        const badge = bridge?.display_status || (freshness[key] === true ? "LIVE" : freshness[key] === false ? "STALE" : "N/A");
        const detailBits = [count !== undefined ? `${count}` : null, age].filter(Boolean).join(" · ");

        return (
          <div
            key={key}
            className="flex items-center justify-between text-xs py-1 px-2 rounded hover:bg-[#1f2537]"
            title={bridge?.detail || info.label}
          >
            <div className="flex items-center gap-2">
              <span className={`w-2 h-2 rounded-full ${styles.dot}`} />
              <span className="text-gray-300">{info.label}</span>
            </div>
            <div className="flex items-center gap-2">
              {detailBits && (
                <span className="text-gray-500 tabular-nums">{detailBits}</span>
              )}
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${styles.badge}`}>
                {badge}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
