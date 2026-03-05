"use client";

export default function EvidenceLog({ evidence }: { evidence: string[] }) {
  if (!evidence?.length) {
    return <div className="text-gray-600 text-xs">No evidence signals</div>;
  }

  return (
    <div className="space-y-1 max-h-[200px] overflow-y-auto">
      {evidence.map((e, i) => (
        <div key={i} className="flex items-start gap-2 text-xs py-1">
          <span className="text-cyan-500 mt-0.5 shrink-0">&#x25B8;</span>
          <span className="text-gray-300">{e}</span>
        </div>
      ))}
    </div>
  );
}
