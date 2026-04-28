"use client";

import type { PerformanceData, PortfolioData } from "@/lib/api";

function StatBox({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-[#111827] rounded px-3 py-2">
      <div className="text-[10px] text-gray-500 uppercase">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${color || "text-gray-200"}`}>{value}</div>
    </div>
  );
}

function OpenPositionsView({ portfolio }: { portfolio: PortfolioData }) {
  const totalPL = portfolio.positions.reduce((acc, p) => acc + p.unrealized_pl, 0);
  const plColor = totalPL >= 0 ? "text-emerald-400" : "text-red-400";
  const winners = portfolio.positions.filter(p => p.unrealized_pl >= 0).length;
  const losers = portfolio.positions.length - winners;

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[10px] text-yellow-500 bg-yellow-950/20 px-1.5 py-0.5 rounded border border-yellow-900/30">
          OPEN POSITIONS
        </span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-3">
        <StatBox label="Unrealized P&L" value={`$${totalPL >= 0 ? "+" : ""}${totalPL.toFixed(2)}`} color={plColor} />
        <StatBox label="Positions" value={`${winners}W / ${losers}L`} />
        <StatBox label="Count" value={`${portfolio.positions.length}`} />
      </div>
      <div className="space-y-0.5">
        {portfolio.positions
          .sort((a, b) => b.unrealized_pl - a.unrealized_pl)
          .map((p) => (
          <div key={p.symbol} className="flex items-center justify-between text-[11px] px-2 py-1 bg-[#111827] rounded">
            <span className="text-gray-200 font-medium">{p.symbol}</span>
            <div className="flex items-center gap-3">
              <span className="text-gray-500">{p.qty} shares</span>
              <span className={`tabular-nums font-medium ${p.unrealized_pl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                ${p.unrealized_pl >= 0 ? "+" : ""}{p.unrealized_pl.toFixed(2)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function PerformancePanel({ data, portfolio }: { data: PerformanceData | null; portfolio?: PortfolioData | null }) {
  if (!data || data.error || data.total_trades === 0) {
    if (portfolio && portfolio.positions && portfolio.positions.length > 0) {
      return <OpenPositionsView portfolio={portfolio} />;
    }
    return <div className="text-gray-600 text-xs">No trades yet</div>;
  }

  const pnlColor = data.total_pnl >= 0 ? "text-emerald-400" : "text-red-400";
  const wrColor = data.win_rate >= 0.5 ? "text-emerald-400" : data.win_rate >= 0.4 ? "text-yellow-400" : "text-red-400";

  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-2 mb-3">
        <StatBox label="Total P&L" value={`$${data.total_pnl >= 0 ? "+" : ""}${data.total_pnl.toFixed(2)}`} color={pnlColor} />
        <StatBox label="Win Rate" value={`${(data.win_rate * 100).toFixed(1)}%`} color={wrColor} />
        <StatBox label="Trades" value={`${data.wins}W / ${data.losses}L`} />
        <StatBox label="Avg Win" value={`$${data.avg_win >= 0 ? "+" : ""}${data.avg_win.toFixed(2)}`} color="text-emerald-400" />
        <StatBox label="Avg Loss" value={`$${data.avg_loss.toFixed(2)}`} color="text-red-400" />
      </div>

      {data.profit_factor !== null && (
        <div className="text-[10px] text-gray-500 mb-3">
          Profit Factor: <span className="text-gray-300 font-medium">{data.profit_factor.toFixed(2)}</span>
          {" · "}Avg P&L/Trade: <span className={pnlColor}>${data.avg_pnl_per_trade >= 0 ? "+" : ""}{data.avg_pnl_per_trade.toFixed(2)}</span>
        </div>
      )}

      {Object.keys(data.by_symbol).length > 0 && (
        <div>
          <h4 className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">By Symbol</h4>
          <div className="space-y-0.5">
            {Object.entries(data.by_symbol).map(([sym, d]) => (
              <div key={sym} className="flex items-center justify-between text-[11px] px-2 py-1 bg-[#111827] rounded">
                <span className="text-gray-200 font-medium">{sym}</span>
                <div className="flex items-center gap-3">
                  <span className="text-gray-500">{d.trades} trades ({d.wins}W)</span>
                  <span className={`tabular-nums font-medium ${d.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                    ${d.pnl >= 0 ? "+" : ""}{d.pnl.toFixed(2)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
