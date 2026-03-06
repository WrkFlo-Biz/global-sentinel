"use client";

import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import type { PortfolioData } from "@/lib/api";

// ──────────────────────────────────────────────────────────────
// ASSET CLASS classification (top-level: Stocks / ETFs / Crypto / Bonds / Commodities / Other)
// ──────────────────────────────────────────────────────────────

const KNOWN_ETFS = new Set([
  // Index / Broad Market
  "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "VT", "VXUS", "RSP", "MDY", "IJR", "SCHB", "ITOT",
  // Sector SPDR
  "XLF", "XLK", "XLV", "XLI", "XLU", "XLP", "XLB", "XLC", "XLRE", "XLE", "XLY",
  // Thematic / Industry
  "HACK", "CIBR", "SKYY", "BOTZ", "ARKK", "ARKW", "ARKF", "ARKG", "ARKQ",
  "PPA", "XAR", "ITA",
  "IBB", "XBI",
  "VNQ", "VNQI",
  "JETS", "IYT",
  // International
  "EEM", "EFA", "FXI", "INDA", "EWJ", "EWZ", "EWG", "EWU", "IEMG", "VWO",
  // Bond ETFs
  "TLT", "TBT", "SHY", "IEF", "AGG", "BND", "HYG", "JNK", "LQD", "TIPS", "BNDX", "VCSH", "VCIT",
  "VGSH", "VGIT", "VGLT", "EMB", "MUB", "SHV", "GOVT", "SCHZ", "FLOT", "IGIB",
  // Commodity ETFs
  "GLD", "IAU", "SLV", "USO", "UNG", "WEAT", "CORN", "DBA", "DBC", "PDBC", "BNO",
  "GLDM", "SGOL", "SIVR", "PPLT", "PALL", "UGA",
  // Leveraged / Inverse / Volatility
  "UVXY", "SQQQ", "SPXU", "TQQQ", "SDOW", "SPXL", "QLD", "SSO", "SDS", "SH", "PSQ",
  "VIXY", "SVXY", "VXX",
  // Uranium / Nuclear
  "URA", "URNM",
  // Mining / Materials
  "GDX", "GDXJ", "SIL", "SILJ", "PICK", "XME", "REMX", "LIT",
  // Dividends / Income
  "DVY", "VYM", "SCHD", "HDV", "JEPI", "JEPQ",
  // Other
  "XOP", "OIH", "KRE", "KBE", "SMH", "SOXX", "IGV", "KWEB", "MCHI",
]);

const KNOWN_CRYPTO = new Set([
  "BTC", "ETH", "SOL", "DOGE", "AVAX", "MATIC", "ADA", "DOT", "LINK", "UNI",
  "AAVE", "ATOM", "NEAR", "APT", "ARB", "OP", "SUI", "SEI", "TIA", "INJ",
  "FIL", "ALGO", "XRP", "LTC", "BCH", "SHIB", "PEPE", "BONK", "WIF", "RENDER",
  "FET", "GRT", "MKR", "CRV", "COMP", "SNX", "SUSHI", "YFI", "BAL",
  // With USD suffix (Alpaca format)
  "BTCUSD", "ETHUSD", "SOLUSD", "DOGEUSD", "AVAXUSD", "MATICUSD", "ADAUSD",
  "DOTUSD", "LINKUSD", "UNIUSD", "AAVEUSD", "XRPUSD", "LTCUSD", "BCHUSD",
  "SHIBUSD", "PEPEUSD",
]);

const BOND_ETFS = new Set([
  "TLT", "TBT", "SHY", "IEF", "AGG", "BND", "HYG", "JNK", "LQD", "TIPS",
  "BNDX", "VCSH", "VCIT", "VGSH", "VGIT", "VGLT", "EMB", "MUB", "SHV",
  "GOVT", "SCHZ", "FLOT", "IGIB",
]);

const COMMODITY_ETFS = new Set([
  "GLD", "IAU", "GLDM", "SGOL", "SLV", "SIVR", "PPLT", "PALL",
  "USO", "BNO", "UNG", "UGA",
  "WEAT", "CORN", "DBA", "DBC", "PDBC",
  "GDX", "GDXJ", "SIL", "SILJ",
]);

function classifyAssetClass(symbol: string): string {
  const upper = symbol.toUpperCase();
  // Crypto: known tickers or *USD suffix
  if (KNOWN_CRYPTO.has(upper) || upper.endsWith("USD") && upper.length > 4) return "Crypto";
  // Bonds
  if (BOND_ETFS.has(upper)) return "Bonds";
  // Commodities
  if (COMMODITY_ETFS.has(upper)) return "Commodities";
  // ETFs (general)
  if (KNOWN_ETFS.has(upper)) return "ETFs";
  // Everything else is a stock
  return "Stocks";
}

// ──────────────────────────────────────────────────────────────
// SECTOR classification (detailed breakdown)
// ──────────────────────────────────────────────────────────────

const SECTOR_MAP: Record<string, string> = {
  // ── Energy ──
  XOM: "Energy", XLE: "Energy", CVX: "Energy", COP: "Energy", OXY: "Energy",
  XOP: "Energy", MPC: "Energy", PSX: "Energy", OIH: "Energy", SLB: "Energy",
  PBF: "Energy", UGA: "Energy", INSW: "Energy", STNG: "Energy", FRO: "Energy",
  USO: "Energy", BNO: "Energy", UNG: "Energy", VLO: "Energy", HFC: "Energy",
  DK: "Energy", CTRA: "Energy", HAL: "Energy", DVN: "Energy", FANG: "Energy",
  EOG: "Energy", PXD: "Energy", MRO: "Energy", APA: "Energy",

  // ── Materials / Mining ──
  GDX: "Materials", GDXJ: "Materials", GOLD: "Materials", SLV: "Materials",
  CCJ: "Materials", URA: "Materials", URNM: "Materials", UUUU: "Materials",
  LEU: "Materials", NEM: "Materials", FCX: "Materials", SCCO: "Materials",
  SIL: "Materials", SILJ: "Materials", XME: "Materials", PICK: "Materials",
  REMX: "Materials", LIT: "Materials", ALB: "Materials", SQM: "Materials",
  GLD: "Gold", IAU: "Gold", GLDM: "Gold", SGOL: "Gold",

  // ── Defense / Aerospace ──
  AVAV: "Defense", PPA: "Defense", XAR: "Defense", HII: "Defense", LHX: "Defense",
  LDOS: "Defense", KTOS: "Defense", ITA: "Defense",
  BA: "Defense", RTX: "Defense", LMT: "Defense", GD: "Defense", NOC: "Defense",
  RKLB: "Defense", TDG: "Defense", HWM: "Defense", SPR: "Defense",

  // ── Cyber / Tech ──
  PANW: "Cyber/Tech", HACK: "Cyber/Tech", CRWD: "Cyber/Tech", CIBR: "Cyber/Tech",
  FTNT: "Cyber/Tech", PLTR: "Cyber/Tech", ZS: "Cyber/Tech",

  // ── Technology (Broad) ──
  AAPL: "Technology", MSFT: "Technology", GOOGL: "Technology", GOOG: "Technology",
  META: "Technology", NVDA: "Technology", AMD: "Technology", INTC: "Technology",
  TSM: "Technology", AVGO: "Technology", QCOM: "Technology", MU: "Technology",
  AMAT: "Technology", LRCX: "Technology", KLAC: "Technology", MRVL: "Technology",
  ADBE: "Technology", CRM: "Technology", ORCL: "Technology", NOW: "Technology",
  SNOW: "Technology", NET: "Technology", DDOG: "Technology", SHOP: "Technology",
  SQ: "Technology", PYPL: "Technology", UBER: "Technology", LYFT: "Technology",
  XLK: "Technology", SMH: "Technology", SOXX: "Technology", IGV: "Technology",
  ARKK: "Technology", ARKW: "Technology",

  // ── Healthcare ──
  XLV: "Healthcare", IBB: "Healthcare", XBI: "Healthcare",
  JNJ: "Healthcare", UNH: "Healthcare", PFE: "Healthcare", MRNA: "Healthcare",
  LLY: "Healthcare", ABBV: "Healthcare", BMY: "Healthcare", MRK: "Healthcare",
  TMO: "Healthcare", DHR: "Healthcare", ABT: "Healthcare", AMGN: "Healthcare",
  GILD: "Healthcare", REGN: "Healthcare", VRTX: "Healthcare", ISRG: "Healthcare",
  ARKG: "Healthcare",

  // ── Financials ──
  XLF: "Financials", JPM: "Financials", BAC: "Financials", GS: "Financials",
  MS: "Financials", V: "Financials", MA: "Financials", C: "Financials",
  WFC: "Financials", BLK: "Financials", SCHW: "Financials", AXP: "Financials",
  COF: "Financials", USB: "Financials", PNC: "Financials", TFC: "Financials",
  KRE: "Financials", KBE: "Financials", AIG: "Financials",
  "BRK-B": "Financials", "BRK.B": "Financials", RE: "Financials",
  RNR: "Financials", ACGL: "Financials", ARKF: "Financials",

  // ── Consumer / Retail ──
  AMZN: "Consumer", TSLA: "Consumer", WMT: "Consumer", COST: "Consumer",
  TGT: "Consumer", HD: "Consumer", LOW: "Consumer", NKE: "Consumer",
  SBUX: "Consumer", MCD: "Consumer", DIS: "Consumer", NFLX: "Consumer",
  XLY: "Consumer", XLP: "Consumer",
  BKNG: "Consumer", EXPE: "Consumer", ABNB: "Consumer",
  CCL: "Consumer", RCL: "Consumer",

  // ── Industrials / Transport ──
  XLI: "Industrials", CAT: "Industrials", DE: "Industrials", HON: "Industrials",
  UNP: "Industrials", UPS: "Industrials", FDX: "Industrials",
  DAL: "Aviation", UAL: "Aviation", AAL: "Aviation", LUV: "Aviation",
  JBLU: "Aviation", JETS: "Aviation", IYT: "Industrials",
  ZIM: "Shipping", GOGL: "Shipping", MATX: "Shipping",

  // ── Real Estate ──
  XLRE: "Real Estate", VNQ: "Real Estate", VNQI: "Real Estate",
  O: "Real Estate", AMT: "Real Estate", PLD: "Real Estate", CCI: "Real Estate",
  EQIX: "Real Estate", SPG: "Real Estate", PSA: "Real Estate",

  // ── Utilities ──
  XLU: "Utilities", NEE: "Utilities", DUK: "Utilities", SO: "Utilities",
  D: "Utilities", AEP: "Utilities", EXC: "Utilities",

  // ── Communication ──
  XLC: "Communication", T: "Communication", VZ: "Communication", TMUS: "Communication",

  // ── Agriculture ──
  WEAT: "Agriculture", DBA: "Agriculture", CORN: "Agriculture",

  // ── Bonds / Fixed Income ──
  TLT: "Bonds", TBT: "Bonds", SHY: "Bonds", IEF: "Bonds",
  AGG: "Bonds", BND: "Bonds", HYG: "Bonds", JNK: "Bonds",
  LQD: "Bonds", TIPS: "Bonds", BNDX: "Bonds", VCSH: "Bonds",
  VCIT: "Bonds", VGSH: "Bonds", VGIT: "Bonds", VGLT: "Bonds",
  EMB: "Bonds", MUB: "Bonds", SHV: "Bonds", GOVT: "Bonds",
  SCHZ: "Bonds", FLOT: "Bonds", IGIB: "Bonds",

  // ── Index ETFs ──
  SPY: "Index", QQQ: "Index", IWM: "Index", DIA: "Index",
  VOO: "Index", VTI: "Index", VT: "Index", VXUS: "Index",
  RSP: "Index", MDY: "Index", IJR: "Index", SCHB: "Index",
  ITOT: "Index",
  EEM: "Index", EFA: "Index", FXI: "Index", INDA: "Index",
  EWJ: "Index", EWZ: "Index", EWG: "Index", EWU: "Index",
  IEMG: "Index", VWO: "Index", MCHI: "Index", KWEB: "Index",

  // ── Leveraged / Volatility ──
  UVXY: "Leveraged/Vol", SQQQ: "Leveraged/Vol", SPXU: "Leveraged/Vol",
  TQQQ: "Leveraged/Vol", SDOW: "Leveraged/Vol", SPXL: "Leveraged/Vol",
  QLD: "Leveraged/Vol", SSO: "Leveraged/Vol", SDS: "Leveraged/Vol",
  SH: "Leveraged/Vol", PSQ: "Leveraged/Vol",
  VIXY: "Leveraged/Vol", SVXY: "Leveraged/Vol", VXX: "Leveraged/Vol",

  // ── Crypto (spot tickers if held via Alpaca) ──
  BTC: "Crypto", ETH: "Crypto", SOL: "Crypto", DOGE: "Crypto",
  AVAX: "Crypto", MATIC: "Crypto", ADA: "Crypto", DOT: "Crypto",
  LINK: "Crypto", UNI: "Crypto", AAVE: "Crypto", XRP: "Crypto",
  LTC: "Crypto", BCH: "Crypto", SHIB: "Crypto", PEPE: "Crypto",
  ATOM: "Crypto", NEAR: "Crypto", APT: "Crypto", ARB: "Crypto",
  OP: "Crypto", SUI: "Crypto", SEI: "Crypto", TIA: "Crypto",
  INJ: "Crypto", FIL: "Crypto", ALGO: "Crypto", BONK: "Crypto",
  WIF: "Crypto", RENDER: "Crypto", FET: "Crypto", GRT: "Crypto",
  MKR: "Crypto", CRV: "Crypto", COMP: "Crypto", SNX: "Crypto",
  SUSHI: "Crypto", YFI: "Crypto", BAL: "Crypto",
  BTCUSD: "Crypto", ETHUSD: "Crypto", SOLUSD: "Crypto", DOGEUSD: "Crypto",
  AVAXUSD: "Crypto", MATICUSD: "Crypto", ADAUSD: "Crypto", DOTUSD: "Crypto",
  LINKUSD: "Crypto", UNIUSD: "Crypto", AAVEUSD: "Crypto", XRPUSD: "Crypto",
  LTCUSD: "Crypto", BCHUSD: "Crypto", SHIBUSD: "Crypto", PEPEUSD: "Crypto",

  // ── Dividends / Income ──
  DVY: "Dividends", VYM: "Dividends", SCHD: "Dividends", HDV: "Dividends",
  JEPI: "Dividends", JEPQ: "Dividends",
};

const SECTOR_COLORS: Record<string, string> = {
  "Energy":        "#f59e0b",
  "Materials":     "#8b5cf6",
  "Defense":       "#3b82f6",
  "Cyber/Tech":    "#06b6d4",
  "Technology":    "#818cf8",
  "Healthcare":    "#ec4899",
  "Financials":    "#14b8a6",
  "Consumer":      "#f472b6",
  "Industrials":   "#78716c",
  "Aviation":      "#38bdf8",
  "Shipping":      "#0ea5e9",
  "Real Estate":   "#a3e635",
  "Utilities":     "#facc15",
  "Communication": "#c084fc",
  "Agriculture":   "#10b981",
  "Bonds":         "#a78bfa",
  "Index":         "#6b7280",
  "Leveraged/Vol": "#ef4444",
  "Crypto":        "#f97316",
  "Gold":          "#fbbf24",
  "Dividends":     "#34d399",
  "Other":         "#4b5563",
};

const ASSET_CLASS_COLORS: Record<string, string> = {
  "Stocks":       "#818cf8",
  "ETFs":         "#3b82f6",
  "Crypto":       "#f97316",
  "Bonds":        "#a78bfa",
  "Commodities":  "#fbbf24",
  "Other":        "#4b5563",
};

// ──────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────

interface ChartSlice {
  name: string;
  value: number;
  pct: number;
  symbols: string[];
  color: string;
}

interface CustomTooltipProps {
  active?: boolean;
  payload?: any[];
}

// ──────────────────────────────────────────────────────────────
// Tooltip
// ──────────────────────────────────────────────────────────────

function ChartTooltip({ active, payload }: CustomTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  if (!d) return null;
  return (
    <div className="bg-[#1a1f2e] border border-[#2a3040] rounded-lg px-3 py-2 text-xs shadow-xl z-50">
      <div className="font-medium mb-1" style={{ color: d.color }}>{d.name}</div>
      <div className="text-gray-300">${Math.abs(d.value).toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
      <div className="text-gray-500">{d.pct.toFixed(1)}% of portfolio</div>
      <div className="text-gray-600 text-[9px] mt-0.5 max-w-[200px] break-words">{d.symbols.join(", ")}</div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// Build slices helper
// ──────────────────────────────────────────────────────────────

function buildSlices(
  positions: PortfolioData["positions"],
  classifier: (symbol: string) => string,
  colorMap: Record<string, string>,
): { slices: ChartSlice[]; totalValue: number } {
  const bucketMap = new Map<string, { value: number; symbols: string[] }>();
  let totalValue = 0;

  for (const pos of positions) {
    const bucket = classifier(pos.symbol);
    const mv = Math.abs(pos.market_value);
    totalValue += mv;
    const existing = bucketMap.get(bucket);
    if (existing) {
      existing.value += mv;
      if (!existing.symbols.includes(pos.symbol)) existing.symbols.push(pos.symbol);
    } else {
      bucketMap.set(bucket, { value: mv, symbols: [pos.symbol] });
    }
  }

  const slices: ChartSlice[] = Array.from(bucketMap.entries())
    .map(([name, data]) => ({
      name,
      value: data.value,
      pct: totalValue > 0 ? (data.value / totalValue) * 100 : 0,
      symbols: data.symbols,
      color: colorMap[name] || colorMap["Other"] || "#4b5563",
    }))
    .sort((a, b) => b.value - a.value);

  return { slices, totalValue };
}

// ──────────────────────────────────────────────────────────────
// Donut + Legend sub-component
// ──────────────────────────────────────────────────────────────

function DonutWithLegend({ slices, totalValue }: { slices: ChartSlice[]; totalValue: number }) {
  return (
    <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
      {/* Donut */}
      <div className="w-full sm:w-[140px] h-[140px] shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="name"
              cx="50%"
              cy="50%"
              innerRadius={35}
              outerRadius={60}
              paddingAngle={2}
              strokeWidth={0}
            >
              {slices.map((s, i) => (
                <Cell key={i} fill={s.color} fillOpacity={0.85} />
              ))}
            </Pie>
            <Tooltip content={<ChartTooltip />} />
          </PieChart>
        </ResponsiveContainer>
      </div>

      {/* Legend */}
      <div className="flex-1 space-y-1 w-full max-h-[180px] overflow-y-auto pr-1">
        {slices.map((s) => (
          <div key={s.name} className="flex items-center justify-between text-[10px]">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: s.color }} />
              <span className="text-gray-300 truncate">{s.name}</span>
            </div>
            <div className="flex items-center gap-2 shrink-0 ml-2">
              <span className="text-gray-500 tabular-nums">{s.pct.toFixed(0)}%</span>
              <span className="text-gray-400 tabular-nums">${(s.value / 1000).toFixed(1)}k</span>
            </div>
          </div>
        ))}
        <div className="flex items-center justify-between text-[10px] pt-1 border-t border-[#2a3040]">
          <span className="text-gray-500">Total Exposure</span>
          <span className="text-gray-300 font-medium tabular-nums">${(totalValue / 1000).toFixed(1)}k</span>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// Main component
// ──────────────────────────────────────────────────────────────

export default function SectorExposure({ portfolio }: { portfolio: PortfolioData | null }) {

  if (!portfolio || !portfolio.positions || portfolio.positions.length === 0) {
    return <div className="text-gray-600 text-xs">No positions to analyze</div>;
  }

  const sectorClassifier = (symbol: string): string => SECTOR_MAP[symbol] || "Other";

  const { slices: assetClassSlices, totalValue: acTotal } = buildSlices(
    portfolio.positions,
    classifyAssetClass,
    ASSET_CLASS_COLORS,
  );

  const { slices: sectorSlices, totalValue: sectorTotal } = buildSlices(
    portfolio.positions,
    sectorClassifier,
    SECTOR_COLORS,
  );

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {/* Asset Class donut */}
      <div>
        <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2 font-medium">By Asset Class</h3>
        <DonutWithLegend slices={assetClassSlices} totalValue={acTotal} />
      </div>

      {/* Sector donut */}
      <div>
        <h3 className="text-[10px] text-gray-500 uppercase tracking-wider mb-2 font-medium">By Sector</h3>
        <DonutWithLegend slices={sectorSlices} totalValue={sectorTotal} />
      </div>
    </div>
  );
}
