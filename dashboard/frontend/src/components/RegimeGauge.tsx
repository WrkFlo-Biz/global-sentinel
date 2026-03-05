"use client";

export default function RegimeGauge({
  regimeP,
  confidence,
}: {
  regimeP: number;
  confidence: number;
}) {
  const pct = Math.round(regimeP * 100);
  const confPct = Math.round(confidence * 100);

  const getColor = (p: number) => {
    if (p >= 0.75) return "#ef4444";
    if (p >= 0.45) return "#f59e0b";
    if (p >= 0.25) return "#06b6d4";
    return "#10b981";
  };

  const color = getColor(regimeP);
  const radius = 70;
  const circumference = Math.PI * radius; // semi-circle
  const offset = circumference - (regimeP * circumference);

  return (
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
        {/* Threshold markers */}
        {[0.45, 0.75].map((thresh) => {
          const angle = Math.PI * (1 - thresh);
          const x = 90 + 70 * Math.cos(angle);
          const y = 90 - 70 * Math.sin(angle);
          return (
            <circle key={thresh} cx={x} cy={y} r="3" fill="#4b5563" />
          );
        })}
        {/* Center text */}
        <text x="90" y="70" textAnchor="middle" fill={color} fontSize="28" fontWeight="bold" fontFamily="monospace">
          {pct}%
        </text>
        <text x="90" y="88" textAnchor="middle" fill="#6b7280" fontSize="11" fontFamily="monospace">
          regime shift
        </text>
      </svg>
      <div className="flex items-center gap-4 mt-1 text-xs text-gray-500">
        <span>Confidence: <span className="text-gray-300">{confPct}%</span></span>
      </div>
    </div>
  );
}
