"use client";

import { useEffect, useState, useCallback } from "react";
import CandlestickChart from "@/components/CandlestickChart";
import AccountSwitcher from "@/components/AccountSwitcher";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

async function fetchJSON<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", headers });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

interface Position {
  symbol: string;
  qty: string;
  side: string;
  market_value: string;
  unrealized_pl: string;
  unrealized_plpc: string;
  avg_entry_price: string;
  current_price: string;
  change_today: string;
  account_label?: string;
}

interface AccountDetail {
  label: string;
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  position_count: number;
}

interface PortfolioData {
  status: string;
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  positions: Position[];
  accounts: AccountDetail[];
}

interface Account {
  label: string;
  is_live: boolean;
}

interface WhatIfPick {
  symbol: string;
  direction: string;
  note: string;
  confidence: number;
  bucket: string;
  current_price: number;
  open_price: number;
  prev_close: number;
  change_pct: number;
  hypothetical_pnl: number;
  hypothetical_value: number;
  hypothetical_pnl_pct: number;
  hypothetical_shares: number;
  error?: string;
}

interface Scenario {
  rank: number;
  name: string;
  probability: number;
  impact: string;
  action: string;
  color: string;
}

interface WhatIfData {
  picks: WhatIfPick[];
  scenarios: Scenario[];
  signal_summary: {
    oil_score: number;
    oil_price: number;
    shipping_score: number;
    geo_score: number;
    vix: number;
  };
  brief_timestamp: string;
  timestamp_utc: string;
}

interface WhatIfScores {
  scores: Record<string, { quality_score: number; win_rate: number; avg_pnl: number }>;
  timestamp_utc: string;
}

function formatCurrency(val: number | string): string {
  const n = typeof val === "string" ? parseFloat(val) : val;
  if (isNaN(n)) return "$0.00";
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
}

function pnlColor(val: number | string): string {
  const n = typeof val === "string" ? parseFloat(val) : val;
  if (n > 0) return "text-green-400";
  if (n < 0) return "text-red-400";
  return "text-gray-400";
}

export default function TradingDashboard() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>("all");
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [chartTimeframe, setChartTimeframe] = useState<string>("5Min");
  const [whatif, setWhatif] = useState<WhatIfData | null>(null);
  const [whatifScores, setWhatifScores] = useState<WhatIfScores | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(new Date());

  const fetchData = useCallback(async () => {
    try {
      const [accts, port, wif, wifScores] = await Promise.all([
        fetchJSON<{ accounts: Account[] }>("/api/accounts"),
        fetchJSON<PortfolioData>(`/api/portfolio?account=${selectedAccount}`),
        fetchJSON<WhatIfData>("/api/whatif-picks").catch(() => null),
        fetchJSON<WhatIfScores>("/api/whatif-scores").catch(() => null),
      ]);
      setAccounts(accts.accounts || []);
      setPortfolio(port);
      if (wif) setWhatif(wif);
      if (wifScores) setWhatifScores(wifScores);
      // Auto-select first position if none selected
      if (!selectedSymbol && port.positions?.length > 0) {
        setSelectedSymbol(port.positions[0].symbol);
      }
      setLastRefresh(new Date());
    } catch (e) {
      console.error("Fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, [selectedAccount, selectedSymbol]);

  useEffect(() => {
    setLoading(true);
    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const positions = portfolio?.positions || [];
  const accountDetails = portfolio?.accounts || [];

  // Compute totals
  const totalEquity = portfolio?.equity || 0;
  const totalCash = portfolio?.cash || 0;
  const totalBuyingPower = portfolio?.buying_power || 0;
  const totalPnL = positions.reduce((sum, p) => sum + parseFloat(p.unrealized_pl || "0"), 0);
  const totalMV = positions.reduce((sum, p) => sum + parseFloat(p.market_value || "0"), 0);

  // Sort positions by absolute PnL
  const sortedPositions = [...positions].sort(
    (a, b) => Math.abs(parseFloat(b.unrealized_pl || "0")) - Math.abs(parseFloat(a.unrealized_pl || "0"))
  );

  const timeframes = [
    { label: "1m", value: "1Min" },
    { label: "5m", value: "5Min" },
    { label: "15m", value: "15Min" },
    { label: "1H", value: "1Hour" },
    { label: "1D", value: "1Day" },
    { label: "1W", value: "1Week" },
  ];

  if (loading && !portfolio) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
          <div className="text-gray-500 text-sm">Loading Trading Dashboard...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen p-3 max-w-[1800px] mx-auto">
      {/* Header */}
      <header className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 mb-4">
        <div className="flex items-center gap-4">
          <a href="/" className="px-3 py-1.5 rounded text-xs font-medium bg-[#1a1f2e] text-gray-400 border border-[#2a3040] hover:bg-[#1f2537] hover:text-gray-200 transition">
            &larr; Dashboard
          </a>
          <h1 className="text-lg font-bold text-gray-200 tracking-tight">TRADING DASHBOARD</h1>
          <span className="text-[10px] text-gray-600">
            {lastRefresh.toLocaleTimeString()}
          </span>
        </div>
        <AccountSwitcher
          accounts={accounts}
          selected={selectedAccount}
          onSelect={setSelectedAccount}
        />
      </header>

      {/* Account Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 mb-4">
        {selectedAccount === "all" ? (
          <>
            <SummaryCard label="Total Equity" value={formatCurrency(totalEquity)} />
            <SummaryCard label="Total Cash" value={formatCurrency(totalCash)} />
            <SummaryCard label="Buying Power" value={formatCurrency(totalBuyingPower)} />
            <SummaryCard label="Market Value" value={formatCurrency(totalMV)} />
            <SummaryCard
              label="Unrealized P&L"
              value={formatCurrency(totalPnL)}
              valueClass={pnlColor(totalPnL)}
            />
            <SummaryCard label="Positions" value={String(positions.length)} />
          </>
        ) : (
          accountDetails
            .filter((a) => a.label === selectedAccount)
            .map((a) => (
              <>
                <SummaryCard key={`${a.label}-eq`} label="Equity" value={formatCurrency(a.equity)} />
                <SummaryCard key={`${a.label}-cash`} label="Cash" value={formatCurrency(a.cash)} />
                <SummaryCard key={`${a.label}-bp`} label="Buying Power" value={formatCurrency(a.buying_power)} />
                <SummaryCard key={`${a.label}-pv`} label="Portfolio Value" value={formatCurrency(a.portfolio_value)} />
                <SummaryCard
                  key={`${a.label}-pnl`}
                  label="Unrealized P&L"
                  value={formatCurrency(totalPnL)}
                  valueClass={pnlColor(totalPnL)}
                />
                <SummaryCard key={`${a.label}-pos`} label="Positions" value={String(a.position_count)} />
              </>
            ))
        )}
      </div>

      {/* Main Grid: Chart + Positions */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
        {/* Candlestick Chart */}
        <div className="lg:col-span-8 card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-xs text-gray-500 uppercase tracking-wider">
              Price Chart {selectedSymbol ? `— ${selectedSymbol}` : ""}
            </h2>
            <div className="flex items-center gap-1">
              {timeframes.map((tf) => (
                <button
                  key={tf.value}
                  onClick={() => setChartTimeframe(tf.value)}
                  className={`px-2 py-1 rounded text-[10px] font-medium cursor-pointer transition ${
                    chartTimeframe === tf.value
                      ? "bg-blue-600 text-white"
                      : "text-gray-500 hover:text-gray-300 hover:bg-[#1a1f2e]"
                  }`}
                >
                  {tf.label}
                </button>
              ))}
            </div>
          </div>
          {selectedSymbol ? (
            <CandlestickChart
              key={`${selectedSymbol}-${chartTimeframe}`}
              symbol={selectedSymbol}
              timeframe={chartTimeframe}
              height={420}
              entryPrice={
                positions.find((p) => p.symbol === selectedSymbol)
                  ? parseFloat(positions.find((p) => p.symbol === selectedSymbol)!.avg_entry_price || "0")
                  : undefined
              }
            />
          ) : (
            <div className="flex items-center justify-center h-[420px] text-gray-600 text-sm">
              Select a position to view chart
            </div>
          )}
        </div>

        {/* Positions List */}
        <div className="lg:col-span-4 card overflow-hidden">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">
            Positions ({positions.length})
          </h2>
          <div className="overflow-y-auto max-h-[480px] -mx-1">
            {sortedPositions.length === 0 ? (
              <div className="text-gray-600 text-xs text-center py-8">No open positions</div>
            ) : (
              sortedPositions.map((p) => {
                const upl = parseFloat(p.unrealized_pl || "0");
                const uplPct = parseFloat(p.unrealized_plpc || "0") * 100;
                const mv = parseFloat(p.market_value || "0");
                const isSelected = selectedSymbol === p.symbol;
                const isLive = p.account_label === "live";

                return (
                  <div
                    key={`${p.symbol}-${p.account_label}`}
                    onClick={() => setSelectedSymbol(p.symbol)}
                    className={`flex items-center justify-between px-3 py-2 cursor-pointer transition rounded mx-1 mb-0.5 ${
                      isSelected
                        ? "bg-blue-600/15 border border-blue-500/30"
                        : "hover:bg-[#1f2537] border border-transparent"
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-gray-200">{p.symbol}</span>
                        <span className={`text-[10px] px-1 rounded ${
                          p.side === "long" ? "text-green-400 bg-green-950/40" : "text-red-400 bg-red-950/40"
                        }`}>
                          {p.side.toUpperCase()}
                        </span>
                        {isLive && (
                          <span className="text-[9px] px-1 rounded text-green-300 bg-green-950/50 border border-green-800/30">
                            LIVE
                          </span>
                        )}
                        {p.account_label && !isLive && (
                          <span className="text-[9px] text-gray-600">{p.account_label}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-gray-500 mt-0.5">
                        <span>{p.qty} shares</span>
                        <span>@ {formatCurrency(p.avg_entry_price)}</span>
                        <span>MV {formatCurrency(mv)}</span>
                      </div>
                    </div>
                    <div className="text-right ml-2">
                      <div className={`text-sm font-medium ${pnlColor(upl)}`}>
                        {upl >= 0 ? "+" : ""}{formatCurrency(upl)}
                      </div>
                      <div className={`text-[10px] ${pnlColor(uplPct)}`}>
                        {uplPct >= 0 ? "+" : ""}{uplPct.toFixed(2)}%
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* Per-Account Breakdown (when viewing all) */}
      {selectedAccount === "all" && accountDetails.length > 1 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
          {accountDetails.map((acct) => {
            const acctPositions = positions.filter((p) => p.account_label === acct.label);
            const acctPnL = acctPositions.reduce(
              (s, p) => s + parseFloat(p.unrealized_pl || "0"), 0
            );
            const isLive = acct.label === "live";
            return (
              <div
                key={acct.label}
                className={`card cursor-pointer transition hover:border-blue-500/30 ${
                  isLive ? "border-green-800/40" : ""
                }`}
                onClick={() => setSelectedAccount(acct.label)}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <h3 className="text-xs font-medium text-gray-300 uppercase">
                      {acct.label.replace("_", " ")}
                    </h3>
                    {isLive && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-green-950/50 text-green-300 border border-green-800/30 pulse-live">
                        LIVE
                      </span>
                    )}
                  </div>
                  <span className={`text-sm font-medium ${pnlColor(acctPnL)}`}>
                    {acctPnL >= 0 ? "+" : ""}{formatCurrency(acctPnL)}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
                  <div className="text-gray-500">Equity</div>
                  <div className="text-right text-gray-300">{formatCurrency(acct.equity)}</div>
                  <div className="text-gray-500">Cash</div>
                  <div className="text-right text-gray-300">{formatCurrency(acct.cash)}</div>
                  <div className="text-gray-500">Buying Power</div>
                  <div className="text-right text-gray-300">{formatCurrency(acct.buying_power)}</div>
                  <div className="text-gray-500">Positions</div>
                  <div className="text-right text-gray-300">{acct.position_count}</div>
                </div>
                {/* Mini position list */}
                {acctPositions.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-[#2a3040]">
                    {acctPositions.slice(0, 5).map((p) => {
                      const upl = parseFloat(p.unrealized_pl || "0");
                      return (
                        <div key={p.symbol} className="flex items-center justify-between text-[10px] py-0.5">
                          <span className="text-gray-400">{p.symbol}</span>
                          <span className={pnlColor(upl)}>
                            {upl >= 0 ? "+" : ""}{formatCurrency(upl)}
                          </span>
                        </div>
                      );
                    })}
                    {acctPositions.length > 5 && (
                      <div className="text-[10px] text-gray-600 mt-1">
                        +{acctPositions.length - 5} more
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* What-If Tracker */}
      {whatif && whatif.picks && whatif.picks.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 mt-3">
          {/* Signal Summary */}
          <div className="lg:col-span-3 card">
            <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Signal Summary</h2>
            {whatif.signal_summary && (
              <div className="space-y-2">
                <div className="flex justify-between text-[11px]">
                  <span className="text-gray-500">Oil Score</span>
                  <span className={`font-medium ${whatif.signal_summary.oil_score >= 8 ? "text-red-400" : whatif.signal_summary.oil_score >= 5 ? "text-yellow-400" : "text-green-400"}`}>
                    {whatif.signal_summary.oil_score}/10
                  </span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span className="text-gray-500">Crude Price</span>
                  <span className="text-gray-200">${whatif.signal_summary.oil_price.toFixed(2)}</span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span className="text-gray-500">Shipping Score</span>
                  <span className={`font-medium ${whatif.signal_summary.shipping_score >= 8 ? "text-red-400" : "text-yellow-400"}`}>
                    {whatif.signal_summary.shipping_score}/10
                  </span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span className="text-gray-500">Geo Score</span>
                  <span className={`font-medium ${whatif.signal_summary.geo_score >= 8 ? "text-red-400" : "text-yellow-400"}`}>
                    {whatif.signal_summary.geo_score}/10
                  </span>
                </div>
                <div className="flex justify-between text-[11px]">
                  <span className="text-gray-500">VIX</span>
                  <span className={`font-medium ${whatif.signal_summary.vix >= 30 ? "text-red-400" : whatif.signal_summary.vix >= 20 ? "text-yellow-400" : "text-green-400"}`}>
                    {whatif.signal_summary.vix.toFixed(1)}
                  </span>
                </div>
              </div>
            )}
            {whatif.brief_timestamp && (
              <div className="mt-3 pt-2 border-t border-[#2a3040] text-[9px] text-gray-600">
                Brief: {new Date(whatif.brief_timestamp).toLocaleString()}
              </div>
            )}
          </div>

          {/* What-If Picks Table */}
          <div className="lg:col-span-9 card">
            <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">
              What-If Tracker — Full Portfolio per Pick from Open
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-gray-500 border-b border-[#2a3040]">
                    <th className="text-left py-1.5 pr-2">Symbol</th>
                    <th className="text-left py-1.5 pr-2">Direction</th>
                    <th className="text-left py-1.5 pr-2">Bucket</th>
                    <th className="text-right py-1.5 pr-2">Open</th>
                    <th className="text-right py-1.5 pr-2">Current</th>
                    <th className="text-right py-1.5 pr-2">Change</th>
                    <th className="text-right py-1.5 pr-2">Hyp P&L</th>
                    <th className="text-right py-1.5 pr-2">Hyp Value</th>
                    <th className="text-right py-1.5">Conf</th>
                  </tr>
                </thead>
                <tbody>
                  {whatif.picks.map((p) => (
                    <tr key={p.symbol} className="border-b border-[#1a1f2e] hover:bg-[#1f2537] transition">
                      <td className="py-1.5 pr-2 font-medium text-gray-200">{p.symbol}</td>
                      <td className="py-1.5 pr-2">
                        <span className={`px-1 rounded text-[10px] ${p.direction === "LONG" ? "text-green-400 bg-green-950/40" : "text-red-400 bg-red-950/40"}`}>
                          {p.direction}
                        </span>
                      </td>
                      <td className="py-1.5 pr-2 text-gray-500">{p.bucket}</td>
                      <td className="py-1.5 pr-2 text-right text-gray-400">${p.open_price.toFixed(2)}</td>
                      <td className="py-1.5 pr-2 text-right text-gray-200">${p.current_price.toFixed(2)}</td>
                      <td className={`py-1.5 pr-2 text-right ${p.change_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {p.change_pct >= 0 ? "+" : ""}{p.change_pct.toFixed(2)}%
                      </td>
                      <td className={`py-1.5 pr-2 text-right font-medium ${p.hypothetical_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {p.hypothetical_pnl >= 0 ? "+" : ""}${p.hypothetical_pnl.toFixed(2)}
                      </td>
                      <td className="py-1.5 pr-2 text-right text-gray-300">${p.hypothetical_value.toFixed(2)}</td>
                      <td className="py-1.5 text-right text-gray-400">{p.confidence}%</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-[#2a3040]">
                    <td colSpan={6} className="py-1.5 text-gray-500 font-medium">Total ({whatif.picks.length} picks)</td>
                    <td className={`py-1.5 pr-2 text-right font-bold ${whatif.picks.reduce((s, p) => s + p.hypothetical_pnl, 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {whatif.picks.reduce((s, p) => s + p.hypothetical_pnl, 0) >= 0 ? "+" : ""}
                      ${whatif.picks.reduce((s, p) => s + p.hypothetical_pnl, 0).toFixed(2)}
                    </td>
                    <td className="py-1.5 text-right text-gray-300 font-bold">
                      ${whatif.picks.reduce((s, p) => s + p.hypothetical_value, 0).toFixed(2)}
                    </td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Scenarios */}
      {whatif && whatif.scenarios && whatif.scenarios.length > 0 && (
        <div className="card mt-3">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Scenario Analysis</h2>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
            {whatif.scenarios.map((s) => (
              <div key={s.rank} className={`rounded-lg border p-3 ${
                s.color === "red" ? "border-red-800/40 bg-red-950/10" :
                s.color === "yellow" ? "border-yellow-800/40 bg-yellow-950/10" :
                "border-green-800/40 bg-green-950/10"
              }`}>
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-[10px] font-bold ${
                    s.color === "red" ? "text-red-400" :
                    s.color === "yellow" ? "text-yellow-400" :
                    "text-green-400"
                  }`}>#{s.rank}</span>
                  <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                    s.color === "red" ? "text-red-300 bg-red-900/30" :
                    s.color === "yellow" ? "text-yellow-300 bg-yellow-900/30" :
                    "text-green-300 bg-green-900/30"
                  }`}>{s.probability}%</span>
                </div>
                <div className="text-[11px] font-medium text-gray-200 mb-1">{s.name}</div>
                <div className="text-[10px] text-gray-500 mb-1">{s.impact}</div>
                <div className="text-[10px] text-blue-400">{s.action}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* What-If Quality Scores */}
      {whatifScores && whatifScores.scores && Object.keys(whatifScores.scores).length > 0 && (
        <div className="card mt-3">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">Learner Quality Scores</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2">
            {Object.entries(whatifScores.scores).map(([sym, s]) => (
              <div key={sym} className="rounded-lg border border-[#2a3040] p-2">
                <div className="text-[11px] font-medium text-gray-200">{sym}</div>
                <div className="flex items-center justify-between mt-1">
                  <span className="text-[10px] text-gray-500">Quality</span>
                  <span className={`text-[10px] font-medium ${(s.quality_score ?? 0) >= 70 ? "text-green-400" : (s.quality_score ?? 0) >= 40 ? "text-yellow-400" : "text-red-400"}`}>
                    {(s.quality_score ?? 0).toFixed(0)}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-gray-500">Win Rate</span>
                  <span className="text-[10px] text-gray-300">{((s.win_rate ?? 0) * 100).toFixed(0)}%</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-gray-500">Avg P&L</span>
                  <span className={`text-[10px] ${(s.avg_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                    ${(s.avg_pnl ?? 0).toFixed(2)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <footer className="mt-4 text-center text-[10px] text-gray-700">
        Global Sentinel Trading Dashboard | Live + Paper Accounts
      </footer>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  valueClass = "text-gray-200",
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="card !py-2 !px-3">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider">{label}</div>
      <div className={`text-sm font-medium mt-0.5 ${valueClass}`}>{value}</div>
    </div>
  );
}
