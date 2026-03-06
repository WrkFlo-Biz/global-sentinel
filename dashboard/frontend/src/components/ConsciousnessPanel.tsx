"use client";

import type { ConsciousnessData } from "@/lib/api";

const COHERENCE_COLORS: Record<string, string> = {
  random: "#10b981",
  low: "#06b6d4",
  moderate: "#f59e0b",
  high: "#f97316",
  extreme: "#ef4444",
};

const SIGNAL_COLORS: Record<string, string> = {
  SYSTEMIC_SHOCK: "#ef4444",
  NOISE_DIVERGENCE: "#f97316",
  COHERENCE_SPIKE: "#f59e0b",
  NARRATIVE_ACCELERATION: "#a855f7",
  CALM: "#10b981",
};

export default function ConsciousnessPanel({ data }: { data: ConsciousnessData | null }) {
  if (!data) {
    return <div className="text-gray-600 text-xs">No consciousness data available</div>;
  }

  const color = COHERENCE_COLORS[data.coherence_level] || "#6b7280";
  const maxZ = data.max_z;
  const clampedZ = Math.min(maxZ, 4); // clamp for gauge display
  const radius = 70;
  const circumference = Math.PI * radius;
  const offset = circumference - ((clampedZ / 4) * circumference);

  const signalColor = data.sentinel_signal
    ? SIGNAL_COLORS[data.sentinel_signal] || "#6b7280"
    : "#6b7280";

  return (
    <div className="space-y-3">
      {/* Coherence Gauge */}
      <div className="flex flex-col items-center">
        <svg width="180" height="100" viewBox="0 0 180 100">
          {/* Background arc */}
          <path
            d="M 10 90 A 70 70 0 0 1 170 90"
            fill="none"
            stroke="#2a3040"
            strokeWidth="12"
            strokeLinecap="round"
          />
          {/* Value arc */}
          <path
            d="M 10 90 A 70 70 0 0 1 170 90"
            fill="none"
            stroke={color}
            strokeWidth="12"
            strokeLinecap="round"
            strokeDasharray={`${circumference}`}
            strokeDashoffset={offset}
            style={{ transition: "stroke-dashoffset 0.8s ease, stroke 0.5s ease" }}
          />
          {/* Threshold markers at z=1.5 and z=2.5 */}
          {[1.5, 2.5].map((thresh) => {
            const angle = Math.PI * (1 - thresh / 4);
            const x = 90 + 70 * Math.cos(angle);
            const y = 90 - 70 * Math.sin(angle);
            return <circle key={thresh} cx={x} cy={y} r="3" fill="#4b5563" />;
          })}
          {/* Center text */}
          <text x="90" y="65" textAnchor="middle" fill={color} fontSize="24" fontWeight="bold" fontFamily="monospace">
            {maxZ.toFixed(2)}
          </text>
          <text x="90" y="80" textAnchor="middle" fill="#6b7280" fontSize="10" fontFamily="monospace">
            Max Z-Score
          </text>
          <text x="90" y="94" textAnchor="middle" fill={color} fontSize="11" fontWeight="600" fontFamily="monospace">
            {data.coherence_level.toUpperCase()}
          </text>
        </svg>
        <div className="flex items-center gap-4 mt-1 text-xs text-gray-500">
          <span>Mean Z: <span className="text-gray-300">{data.mean_z.toFixed(2)}</span></span>
          <span>Nodes: <span className="text-gray-300">{data.node_count}</span></span>
        </div>
      </div>

      {/* Sentinel Signal */}
      {data.sentinel_signal && (
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 uppercase">Signal:</span>
          <span
            className="text-xs font-mono font-semibold px-2 py-0.5 rounded"
            style={{ color: signalColor, backgroundColor: `${signalColor}15`, border: `1px solid ${signalColor}30` }}
          >
            {data.sentinel_signal}
          </span>
        </div>
      )}

      {/* Narrative Velocity */}
      {data.narrative_velocity != null && (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-gray-500 uppercase">Narrative Velocity</span>
            <span className="text-xs text-gray-300 font-mono">{data.narrative_velocity.toFixed(1)}</span>
          </div>
          <div className="w-full h-1.5 bg-[#2a3040] rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{
                width: `${Math.min(data.narrative_velocity * 10, 100)}%`,
                backgroundColor: data.narrative_velocity > 7 ? "#ef4444" : data.narrative_velocity > 4 ? "#f59e0b" : "#10b981",
              }}
            />
          </div>
          {data.dominant_narrative && (
            <div className="text-[10px] text-gray-500 truncate" title={data.dominant_narrative}>
              {data.dominant_narrative}
            </div>
          )}
        </div>
      )}

      {/* Regional Spikes */}
      {data.regional_spikes.length > 0 && (
        <div className="space-y-1">
          <span className="text-[10px] text-gray-500 uppercase">Regional Spikes</span>
          <div className="flex flex-wrap gap-1">
            {data.regional_spikes.map((spike) => {
              const spikeColor = COHERENCE_COLORS[spike.level] || "#6b7280";
              return (
                <span
                  key={spike.region}
                  className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                  style={{ color: spikeColor, backgroundColor: `${spikeColor}15`, border: `1px solid ${spikeColor}30` }}
                  title={`Z=${spike.z_score.toFixed(2)} | ${spike.market_zone} | ${spike.predicted_markets.join(", ")}`}
                >
                  {spike.region} {spike.z_score.toFixed(1)}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Evidence */}
      {data.evidence.length > 0 && (
        <div className="space-y-1">
          <span className="text-[10px] text-gray-500 uppercase">Consciousness Evidence</span>
          <div className="space-y-0.5 max-h-[150px] overflow-y-auto">
            {data.evidence.map((e, i) => (
              <div key={i} className="text-[10px] text-gray-400 font-mono leading-tight truncate" title={e}>
                {e}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
