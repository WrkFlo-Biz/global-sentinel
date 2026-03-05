const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "";

async function fetchJSON<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store", headers });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export interface Heartbeat {
  timestamp_utc: string;
  status: string;
  mode: string;
  cycle: number;
}

export interface ComponentScores {
  geopolitical_tension: number;
  market_volatility: number;
  currency_stress: number;
  commodity_shock: number;
  policy_uncertainty: number;
  labor_disruption: number;
  credit_spread: number;
  liquidity_stress: number;
}

export interface BridgeSummary {
  aviation_disruption_count: number;
  microstructure_symbols: number;
  gdelt_event_count: number;
  finnhub_packet_count: number;
}

export interface Scorecard {
  schema_version: string;
  timestamp_utc: string;
  cycle: number;
  mode: string;
  regime_shift_probability: number;
  component_scores: ComponentScores;
  confidence: number;
  evidence: string[];
  data_freshness_status: Record<string, boolean>;
  risk_gate_status: string;
  manual_veto_status: boolean;
  kill_switch_status: boolean;
  fallback_mode_status: boolean;
  shadow_execution_eligible: boolean;
  bridge_summary: BridgeSummary;
  time_window?: {
    current_window: string;
    window_priority: string;
    timestamp_et_hhmm: string;
  };
}

export interface TimelinePoint {
  timestamp_utc: string;
  cycle: number;
  mode: string;
  regime_p: number;
  confidence: number;
  components: ComponentScores;
  bridge_summary: BridgeSummary;
  shadow_eligible: boolean;
  fallback: boolean;
}

export interface Controls {
  kill_switch: { active: boolean; reason?: string; activated_at?: string };
  manual_veto: { active: boolean; reason?: string; activated_at?: string };
}

export interface GraduationCheck {
  check: string;
  pass: boolean;
  actual: number | string;
  required: number | string;
  insufficient_data?: boolean;
}

export interface GraduationReport {
  stage: string;
  overall_pass: boolean;
  checks: GraduationCheck[];
  summary: { total_checks: number; passed: number; failed: number };
}

export interface PortfolioPosition {
  symbol: string;
  qty: number;
  side: string;
  avg_entry_price: number;
  current_price: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  market_value: number;
}

export interface PortfolioData {
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  positions: PortfolioPosition[];
  timestamp_utc: string;
  error?: string;
}

export const api = {
  heartbeat: () => fetchJSON<Heartbeat>("/api/heartbeat"),
  controls: () => fetchJSON<Controls>("/api/controls"),
  latestScorecard: () => fetchJSON<Scorecard>("/api/scorecard/latest"),
  timeline: (limit = 200) => fetchJSON<TimelinePoint[]>(`/api/scorecards/timeline?limit=${limit}`),
  bridges: () => fetchJSON<any>("/api/bridges"),
  orders: (limit = 50) => fetchJSON<any[]>(`/api/execution/orders?limit=${limit}`),
  bindings: (limit = 50) => fetchJSON<any[]>(`/api/execution/bindings?limit=${limit}`),
  alerts: (limit = 30) => fetchJSON<any[]>(`/api/alerts?limit=${limit}`),
  graduation: () => fetchJSON<GraduationReport>("/api/graduation"),
  thresholds: () => fetchJSON<any>("/api/thresholds"),
  timeWindow: () => fetchJSON<any>("/api/time_window"),
  portfolio: () => fetchJSON<PortfolioData>("/api/portfolio"),
  tradeAnalysis: () => fetchJSON<TradeAnalysis>("/api/trade-analysis"),
  performance: () => fetchJSON<PerformanceData>("/api/performance"),
};

export interface PerformanceData {
  timestamp_utc?: string;
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
  avg_pnl_per_trade: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number | null;
  by_symbol: Record<string, { trades: number; wins: number; pnl: number }>;
  open_positions_snapshot?: any;
  error?: string;
}

export interface TradeIdea {
  symbol: string;
  side: string;
  reason: string;
  historical_win_rate: number;
  confidence_adjusted_score: number;
  current_price?: number;
  daily_vol_pct?: number;
  entry?: number;
  target?: number;
  stop?: number;
  risk_reward?: number;
}

export interface SectorRotation {
  sector: string;
  signal: string;
  strength: number;
  rationale: string;
  symbols: string[];
}

export interface HistoricalExample {
  event: string;
  result: string;
}

export interface TradeAnalysis {
  timestamp_utc: string;
  mode: string;
  regime_p: number;
  transition: string;
  playbook_thesis: string;
  trade_ideas: TradeIdea[];
  sector_analysis: SectorRotation[];
  risk_assessment: {
    regime_p: number;
    confidence: number;
    mode: string;
    position_sizing: string;
    max_position_pct: number;
    time_window: string;
    window_quality: string;
    risk_factors: string[];
  };
  historical_examples: HistoricalExample[];
  evidence_summary: string[];
  confidence: number;
  advisory_only: boolean;
  error?: string;
}

export function connectWS(onMessage: (data: any) => void): WebSocket | null {
  const origin = typeof window !== "undefined" ? window.location.origin : "http://localhost:8501";
  const wsBase = (process.env.NEXT_PUBLIC_API_URL || origin).replace("http", "ws") + "/ws";
  const wsUrl = API_KEY ? `${wsBase}?api_key=${API_KEY}` : wsBase;
  try {
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (e) => {
      try { onMessage(JSON.parse(e.data)); } catch {}
    };
    ws.onclose = () => {
      setTimeout(() => connectWS(onMessage), 5000);
    };
    return ws;
  } catch {
    return null;
  }
}
