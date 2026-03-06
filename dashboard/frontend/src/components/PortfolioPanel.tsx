"use client";

import type { PortfolioAccountDetail, PortfolioData, PortfolioPosition } from "@/lib/api";

function formatUSD(val: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(val);
}

function formatPct(val: number): string {
  const sign = val >= 0 ? "+" : "";
  return `${sign}${(val * 100).toFixed(2)}%`;
}

function formatFreshness(sourceTimestampUtc?: string, cacheStatus?: string, cacheAgeMs?: number): string {
  if (!sourceTimestampUtc) return "Source freshness unavailable";
  try {
    const ageSeconds = Math.max(0, Math.floor((Date.now() - new Date(sourceTimestampUtc).getTime()) / 1000));
    const ageLabel = ageSeconds < 5
      ? "Source updated just now"
      : ageSeconds < 60
        ? `Source updated ${ageSeconds}s ago`
        : `Source updated ${Math.floor(ageSeconds / 60)}m ago`;
    const cacheLabel = cacheStatus ? ` · cache ${cacheStatus}` : "";
    const ageMsLabel = typeof cacheAgeMs === "number" ? ` · age ${Math.round(cacheAgeMs)}ms` : "";
    return `${ageLabel}${cacheLabel}${ageMsLabel}`;
  } catch {
    return "Source freshness unavailable";
  }
}

function formatAccountLabel(label: string): string {
  if (label === "day_trade") return "Day Trade";
  if (label === "day_trade_2") return "Day Trade 2";
  if (label === "medium_long") return "Med/Long";
  return label.replace(/_/g, " ");
}

function formatAccountTag(label?: string): string {
  if (label === "day_trade") return "DT";
  if (label === "day_trade_2") return "DT2";
  if (label === "medium_long") return "ML";
  return (label || "").slice(0, 3).toUpperCase();
}

function statusTone(status?: string): string {
  if (status === "error") return "border-red-900/50 bg-red-950/30 text-red-200";
  if (status === "partial") return "border-amber-900/50 bg-amber-950/30 text-amber-200";
  return "border-emerald-900/50 bg-emerald-950/30 text-emerald-200";
}

function accountPositionCount(
  data: PortfolioData,
  account: PortfolioAccountDetail,
): number {
  return data.position_count_by_account?.[account.label]
    ?? account.position_count
    ?? account.positions.length;
}

function accountPnL(account: PortfolioAccountDetail): number {
  return account.positions.reduce((acc, position) => acc + position.unrealized_pl, 0);
}

function positionsEmptyMessage(status: string, accountErrors: Array<{ label: string; error: string }>): string {
  if (status === "error") return "Portfolio unavailable. All requested accounts failed.";
  if (accountErrors.length > 0) return "No open positions returned from healthy accounts.";
  return "No open positions";
}

function PositionsTable({
  positions,
  multiAccount,
}: {
  positions: PortfolioPosition[];
  multiAccount: boolean;
}) {
  return (
    <div className="overflow-x-auto overflow-y-auto max-h-[320px]">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-[#1a1f2e]">
          <tr className="text-gray-500 border-b border-[#2a3040]">
            <th className="text-left py-1.5 font-medium">Symbol</th>
            {multiAccount && <th className="text-left py-1.5 font-medium">Acct</th>}
            <th className="text-right py-1.5 font-medium">Qty</th>
            <th className="text-right py-1.5 font-medium">Entry</th>
            <th className="text-right py-1.5 font-medium">Price</th>
            <th className="text-right py-1.5 font-medium">P&L</th>
            <th className="text-right py-1.5 font-medium">%</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position, index) => {
            const plColor = position.unrealized_pl >= 0 ? "text-emerald-400" : "text-red-400";
            const accountTag = formatAccountTag(position.account);
            return (
              <tr
                key={`${position.symbol}-${position.account || "single"}-${index}`}
                className="border-b border-[#1f2537] hover:bg-[#1f2537]"
              >
                <td className="py-1.5 font-medium text-gray-200">{position.symbol}</td>
                {multiAccount && <td className="py-1.5 text-[10px] text-gray-500">{accountTag}</td>}
                <td className="py-1.5 text-right text-gray-300 tabular-nums">{position.qty}</td>
                <td className="py-1.5 text-right text-gray-400 tabular-nums">{position.avg_entry_price.toFixed(2)}</td>
                <td className="py-1.5 text-right text-gray-300 tabular-nums">{position.current_price.toFixed(2)}</td>
                <td className={`py-1.5 text-right tabular-nums ${plColor}`}>{formatUSD(position.unrealized_pl)}</td>
                <td className={`py-1.5 text-right tabular-nums ${plColor}`}>{formatPct(position.unrealized_plpc)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function PortfolioPanel({ data }: { data: PortfolioData | null }) {
  if (!data) {
    return (
      <div className="text-gray-600 text-xs">
        Portfolio data unavailable. Connect Alpaca paper account.
      </div>
    );
  }

  const positions = data.positions || [];
  const accounts = data.accounts || [];
  const accountErrors = data.account_errors || [];
  const totalPL = positions.reduce((acc, position) => acc + position.unrealized_pl, 0);
  const totalPositionCount = data.position_count_total ?? positions.length;
  const totalAccountCount = Math.max(1, data.account_count ?? accounts.length ?? 1);
  const multiAccount = totalAccountCount > 1;
  const status = data.status || (accountErrors.length > 0 ? "partial" : "ok");
  const statusClasses = statusTone(status);

  return (
    <div>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className={`rounded border px-2 py-0.5 text-[10px] font-semibold tracking-wide ${statusClasses}`}>
              {status.toUpperCase()}
            </span>
            {data.schema_version && (
              <span className="text-[10px] text-gray-500">{data.schema_version}</span>
            )}
          </div>
          <div className="text-[10px] text-gray-500 mt-1">
            {totalPositionCount} positions across {totalAccountCount} account{totalAccountCount === 1 ? "" : "s"}
          </div>
          <div className="text-[10px] text-gray-500 mt-1">
            {formatFreshness(data.source_timestamp_utc, data.cache_status, data.cache_age_ms)}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        {[
          { label: multiAccount ? "Total Equity" : "Equity", value: formatUSD(data.equity) },
          { label: "Cash", value: formatUSD(data.cash) },
          { label: "Buying Power", value: formatUSD(data.buying_power) },
          {
            label: "Unrealized P&L",
            value: formatUSD(totalPL),
            color: totalPL >= 0 ? "text-emerald-400" : "text-red-400",
          },
        ].map((item) => (
          <div key={item.label} className="text-center">
            <div className="text-[10px] text-gray-500 uppercase">{item.label}</div>
            <div className={`text-sm font-bold tabular-nums ${item.color || "text-gray-200"}`}>
              {item.value}
            </div>
          </div>
        ))}
      </div>

      {accountErrors.length > 0 && (
        <div className={`rounded border px-3 py-2 mb-3 ${statusClasses}`}>
          <div className="text-[10px] uppercase tracking-wider font-semibold">
            {status === "error" ? "Account failures" : "Partial account failure"}
          </div>
          <div className="space-y-1 mt-1">
            {accountErrors.map((accountError) => (
              <div key={accountError.label} className="text-[11px] leading-4">
                <span className="font-semibold">{formatAccountLabel(accountError.label)}:</span>{" "}
                <span>{accountError.error}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {accounts.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-3">
          {accounts.map((account) => {
            const isError = account.status === "error";
            const pnl = accountPnL(account);
            const pnlColor = pnl >= 0 ? "text-emerald-400" : "text-red-400";
            return (
              <div
                key={account.label}
                className={`rounded px-2.5 py-2 border ${isError ? "border-red-900/40 bg-red-950/20" : "border-[#1f2537] bg-[#111827]"}`}
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <div className="text-[10px] text-gray-400 uppercase">
                    {formatAccountLabel(account.label)}
                  </div>
                  <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold ${statusTone(account.status || "ok")}`}>
                    {(account.status || "ok").toUpperCase()}
                  </span>
                </div>

                {isError ? (
                  <div className="text-[11px] text-red-200 leading-4">
                    {account.error || "Account data unavailable."}
                  </div>
                ) : (
                  <>
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-gray-300 tabular-nums">{formatUSD(account.equity)}</span>
                      <span className={`tabular-nums font-medium ${pnlColor}`}>{formatUSD(pnl)}</span>
                    </div>
                    <div className="text-[10px] text-gray-500 mt-1">
                      {accountPositionCount(data, account)} positions
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}

      {positions.length > 0 ? (
        <PositionsTable positions={positions} multiAccount={multiAccount} />
      ) : (
        <div className="text-gray-600 text-xs text-center py-4">
          {positionsEmptyMessage(status, accountErrors)}
        </div>
      )}
    </div>
  );
}
