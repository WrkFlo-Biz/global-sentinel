"use client";

import { useEffect, useState, useCallback, useRef } from "react";
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
  account?: string;
  broker?: string;
  is_live?: boolean;
}

interface AccountDetail {
  label: string;
  broker?: string;
  display_label?: string;
  account_number?: string;
  is_live?: boolean;
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

interface Order {
  id: string;
  symbol: string;
  side: string;
  type: string;
  qty?: string;
  notional?: string;
  status: string;
  submitted_at: string;
  account_label?: string;
}

interface Account {
  label: string;
  is_live: boolean;
  display_label?: string;
  broker?: string;
}

interface SignalFeedData {
  war_intensity?: number;
  bucket_scores?: Record<string, number>;
  market_data?: Record<string, { price?: number; change_pct?: number }>;
  timestamp_utc?: string;
  last_updated?: string;
  _served_at?: string;
  [key: string]: unknown;
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

function pnlBorderClass(plPct: number): string {
  if (plPct < -35) return "border-red-500/60 shadow-[0_0_8px_rgba(239,68,68,0.4)] animate-pulse";
  if (plPct < -20) return "border-red-500/50 shadow-[0_0_6px_rgba(239,68,68,0.3)]";
  if (plPct > 50) return "border-green-500/50 shadow-[0_0_6px_rgba(34,197,94,0.3)]";
  return "border-transparent";
}

function positionAccountLabel(position: Position): string {
  return position.account_label || position.account || "";
}

export default function TradingDashboard() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>("all");
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [chartTimeframe, setChartTimeframe] = useState<string>("5Min");
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(new Date());
  const [closingSymbol, setClosingSymbol] = useState<string | null>(null);
  const [riskPanelOpen, setRiskPanelOpen] = useState(true);
  const [signalFeed, setSignalFeed] = useState<SignalFeedData | null>(null);
  const [signalFeedError, setSignalFeedError] = useState(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [pnlFlash, setPnlFlash] = useState<string | null>(null);
  const prevPnlRef = useRef<Record<string, number>>({});

  const fetchData = useCallback(async () => {
    try {
      const [accts, port, ords] = await Promise.all([
        fetchJSON<{ accounts: Account[] }>("/api/accounts"),
        fetchJSON<PortfolioData>(`/api/portfolio?account=${selectedAccount}`),
        fetchJSON<{ orders: Order[] }>(`/api/orders?account=${selectedAccount}&status=open&limit=20`).catch(() => ({ orders: [] })),
      ]);
      setAccounts(accts.accounts || []);
      setPortfolio(port);
      setOrders(ords.orders || []);
      // Auto-select first position ONLY if no symbol currently selected
      // or if current selection no longer exists in positions list
      if (port.positions?.length > 0) {
        setSelectedSymbol((prev) => {
          if (!prev || !port.positions.some((p: Position) => p.symbol === prev)) {
            return port.positions[0].symbol;
          }
          return prev;
        });
        // Detect P&L changes for flash effect
        for (const p of port.positions) {
          const newPl = parseFloat(p.unrealized_pl || "0");
          const prevPl = prevPnlRef.current[p.symbol];
          if (prevPl !== undefined && Math.abs(newPl - prevPl) > 0.005) {
            setPnlFlash(p.symbol);
            setTimeout(() => setPnlFlash(null), 600);
          }
          prevPnlRef.current[p.symbol] = newPl;
        }
      } else {
        setSelectedSymbol(null);
      }
      setLastRefresh(new Date());
    } catch (e) {
      console.error("Fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, [selectedAccount]);

  useEffect(() => {
    setLoading(true);
    setPortfolio(null); // Clear stale data immediately on account switch
    setSelectedSymbol(null);
    fetchData();
    const interval = setInterval(fetchData, 1000); // Real-time: 1s refresh
    return () => clearInterval(interval);
  }, [fetchData]);

  // Signal feed polling — every 10 seconds
  useEffect(() => {
    let cancelled = false;
    const fetchSignal = async () => {
      try {
        const data = await fetchJSON<SignalFeedData>("/api/signal-feed");
        if (!cancelled) {
          setSignalFeed(data);
          setSignalFeedError(false);
        }
      } catch {
        if (!cancelled) setSignalFeedError(true);
      }
    };
    fetchSignal();
    const interval = setInterval(fetchSignal, 10000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const cancelOrder = useCallback(async (orderId: string, accountLabel?: string) => {
    try {
      const headers: Record<string, string> = {};
      if (API_KEY) headers["X-API-Key"] = API_KEY;
      const acctParam = accountLabel || selectedAccount;
      const res = await fetch(`${API_BASE}/api/orders/${orderId}?account_label=${acctParam}`, {
        method: "DELETE",
        headers,
      });
      if (!res.ok) throw new Error(`Cancel failed: ${res.status}`);
      setOrders((prev) => prev.filter((o) => o.id !== orderId));
    } catch (e) {
      console.error("Cancel order error:", e);
    }
  }, [selectedAccount]);

  const closePosition = useCallback(async (symbol: string, accountLabel?: string) => {
    if (closingSymbol !== symbol) {
      setClosingSymbol(symbol);
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
      closeTimerRef.current = setTimeout(() => setClosingSymbol(null), 3000);
      return;
    }
    setClosingSymbol(null);
    if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    try {
      const headers: Record<string, string> = {};
      if (API_KEY) headers["X-API-Key"] = API_KEY;
      const acctParam = accountLabel || selectedAccount;
      const res = await fetch(`${API_BASE}/api/positions/${symbol}/close?account_label=${acctParam}`, {
        method: "POST",
        headers,
      });
      if (!res.ok) throw new Error(`Close failed: ${res.status}`);
      fetchData();
    } catch (e) {
      console.error("Close position error:", e);
    }
  }, [closingSymbol, selectedAccount, fetchData]);

  const positions = portfolio?.positions || [];
  const accountDetails = portfolio?.accounts || [];

  // Compute totals
  const totalEquity = portfolio?.equity || 0;
  const totalCash = portfolio?.cash || 0;
  const totalBuyingPower = portfolio?.buying_power || 0;
  const totalPnL = positions.reduce((sum, p) => sum + parseFloat(p.unrealized_pl || "0"), 0);
  const totalMV = positions.reduce((sum, p) => sum + parseFloat(p.market_value || "0"), 0);

  // Per-account P&L when viewing a single account
  const selectedAcctDetail = accountDetails.find((a) => a.label === selectedAccount);
  const filteredPnL = selectedAccount === "all"
    ? totalPnL
    : positions
        .filter((p) => positionAccountLabel(p) === selectedAccount)
        .reduce((sum, p) => sum + parseFloat(p.unrealized_pl || "0"), 0);

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
          <a href="/" className="text-gray-500 hover:text-gray-300 text-xs transition">
            &larr; Sentinel
          </a>
          <h1 className="text-lg font-bold text-gray-200 tracking-tight">TRADING DASHBOARD</h1>
          <span className="flex items-center gap-1.5 text-[10px] text-gray-600">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
            LIVE {lastRefresh.toLocaleTimeString()}
          </span>
        </div>
        <AccountSwitcher
          accounts={accounts}
          selected={selectedAccount}
          onSelect={setSelectedAccount}
        />
      </header>

      {/* Single Account Header */}
      {selectedAccount !== "all" && (
        <div className="flex items-center gap-3 mb-3">
          <button
            onClick={() => setSelectedAccount("all")}
            className="text-xs text-blue-400 hover:text-blue-300 transition cursor-pointer"
          >
            &larr; All Accounts
          </button>
          <h2 className="text-sm font-bold text-gray-200 uppercase">
            {(selectedAcctDetail?.display_label || selectedAccount.replaceAll("_", " ")).toUpperCase()}
          </h2>
          {selectedAcctDetail?.is_live && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-green-950/50 text-green-300 border border-green-800/30 pulse-live">
              LIVE
            </span>
          )}
        </div>
      )}

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
            >
              <PnlSparkline account="all" />
            </SummaryCard>
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
                  value={formatCurrency(filteredPnL)}
                  valueClass={pnlColor(filteredPnL)}
                >
                  <PnlSparkline account={a.label} />
                </SummaryCard>
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
                const posAccount = positionAccountLabel(p);
                const posKey = `${p.symbol}-${posAccount}`;
                const isSelected = selectedSymbol === p.symbol;
                const isLive = posAccount === "live" || p.is_live === true;
                const borderClass = pnlBorderClass(uplPct);
                const isConfirmingClose = closingSymbol === p.symbol;

                return (
                  <div
                    key={posKey}
                    onClick={() => setSelectedSymbol(p.symbol)}
                    className={`group flex items-center justify-between px-3 py-2 cursor-pointer transition rounded mx-1 mb-0.5 ${
                      isSelected
                        ? `bg-blue-600/15 border ${borderClass !== "border-transparent" ? borderClass : "border-blue-500/30"}`
                        : `hover:bg-[#1f2537] border ${borderClass}`
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
                        {posAccount && !isLive && (
                          <span className="text-[9px] text-gray-600">{posAccount}</span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-[10px] text-gray-500 mt-0.5">
                        <span>{p.qty} shares</span>
                        <span>@ {formatCurrency(p.avg_entry_price)}</span>
                        <span>MV {formatCurrency(mv)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className={`text-right transition-all duration-300 ${pnlFlash === p.symbol ? "scale-110 brightness-150" : ""}`}>
                        <div className={`text-sm font-medium ${pnlColor(upl)}`}>
                          {upl >= 0 ? "+" : ""}{formatCurrency(upl)}
                        </div>
                        <div className={`text-[10px] ${pnlColor(uplPct)}`}>
                          {uplPct >= 0 ? "+" : ""}{uplPct.toFixed(2)}%
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          closePosition(p.symbol, posAccount);
                        }}
                        className={`w-6 h-6 rounded text-xs font-bold transition flex-shrink-0 cursor-pointer ${
                          isConfirmingClose
                            ? "bg-red-600/80 text-white opacity-100"
                            : "opacity-0 group-hover:opacity-100 text-gray-500 hover:text-red-400 hover:bg-red-950/40"
                        }`}
                        title={isConfirmingClose ? "Click again to confirm close" : "Close position"}
                      >
                        {isConfirmingClose ? "!" : "\u00d7"}
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* Risk Exposure Panel */}
      {positions.length > 0 && (
        <div className="card mt-3">
          <button
            onClick={() => setRiskPanelOpen(!riskPanelOpen)}
            className="flex items-center justify-between w-full cursor-pointer"
          >
            <h2 className="text-xs text-gray-500 uppercase tracking-wider">
              Risk Exposure
            </h2>
            <span className="text-gray-600 text-xs">{riskPanelOpen ? "▾" : "▸"}</span>
          </button>

          {riskPanelOpen && (() => {
            const SECTOR_MAP: Record<string, string[]> = {
              "Energy/Oil": ["GUSH","UCO","USO","XLE","VLO","OXY","XOM","CVX","COP","BOIL","XOP","SLB"],
              "Gold/Precious": ["GLD","SLV","GDX","PAXGUSD"],
              "Defense": ["ITA","LMT","RTX","NOC","GD","BA"],
              "Shipping/Tankers": ["STNG","ZIM","GOGL","FRO","INSW","DHT"],
              "Volatility": ["UVXY","VXX","VIXY","SQQQ"],
              "Tech": ["QQQ","TQQQ","META","AAPL","MSFT","NVDA","GOOGL","AMZN"],
            };
            const SECTOR_COLORS: Record<string, string> = {
              "Energy/Oil": "bg-orange-500",
              "Crypto": "bg-yellow-500",
              "Gold/Precious": "bg-amber-400",
              "Defense": "bg-slate-400",
              "Shipping/Tankers": "bg-cyan-500",
              "Volatility": "bg-red-500",
              "Tech": "bg-purple-500",
              "Other": "bg-gray-500",
            };

            function getSector(symbol: string): string {
              if (symbol.includes("USD") && !["PAXGUSD"].includes(symbol)) return "Crypto";
              for (const [sector, syms] of Object.entries(SECTOR_MAP)) {
                if (syms.includes(symbol)) return sector;
              }
              return "Other";
            }

            const absMV = positions.reduce((s, p) => s + Math.abs(parseFloat(p.market_value || "0")), 0);

            // Sector breakdown
            const sectorMV: Record<string, number> = {};
            positions.forEach((p) => {
              const sector = getSector(p.symbol);
              sectorMV[sector] = (sectorMV[sector] || 0) + Math.abs(parseFloat(p.market_value || "0"));
            });
            const sectorEntries = Object.entries(sectorMV).sort((a, b) => b[1] - a[1]);

            // Long/short
            const longMV = positions.filter(p => p.side === "long").reduce((s, p) => s + Math.abs(parseFloat(p.market_value || "0")), 0);
            const shortMV = positions.filter(p => p.side === "short").reduce((s, p) => s + Math.abs(parseFloat(p.market_value || "0")), 0);
            const lsTotal = longMV + shortMV || 1;

            // Account concentration
            const acctMV: Record<string, number> = {};
            positions.forEach((p) => {
              const lbl = positionAccountLabel(p) || "unknown";
              acctMV[lbl] = (acctMV[lbl] || 0) + Math.abs(parseFloat(p.market_value || "0"));
            });
            const acctEntries = Object.entries(acctMV).sort((a, b) => b[1] - a[1]);

            // Top 5 positions
            const top5 = [...positions]
              .sort((a, b) => Math.abs(parseFloat(b.market_value || "0")) - Math.abs(parseFloat(a.market_value || "0")))
              .slice(0, 5);

            return (
              <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Sector Concentration */}
                <div>
                  <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Sector Concentration</h3>
                  {/* Stacked bar */}
                  <div className="flex h-5 rounded overflow-hidden mb-2">
                    {sectorEntries.map(([sector, mv]) => {
                      const pct = absMV ? (mv / absMV) * 100 : 0;
                      if (pct < 0.5) return null;
                      return (
                        <div
                          key={sector}
                          className={`${SECTOR_COLORS[sector] || "bg-gray-500"} relative group`}
                          style={{ width: `${pct}%` }}
                          title={`${sector}: ${pct.toFixed(1)}%`}
                        />
                      );
                    })}
                  </div>
                  {/* Legend */}
                  <div className="space-y-1">
                    {sectorEntries.map(([sector, mv]) => {
                      const pct = absMV ? (mv / absMV) * 100 : 0;
                      return (
                        <div key={sector} className="flex items-center justify-between text-[10px] font-mono">
                          <div className="flex items-center gap-1.5">
                            <div className={`w-2 h-2 rounded-sm ${SECTOR_COLORS[sector] || "bg-gray-500"}`} />
                            <span className="text-gray-400">{sector}</span>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="text-gray-500">{formatCurrency(mv)}</span>
                            <span className="text-gray-300 w-12 text-right">{pct.toFixed(1)}%</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Right column: Long/Short + Account + Top 5 */}
                <div className="space-y-4">
                  {/* Long/Short Split */}
                  <div>
                    <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Long / Short Split</h3>
                    <div className="flex h-5 rounded overflow-hidden mb-1.5">
                      <div className="bg-green-500" style={{ width: `${(longMV / lsTotal) * 100}%` }} />
                      <div className="bg-red-500" style={{ width: `${(shortMV / lsTotal) * 100}%` }} />
                    </div>
                    <div className="flex justify-between text-[10px] font-mono">
                      <span className="text-green-400">LONG {formatCurrency(longMV)} ({((longMV / lsTotal) * 100).toFixed(1)}%)</span>
                      <span className="text-red-400">SHORT {formatCurrency(shortMV)} ({((shortMV / lsTotal) * 100).toFixed(1)}%)</span>
                    </div>
                  </div>

                  {/* Account Concentration */}
                  {selectedAccount === "all" && acctEntries.length > 1 && (
                    <div>
                      <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Account Concentration</h3>
                      <div className="flex h-4 rounded overflow-hidden mb-1.5">
                        {acctEntries.map(([lbl, mv], i) => {
                          const pct = absMV ? (mv / absMV) * 100 : 0;
                          const colors = ["bg-blue-500", "bg-indigo-500", "bg-teal-500", "bg-pink-500"];
                          return (
                            <div key={lbl} className={colors[i % colors.length]} style={{ width: `${pct}%` }} title={`${lbl}: ${pct.toFixed(1)}%`} />
                          );
                        })}
                      </div>
                      <div className="space-y-0.5">
                        {acctEntries.map(([lbl, mv], i) => {
                          const pct = absMV ? (mv / absMV) * 100 : 0;
                          const dotColors = ["bg-blue-500", "bg-indigo-500", "bg-teal-500", "bg-pink-500"];
                          return (
                            <div key={lbl} className="flex items-center justify-between text-[10px] font-mono">
                              <div className="flex items-center gap-1.5">
                                <div className={`w-2 h-2 rounded-sm ${dotColors[i % dotColors.length]}`} />
                                <span className="text-gray-400">{lbl.replace("_", " ")}</span>
                              </div>
                              <span className="text-gray-300">{pct.toFixed(1)}%</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Top 5 Positions */}
                  <div>
                    <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2">Top 5 Positions (by MV)</h3>
                    <div className="space-y-1">
                      {top5.map((p) => {
                        const mv = Math.abs(parseFloat(p.market_value || "0"));
                        const pct = absMV ? (mv / absMV) * 100 : 0;
                        return (
                          <div key={`${p.symbol}-${positionAccountLabel(p)}`} className="flex items-center gap-2 text-[10px] font-mono">
                            <span className="text-gray-300 w-16 truncate">{p.symbol}</span>
                            <div className="flex-1 h-3 bg-[#1a1f2e] rounded overflow-hidden">
                              <div
                                className={`h-full ${p.side === "long" ? "bg-green-600/60" : "bg-red-600/60"}`}
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                            <span className="text-gray-400 w-20 text-right">{formatCurrency(mv)}</span>
                            <span className="text-gray-500 w-12 text-right">{pct.toFixed(1)}%</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* Pending Orders Panel */}
      <div className="card mt-3">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-3">
          Orders ({orders.length})
        </h2>
        {orders.length === 0 ? (
          <div className="text-gray-600 text-xs text-center py-4">No pending orders</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 text-[10px] uppercase border-b border-[#2a3040]">
                  <th className="text-left py-1.5 px-2 font-medium">Symbol</th>
                  <th className="text-left py-1.5 px-2 font-medium">Side</th>
                  <th className="text-left py-1.5 px-2 font-medium">Type</th>
                  <th className="text-right py-1.5 px-2 font-medium">Qty / Notional</th>
                  <th className="text-left py-1.5 px-2 font-medium">Status</th>
                  <th className="text-left py-1.5 px-2 font-medium">Submitted</th>
                  {selectedAccount === "all" && <th className="text-left py-1.5 px-2 font-medium">Account</th>}
                  <th className="text-right py-1.5 px-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} className="border-b border-[#1e2433] hover:bg-[#1f2537] transition">
                    <td className="py-1.5 px-2 text-gray-200 font-medium">{o.symbol}</td>
                    <td className="py-1.5 px-2">
                      <span className={o.side === "buy" ? "text-green-400" : "text-red-400"}>
                        {o.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-1.5 px-2 text-gray-400">{o.type}</td>
                    <td className="py-1.5 px-2 text-right text-gray-300">
                      {o.qty ? `${o.qty} shares` : o.notional ? formatCurrency(o.notional) : "—"}
                    </td>
                    <td className="py-1.5 px-2">
                      <span className="text-yellow-400/80 text-[10px] px-1.5 py-0.5 rounded bg-yellow-950/30">
                        {o.status}
                      </span>
                    </td>
                    <td className="py-1.5 px-2 text-gray-500">
                      {new Date(o.submitted_at).toLocaleTimeString()}
                    </td>
                    {selectedAccount === "all" && (
                      <td className="py-1.5 px-2 text-gray-600 text-[10px]">{o.account_label}</td>
                    )}
                    <td className="py-1.5 px-2 text-right">
                      <button
                        onClick={() => cancelOrder(o.id, o.account_label)}
                        className="text-[10px] px-2 py-0.5 rounded bg-red-950/40 text-red-400 hover:bg-red-900/60 hover:text-red-300 transition cursor-pointer"
                      >
                        Cancel
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* What-If Picks Tracker */}
      <WhatIfPicks />

      {/* Signal Feed Panel */}
      <SignalFeedPanel data={signalFeed} error={signalFeedError} />

      {/* Per-Account Breakdown (when viewing all) */}
      {selectedAccount === "all" && accountDetails.length > 1 && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-3">
          {accountDetails.map((acct) => {
            const acctPositions = positions.filter((p) => positionAccountLabel(p) === acct.label);
            const acctPnL = acctPositions.reduce(
              (s, p) => s + parseFloat(p.unrealized_pl || "0"), 0
            );
            const isLive = acct.is_live === true;
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
                      {acct.display_label || acct.label.replaceAll("_", " ")}
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
                    {[...acctPositions].sort((a, b) =>
                      Math.abs(parseFloat(b.unrealized_pl || "0")) - Math.abs(parseFloat(a.unrealized_pl || "0"))
                    ).slice(0, 5).map((p) => {
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
  children,
}: {
  label: string;
  value: string;
  valueClass?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="card !py-2 !px-3">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider">{label}</div>
      <div className={`text-sm font-medium mt-0.5 ${valueClass}`}>{value}</div>
      {children}
    </div>
  );
}

interface EquitySample {
  timestamp: number;
  timestamp_utc: string;
  equity: number;
}

function PnlSparkline({ account }: { account: string }) {
  const [samples, setSamples] = useState<EquitySample[]>([]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchJSON<{ samples: EquitySample[] }>(
          `/api/pnl-history?account=${account}`
        );
        if (!cancelled) setSamples(data.samples || []);
      } catch {
        // ignore fetch errors
      }
    };
    load();
    const iv = setInterval(load, 30000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [account]);

  if (samples.length < 2) return null;

  const equities = samples.map((s) => s.equity);
  const minE = Math.min(...equities);
  const maxE = Math.max(...equities);
  const range = maxE - minE || 1;
  const w = 200;
  const h = 40;
  const pad = 2;

  const points = samples.map((s, i) => {
    const x = pad + ((w - 2 * pad) * i) / (samples.length - 1);
    const y = h - pad - ((s.equity - minE) / range) * (h - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const first = equities[0];
  const last = equities[equities.length - 1];
  const color = last >= first ? "#22c55e" : "#ef4444";
  const pctChange = ((last - first) / first) * 100;

  return (
    <div className="mt-1.5 flex items-center gap-2">
      <svg width={w} height={h} className="block">
        <polyline
          points={points.join(" ")}
          fill="none"
          stroke={color}
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <polygon
          points={`${pad},${h - pad} ${points.join(" ")} ${w - pad},${h - pad}`}
          fill={color}
          fillOpacity="0.08"
        />
      </svg>
      <span className="text-[10px]" style={{ color }}>
        {pctChange >= 0 ? "+" : ""}{pctChange.toFixed(2)}%
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// What-If Picks Tracker — shows top suggested picks with live hypothetical P&L
// ---------------------------------------------------------------------------

interface WhatIfPick {
  symbol: string;
  direction: string;
  note: string;
  confidence: number;
  ev_pct: number;
  bucket: string;
  current_price: number;
  open_price: number;
  prev_close?: number;
  change_pct: number;
  hypothetical_investment: number;
  hypothetical_pnl: number;
  hypothetical_value: number;
  hypothetical_pnl_pct?: number;
  hypothetical_shares?: number;
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

function WhatIfPicks() {
  const [picks, setPicks] = useState<WhatIfPick[]>([]);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [loading, setLoading] = useState(true);
  const [briefTs, setBriefTs] = useState("");
  const [expanded, setExpanded] = useState(true);
  const prevPnlsRef = useRef<Record<string, number>>({});
  const [flashSymbol, setFlashSymbol] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchJSON<{
          picks: WhatIfPick[];
          scenarios?: Scenario[];
          brief_timestamp?: string;
        }>("/api/whatif-picks");
        if (cancelled) return;
        const newPicks = data.picks || [];
        setScenarios(data.scenarios || []);
        // Detect P&L changes for flash
        for (const p of newPicks) {
          const prev = prevPnlsRef.current[p.symbol];
          if (prev !== undefined && Math.abs(p.hypothetical_pnl - prev) > 0.005) {
            setFlashSymbol(p.symbol);
            setTimeout(() => setFlashSymbol(null), 600);
          }
          prevPnlsRef.current[p.symbol] = p.hypothetical_pnl;
        }
        setPicks(newPicks);
        setBriefTs(data.brief_timestamp || "");
      } catch {
        // ignore
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const iv = setInterval(load, 10000); // refresh every 10s
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const SHORT_BUCKETS = new Set(["TECH_SELLOFF", "AVIATION", "MELTDOWN_HEDGE"]);
  const bullPicks = picks.filter((p) => !SHORT_BUCKETS.has(p.bucket) && p.direction !== "SHORT");
  const bearPicks = picks.filter((p) => SHORT_BUCKETS.has(p.bucket) || p.direction === "SHORT");

  const totalHypPnl = picks.reduce((s, p) => s + (p.hypothetical_pnl || 0), 0);
  const totalHypValue = picks.reduce((s, p) => s + (p.hypothetical_value || 0), 0);
  const totalInvested = picks.length * 25;
  const bullPnl = bullPicks.reduce((s, p) => s + (p.hypothetical_pnl || 0), 0);
  const bearPnl = bearPicks.reduce((s, p) => s + (p.hypothetical_pnl || 0), 0);

  function renderPicksTable(items: WhatIfPick[], label: string, accentColor: string) {
    if (items.length === 0) return null;
    const subtotal = items.reduce((s, p) => s + (p.hypothetical_pnl || 0), 0);
    const subValue = items.reduce((s, p) => s + (p.hypothetical_value || 0), 0);
    return (
      <div className="mb-4">
        <div className="flex items-center gap-2 mb-2">
          <span className={`text-[10px] font-bold uppercase tracking-wider ${accentColor}`}>{label}</span>
          <span className={`text-[10px] font-medium ${subtotal >= 0 ? "text-green-400" : "text-red-400"}`}>
            {subtotal >= 0 ? "+" : ""}{formatCurrency(subtotal)}
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 text-[10px] uppercase border-b border-[#2a3040]">
                <th className="text-left py-1.5 px-2 font-medium">#</th>
                <th className="text-left py-1.5 px-2 font-medium">Symbol</th>
                <th className="text-left py-1.5 px-2 font-medium">Signal</th>
                <th className="text-right py-1.5 px-2 font-medium">Open</th>
                <th className="text-right py-1.5 px-2 font-medium">Now</th>
                <th className="text-right py-1.5 px-2 font-medium">vs Open</th>
                <th className="text-right py-1.5 px-2 font-medium">Hyp P&L</th>
                <th className="text-right py-1.5 px-2 font-medium">Hyp Value</th>
                <th className="text-center py-1.5 px-2 font-medium">Conf</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p, idx) => {
                const isFlashing = flashSymbol === p.symbol;
                return (
                  <tr
                    key={p.symbol}
                    className={`border-b border-[#1e2433] transition-all duration-300 ${
                      isFlashing ? "bg-blue-600/10" : "hover:bg-[#1f2537]"
                    }`}
                  >
                    <td className="py-1.5 px-2 text-gray-600">{idx + 1}</td>
                    <td className="py-1.5 px-2">
                      <div className="flex items-center gap-1.5">
                        <span className="text-gray-200 font-medium">{p.symbol}</span>
                        <span className={`text-[9px] px-1 rounded ${
                          p.direction === "LONG" ? "text-green-400 bg-green-950/40" : "text-red-400 bg-red-950/40"
                        }`}>
                          {p.direction}
                        </span>
                      </div>
                    </td>
                    <td className="py-1.5 px-2 text-gray-500 text-[10px] max-w-[200px] truncate" title={p.note}>
                      {p.note}
                    </td>
                    <td className="py-1.5 px-2 text-right text-gray-500">
                      {p.open_price ? `$${p.open_price.toFixed(2)}` : "\u2014"}
                    </td>
                    <td className="py-1.5 px-2 text-right text-gray-300">
                      {p.current_price ? `$${p.current_price.toFixed(2)}` : "\u2014"}
                    </td>
                    <td className={`py-1.5 px-2 text-right font-medium ${
                      p.change_pct > 0 ? "text-green-400" : p.change_pct < 0 ? "text-red-400" : "text-gray-400"
                    }`}>
                      {p.change_pct > 0 ? "+" : ""}{p.change_pct.toFixed(2)}%
                    </td>
                    <td className={`py-1.5 px-2 text-right font-medium transition-all duration-300 ${
                      isFlashing ? "scale-110" : ""
                    } ${p.hypothetical_pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {p.hypothetical_pnl >= 0 ? "+" : ""}{formatCurrency(p.hypothetical_pnl)}
                    </td>
                    <td className="py-1.5 px-2 text-right text-gray-300">
                      {formatCurrency(p.hypothetical_value)}
                    </td>
                    <td className="py-1.5 px-2 text-center">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                        p.confidence >= 70 ? "bg-green-950/40 text-green-400" :
                        p.confidence >= 50 ? "bg-yellow-950/40 text-yellow-400" :
                        "bg-gray-800/40 text-gray-500"
                      }`}>
                        {p.confidence}%
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div className="card mt-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center justify-between w-full cursor-pointer"
      >
        <div className="flex items-center gap-3">
          <h2 className="text-xs text-gray-500 uppercase tracking-wider">
            What-If Tracker
          </h2>
          {!loading && picks.length > 0 && (
            <div className="flex items-center gap-3">
              <span className={`text-[10px] font-medium ${bullPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                BULL {bullPnl >= 0 ? "+" : ""}{formatCurrency(bullPnl)}
              </span>
              <span className={`text-[10px] font-medium ${bearPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                BEAR {bearPnl >= 0 ? "+" : ""}{formatCurrency(bearPnl)}
              </span>
              <span className={`text-xs font-medium ${totalHypPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                NET {totalHypPnl >= 0 ? "+" : ""}{formatCurrency(totalHypPnl)}
              </span>
            </div>
          )}
        </div>
        <span className="text-gray-600 text-xs">{expanded ? "\u25BE" : "\u25B8"}</span>
      </button>

      {expanded && (
        <div className="mt-3">
          {loading ? (
            <div className="text-gray-600 text-xs text-center py-4">Loading picks...</div>
          ) : picks.length === 0 ? (
            <div className="text-gray-600 text-xs text-center py-4">No suggested picks available</div>
          ) : (
            <>
              <div className="text-[10px] text-gray-600 mb-3">
                Hypothetical $25/each if bought at today&apos;s open &bull; Brief: {briefTs ? new Date(briefTs).toLocaleTimeString() : "\u2014"}
              </div>

              {renderPicksTable(bullPicks, "Bull Picks — War Escalation", "text-green-500")}
              {renderPicksTable(bearPicks, "Bear / Hedge Picks — Meltdown Protection", "text-red-500")}

              {/* Top 5 Scenarios */}
              {scenarios.length > 0 && (
                <div className="mb-4">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-blue-400">Top 5 Scenarios</span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
                    {scenarios.map((s) => {
                      const borderColor = s.color === "red" ? "border-red-800/40" :
                        s.color === "green" ? "border-green-800/40" : "border-yellow-800/40";
                      const probColor = s.probability >= 30 ? "text-red-400" :
                        s.probability >= 15 ? "text-yellow-400" : "text-green-400";
                      return (
                        <div key={s.rank} className={`bg-[#0d1117] border ${borderColor} rounded-lg p-2.5`}>
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[10px] text-gray-500">#{s.rank}</span>
                            <span className={`text-[11px] font-bold ${probColor}`}>{s.probability}%</span>
                          </div>
                          <div className="text-[11px] font-medium text-gray-200 mb-1.5 leading-tight">
                            {s.name}
                          </div>
                          <div className="text-[10px] text-gray-500 mb-1.5 leading-snug">
                            {s.impact}
                          </div>
                          <div className="text-[10px] text-blue-400/80 leading-snug">
                            {s.action}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="flex items-center justify-between pt-2 border-t border-[#2a3040] text-[11px]">
                <span className="text-gray-500">
                  Total: {picks.length} picks @ $25 each = {formatCurrency(totalInvested)}
                </span>
                <div className="flex items-center gap-4">
                  <span className={`font-bold ${totalHypPnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                    P&L: {totalHypPnl >= 0 ? "+" : ""}{formatCurrency(totalHypPnl)}
                  </span>
                  <span className="text-gray-300 font-bold">
                    Value: {formatCurrency(totalHypValue)}
                  </span>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Signal Feed Panel
// ---------------------------------------------------------------------------

const BUCKET_LABELS: Record<string, string> = {
  OIL_SUPPLY: "OIL",
  SHIPPING: "SHIP",
  GEOPOLITICAL: "GEO",
  ENERGY_CASCADE: "ENRG",
  DEFENSE: "DEF",
  AVIATION: "AVIA",
  SAFE_HAVEN: "SAFE",
  INFLATION: "INFL",
  FOOD_CHAIN: "FOOD",
  TECH_SELLOFF: "TECH",
};

const BUCKET_ORDER = ["OIL_SUPPLY", "SHIPPING", "GEOPOLITICAL", "ENERGY_CASCADE", "DEFENSE", "AVIATION", "SAFE_HAVEN", "INFLATION", "FOOD_CHAIN", "TECH_SELLOFF"];

const MARKET_LABELS: Record<string, string> = {
  wti: "WTI",
  brent: "Brent",
  gold: "Gold",
  vix: "VIX",
  natgas: "NatGas",
  oil_wti: "WTI",
  oil_brent: "Brent",
};

function warIntensityColor(val: number): string {
  if (val < 3) return "text-green-400";
  if (val < 6) return "text-yellow-400";
  if (val < 8) return "text-orange-400";
  return "text-red-400";
}

function warIntensityBg(val: number): string {
  if (val < 3) return "bg-green-500/15 border-green-500/30";
  if (val < 6) return "bg-yellow-500/15 border-yellow-500/30";
  if (val < 8) return "bg-orange-500/15 border-orange-500/30";
  return "bg-red-500/15 border-red-500/30";
}

function bucketBarColor(score: number): string {
  if (score < 3) return "bg-green-500";
  if (score < 5) return "bg-yellow-500";
  if (score < 7) return "bg-orange-500";
  return "bg-red-500";
}

function SignalFeedPanel({ data, error }: { data: SignalFeedData | null; error: boolean }) {
  if (error && !data) {
    return (
      <div className="card mt-3">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-2">Signal Feed</h2>
        <div className="text-gray-600 text-xs text-center py-3">Signal feed unavailable</div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="card mt-3">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider mb-2">Signal Feed</h2>
        <div className="flex items-center justify-center py-3">
          <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      </div>
    );
  }

  const warIntensity = typeof data.war_intensity === "number" ? data.war_intensity : null;
  const marketWarIntensity = typeof (data as Record<string, unknown>).market_war_intensity === "number" ? (data as Record<string, unknown>).market_war_intensity as number : null;
  const bucketScores: Record<string, number> = data.bucket_scores && typeof data.bucket_scores === "object"
    ? data.bucket_scores as Record<string, number>
    : {};
  const marketData: Record<string, { price?: number; change_pct?: number }> =
    data.market_data && typeof data.market_data === "object"
      ? data.market_data as Record<string, { price?: number; change_pct?: number }>
      : {};
  const lastUpdated = data.last_updated || data.timestamp_utc || data._served_at || "";

  // Normalize bucket keys — match API keys directly, sort active (non-zero) first
  const normalizedBuckets: { key: string; label: string; score: number }[] = [];
  for (const bk of BUCKET_ORDER) {
    const score = bucketScores[bk] ?? bucketScores[bk.toLowerCase()] ?? null;
    if (score !== null) {
      normalizedBuckets.push({ key: bk, label: BUCKET_LABELS[bk] || bk, score });
    }
  }
  // Add any extra keys not in BUCKET_ORDER
  for (const [k, v] of Object.entries(bucketScores)) {
    const upper = k.toUpperCase().replace(/[^A-Z_]/g, "_");
    if (!BUCKET_ORDER.includes(upper)) {
      normalizedBuckets.push({ key: upper, label: k.substring(0, 4).toUpperCase(), score: v });
    }
  }
  // Sort: highest score first so active signals surface to top
  normalizedBuckets.sort((a, b) => b.score - a.score);

  const marketEntries = Object.entries(marketData).map(([key, val]) => ({
    label: MARKET_LABELS[key.toLowerCase()] || key,
    price: val?.price,
    changePct: val?.change_pct,
  }));

  return (
    <div className="card mt-3">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs text-gray-500 uppercase tracking-wider">Signal Feed</h2>
        {lastUpdated && (() => {
          try {
            const ageMs = Date.now() - new Date(lastUpdated).getTime();
            const ageMins = Math.floor(ageMs / 60000);
            const stale = ageMs > 20 * 60 * 1000;
            return (
              <span className={`text-[9px] ${stale ? "text-yellow-500" : "text-gray-600"}`}>
                {stale ? `⚠ stale ${ageMins}m ago` : new Date(lastUpdated).toLocaleTimeString()}
              </span>
            );
          } catch { return null; }
        })()}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
        {/* War Intensity */}
        <div className="md:col-span-2 flex flex-col items-center justify-center">
          {warIntensity !== null ? (
            <div className={`rounded-lg border px-4 py-3 text-center w-full ${warIntensityBg(warIntensity)}`}>
              <div className="text-[9px] text-gray-500 uppercase tracking-wider mb-1">War Intensity</div>
              <div className={`text-3xl font-bold tabular-nums ${warIntensityColor(warIntensity)}`}>
                {warIntensity.toFixed(1)}
              </div>
              <div className={`text-[10px] mt-0.5 ${warIntensityColor(warIntensity)}`}>
                {warIntensity < 3 ? "LOW" : warIntensity < 6 ? "MODERATE" : warIntensity < 8 ? "HIGH" : "CRITICAL"}
              </div>
              {marketWarIntensity !== null && marketWarIntensity > warIntensity + 0.5 && (
                <div className="text-[9px] mt-1 text-yellow-500 opacity-80">
                  mkt: {marketWarIntensity.toFixed(1)}
                </div>
              )}
            </div>
          ) : (
            <div className="text-gray-600 text-xs">No intensity data</div>
          )}
        </div>

        {/* Bucket Scores */}
        <div className="md:col-span-5">
          <div className="text-[9px] text-gray-500 uppercase tracking-wider mb-2">Bucket Scores</div>
          {normalizedBuckets.length > 0 ? (
            <div className="space-y-1.5">
              {normalizedBuckets.map((b) => (
                <div key={b.key} className="flex items-center gap-2 text-[10px] font-mono">
                  <span className="text-gray-400 w-10 text-right flex-shrink-0">{b.label}</span>
                  <div className="flex-1 h-3.5 bg-[#1a1f2e] rounded overflow-hidden">
                    <div
                      className={`h-full rounded-r ${bucketBarColor(b.score)} transition-all duration-500`}
                      style={{ width: `${Math.min((b.score / 10) * 100, 100)}%` }}
                    />
                  </div>
                  <span className="text-gray-300 w-8 text-right flex-shrink-0">{b.score.toFixed(1)}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-gray-600 text-xs py-2">No bucket data</div>
          )}
        </div>

        {/* Key Markets */}
        <div className="md:col-span-5">
          <div className="text-[9px] text-gray-500 uppercase tracking-wider mb-2">Key Markets</div>
          {marketEntries.length > 0 ? (
            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
              {marketEntries.map((m) => (
                <div key={m.label} className="flex items-center justify-between text-[11px] font-mono">
                  <span className="text-gray-500">{m.label}</span>
                  <div className="flex items-center gap-2">
                    {m.price != null && (
                      <span className="text-gray-300">
                        {m.label === "VIX" ? m.price.toFixed(2) : `$${m.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                      </span>
                    )}
                    {m.changePct != null && (
                      <span className={`text-[10px] ${m.changePct >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {m.changePct >= 0 ? "+" : ""}{m.changePct.toFixed(2)}%
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-gray-600 text-xs py-2">No market data</div>
          )}
        </div>
      </div>
    </div>
  );
}
