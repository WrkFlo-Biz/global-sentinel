"use client";

interface Position {
  symbol: string;
  qty: number;
  side: string;
  avg_entry_price: number;
  current_price: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  market_value: number;
}

interface PortfolioData {
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  positions: Position[];
  timestamp_utc: string;
}

function formatUSD(val: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(val);
}

function formatPct(val: number): string {
  const sign = val >= 0 ? "+" : "";
  return `${sign}${(val * 100).toFixed(2)}%`;
}

export default function PortfolioPanel({ data }: { data: PortfolioData | null }) {
  if (!data) {
    return (
      <div className="text-gray-600 text-xs">
        Portfolio data unavailable. Connect Alpaca paper account.
      </div>
    );
  }

  const totalPL = data.positions.reduce((acc, p) => acc + p.unrealized_pl, 0);

  return (
    <div>
      {/* Account summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        {[
          { label: "Equity", value: formatUSD(data.equity) },
          { label: "Cash", value: formatUSD(data.cash) },
          { label: "Buying Power", value: formatUSD(data.buying_power) },
          { label: "Unrealized P&L", value: formatUSD(totalPL), color: totalPL >= 0 ? "text-emerald-400" : "text-red-400" },
        ].map((item) => (
          <div key={item.label} className="text-center">
            <div className="text-[10px] text-gray-500 uppercase">{item.label}</div>
            <div className={`text-sm font-bold tabular-nums ${item.color || "text-gray-200"}`}>
              {item.value}
            </div>
          </div>
        ))}
      </div>

      {/* Positions table */}
      {data.positions.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-[#2a3040]">
                <th className="text-left py-1.5 font-medium">Symbol</th>
                <th className="text-right py-1.5 font-medium">Qty</th>
                <th className="text-right py-1.5 font-medium">Entry</th>
                <th className="text-right py-1.5 font-medium">Price</th>
                <th className="text-right py-1.5 font-medium">P&L</th>
                <th className="text-right py-1.5 font-medium">%</th>
              </tr>
            </thead>
            <tbody>
              {data.positions.map((p) => {
                const plColor = p.unrealized_pl >= 0 ? "text-emerald-400" : "text-red-400";
                return (
                  <tr key={p.symbol} className="border-b border-[#1f2537] hover:bg-[#1f2537]">
                    <td className="py-1.5 font-medium text-gray-200">{p.symbol}</td>
                    <td className="py-1.5 text-right text-gray-300 tabular-nums">{p.qty}</td>
                    <td className="py-1.5 text-right text-gray-400 tabular-nums">{p.avg_entry_price.toFixed(2)}</td>
                    <td className="py-1.5 text-right text-gray-300 tabular-nums">{p.current_price.toFixed(2)}</td>
                    <td className={`py-1.5 text-right tabular-nums ${plColor}`}>{formatUSD(p.unrealized_pl)}</td>
                    <td className={`py-1.5 text-right tabular-nums ${plColor}`}>{formatPct(p.unrealized_plpc)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-gray-600 text-xs text-center py-4">No open positions</div>
      )}
    </div>
  );
}
