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

export interface BridgeOperatorStatus {
  label: string;
  status: "live" | "source_live" | "empty" | "snapshot_only" | "no_snapshot" | "stale" | "unknown";
  display_status: string;
  fresh?: boolean;
  snapshot_recent?: boolean;
  count?: number;
  detail?: string;
  exists?: boolean;
  file_count?: number;
  json_file_count?: number;
  hash_file_count?: number;
  latest_file?: string | null;
  latest_age_min?: number | null;
}

export interface BridgeStatusResponse {
  bridges: Record<string, BridgeOperatorStatus>;
  bridge_summary: Record<string, number | string | undefined>;
  data_freshness: Record<string, boolean>;
  fallback_mode: boolean;
  timestamp_utc?: string;
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
  account?: string;
  account_label?: string;
  broker?: string;
}

export interface PortfolioAccountError {
  label: string;
  error: string;
}

export interface PortfolioAccountDetail {
  label: string;
  status?: string;
  broker?: string;
  display_label?: string;
  account_number?: string;
  is_live?: boolean;
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  positions: PortfolioPosition[];
  position_count: number;
  timestamp_utc?: string;
  source_timestamp_utc?: string;
  fetched_at_utc?: string;
  cache_age_ms?: number;
  cache_status?: string;
  error?: string;
}

export interface PortfolioConsistency {
  account_count_requested: number;
  account_count_success: number;
  account_count_error: number;
  position_count_total: number;
  position_count_total_from_accounts?: number;
  position_count_by_account: Record<string, number>;
  requested_accounts?: string[];
  accounts_match_requested?: boolean;
  positions_match_total?: boolean;
  has_account_errors?: boolean;
}

export interface PortfolioData {
  schema_version?: string;
  status?: string;
  equity: number;
  cash: number;
  buying_power: number;
  portfolio_value: number;
  positions: PortfolioPosition[];
  accounts?: PortfolioAccountDetail[];
  account_errors?: PortfolioAccountError[];
  position_count_total?: number;
  position_count_by_account?: Record<string, number>;
  account_count?: number;
  consistency?: PortfolioConsistency;
  timestamp_utc: string;
  source_timestamp_utc?: string;
  latest_source_timestamp_utc?: string;
  fetched_at_utc?: string;
  cache_age_ms?: number;
  cache_status?: string;
  pricing_summary?: {
    priced_position_count: number;
    position_count: number;
    latest_pricing_timestamp_utc?: string | null;
    latest_pricing_age_seconds?: number | null;
    oldest_pricing_timestamp_utc?: string | null;
    oldest_pricing_age_seconds?: number | null;
    delayed_position_count: number;
    stale_position_count: number;
    market_data_health: "live" | "delayed" | "degraded" | "stale";
    stream_error_accounts?: string[];
    stream_degraded_accounts?: string[];
  };
  stream_health?: Record<string, any>;
  error?: string;
}

export interface PortfolioHistoryData {
  schema_version?: string;
  account?: string;
  requested_period?: string;
  requested_timeframe?: string;
  timestamp: number[];
  equity: number[];
  profit_loss: number[];
  profit_loss_pct: number[];
  base_value: number;
  timeframe: string;
  timestamp_utc?: string;
  source_timestamp_utc?: string;
  latest_source_timestamp_utc?: string;
  fetched_at_utc?: string;
  cache_age_ms?: number;
  cache_status?: string;
  accounts?: Record<string, PortfolioHistoryData | { error: string }>;
  error?: string;
}

export interface ConsciousnessData {
  timestamp_utc: string;
  source: string;
  max_z: number;
  mean_z: number;
  node_count: number;
  coherence_level: string; // "random" | "low" | "moderate" | "high" | "extreme"
  regional_z: Record<string, number>;
  regional_spikes: Array<{
    region: string;
    z_score: number;
    level: string;
    predicted_markets: string[];
    market_zone: string;
  }>;
  evidence: string[];
  narrative_velocity?: number;
  dominant_narrative?: string;
  sentinel_signal?: string;
}

export interface ExecutionModeData {
  strategies: Record<string, {
    description: string;
    bot: string;
    bot_username: string;
    holding_period: string;
    profit_target_pct: number;
    stop_loss_pct: number;
    max_positions: number;
  }>;
  execution_mode: Record<string, string>;  // "auto" | "manual"
  bot_permissions: Record<string, any>;
}

export interface ExecutionRoutingSummary {
  event_count: number;
  processed_candidate_count: number;
  submit_attempt_count: number;
  submit_success_count: number;
  broker_rejected_count: number;
  skipped_count: number;
  error_count: number;
  candidate_conversion_rate: number;
  broker_accept_rate: number;
  skip_or_block_rate: number;
  block_reason_category_counts: Record<string, number>;
  raw_block_reason_counts: Record<string, number>;
}

export interface ExecutionLiveOrderAccountSummary {
  order_count_total: number;
  filled: number;
  partially_filled: number;
  open: number;
  rejected: number;
  canceled: number;
  expired: number;
  other: number;
  fill_rate_any: number;
  fill_rate_full: number;
}

export interface ExecutionLiveOrdersSummary {
  status: string;
  lookback_hours: number;
  sample_window_start_utc: string;
  account_count: number;
  order_count_total: number;
  filled_count: number;
  partially_filled_count: number;
  open_count: number;
  rejected_count: number;
  canceled_count: number;
  expired_count: number;
  other_count: number;
  fill_rate_any: number;
  fill_rate_full: number;
  open_rate: number;
  by_account: Record<string, ExecutionLiveOrderAccountSummary>;
  raw_status_counts: Record<string, number>;
  account_errors: Array<{ label: string; error: string }>;
}

export interface ExecutionSummary {
  schema_version: string;
  timestamp_utc: string;
  routing: ExecutionRoutingSummary;
  live_orders: ExecutionLiveOrdersSummary;
}

export const api = {
  heartbeat: () => fetchJSON<Heartbeat>("/api/heartbeat"),
  controls: () => fetchJSON<Controls>("/api/controls"),
  latestScorecard: () => fetchJSON<Scorecard>("/api/scorecard/latest"),
  timeline: (limit = 200) => fetchJSON<TimelinePoint[]>(`/api/scorecards/timeline?limit=${limit}`),
  bridges: () => fetchJSON<BridgeStatusResponse>("/api/bridges"),
  orders: (limit = 50) => fetchJSON<any[]>(`/api/execution/orders?limit=${limit}`),
  bindings: (limit = 50) => fetchJSON<any[]>(`/api/execution/bindings?limit=${limit}`),
  executionSummary: (routerLimit = 100, brokerLimit = 100, lookbackHours = 24) =>
    fetchJSON<ExecutionSummary>(
      `/api/execution/summary?router_limit=${routerLimit}&broker_limit=${brokerLimit}&lookback_hours=${lookbackHours}`,
    ),
  alerts: (limit = 30) => fetchJSON<any[]>(`/api/alerts?limit=${limit}`),
  graduation: () => fetchJSON<GraduationReport>("/api/graduation"),
  thresholds: () => fetchJSON<any>("/api/thresholds"),
  timeWindow: () => fetchJSON<any>("/api/time_window"),
  portfolio: () => fetchJSON<PortfolioData>("/api/portfolio"),
  portfolioHistory: (period = "1M", timeframe = "1D", account = "all") =>
    fetchJSON<PortfolioHistoryData>(
      `/api/portfolio-history?period=${period}&timeframe=${timeframe}&account=${account}`,
    ),
  tradeAnalysis: () => fetchJSON<TradeAnalysis>("/api/trade-analysis"),
  performance: () => fetchJSON<PerformanceData>("/api/performance"),
  consciousness: () => fetchJSON<ConsciousnessData>("/api/consciousness"),
  politicianAlpha: () => fetchJSON<PoliticianAlphaData>("/api/politician-alpha"),
  executionMode: () => fetchJSON<ExecutionModeData>("/api/execution-mode"),
  setExecutionMode: (strategy: string, mode: string) =>
    fetch(`${API_BASE}/api/execution-mode`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(API_KEY ? { "X-API-Key": API_KEY } : {}) },
      body: JSON.stringify({ strategy, mode }),
    }).then(r => r.json()),
  approveOrders: (strategy: string, action: string) =>
    fetch(`${API_BASE}/api/telegram/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(API_KEY ? { "X-API-Key": API_KEY } : {}) },
      body: JSON.stringify({ strategy, action }),
    }).then(r => r.json()),
  pendingOrders: () => fetchJSON<any>("/api/pending-orders"),
  gssTimeline: (limit = 100) => fetchJSON<GSSTimelinePoint[]>(`/api/gss-timeline?limit=${limit}`),
  gssLatest: () => fetchJSON<GSSLatest>("/api/gss-latest"),
  dashboardLayout: () => fetchJSON<DashboardLayout>("/api/dashboard/layout"),
  quantum: () => fetchJSON<any>("/api/quantum"),
};

export interface PerformanceData {
  timestamp_utc?: string;
  source_timestamp_utc?: string;
  source_age_seconds?: number | null;
  source_freshness?: "live" | "degraded" | "stale" | "unknown";
  open_positions_snapshot_timestamp_utc?: string;
  open_positions_snapshot_age_seconds?: number | null;
  open_positions_snapshot_freshness?: "live" | "degraded" | "stale" | "unknown";
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
  holding_period?: string;
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
  source_timestamp_utc?: string;
  source_age_seconds?: number | null;
  source_freshness?: "live" | "degraded" | "stale" | "unknown";
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

export interface PoliticianAlphaWhaleTrade {
  politician: string;
  symbol: string;
  transaction_type: string;
  amount: string;
  transaction_date: string;
  committee: string;
  score: number;
  chamber: string;
}

export interface PoliticianAlphaCommitteeSignal {
  committee: string;
  symbol: string;
  trade_count: number;
  influence_weight: number;
}

export interface PoliticianAlphaData {
  timestamp_utc: string;
  fresh: boolean;
  source: string;
  reason?: string;
  political_alpha_scores: Record<string, number>;
  top_whale_trades: PoliticianAlphaWhaleTrade[];
  committee_signals: PoliticianAlphaCommitteeSignal[];
  aggregate_sentiment: string;
  total_trades_analyzed: number;
  tracked_symbols_with_activity: number;
  error?: string;
}

export interface GSSTimelinePoint {
  timestamp_utc: string;
  z_score: number;
  narrative_velocity: number;
  vix: number;
  regime_p: number;
  confidence: number;
  gss_signal: string;
  mode: string;
}

export interface GSSLatest {
  timestamp_utc?: string;
  gss_signal: string;
  action?: string;
  reason?: string;
  confidence?: number;
  field_data?: { z_score: number; coherence_level: string; regional_spikes: any[] };
  narrative_data?: { velocity: number; dominant_narrative: string };
  execution_data?: { vix: number; gamma_exposure: number; put_call_ratio: number };
  hedge_recommendations?: any[];
  margin_status?: any;
  error?: string;
}

export interface DashboardWidget {
  id: string;
  cols: number;
  title: string;
  visible: boolean;
  badge?: string;
}

export interface DashboardRow {
  id: string;
  widgets: DashboardWidget[];
}

export interface DashboardLayout {
  version: number;
  updated_at: string;
  updated_by: string;
  rows: DashboardRow[];
  error?: string;
}

export interface WSConnection {
  close: () => void;
}

export function connectWS(onMessage: (data: any) => void): WSConnection | null {
  const origin = typeof window !== "undefined" ? window.location.origin : "http://localhost:8501";
  const wsBase = (process.env.NEXT_PUBLIC_API_URL || origin).replace("http", "ws") + "/ws";
  const wsUrl = API_KEY ? `${wsBase}?api_key=${API_KEY}` : wsBase;
  try {
    let closed = false;
    let activeSocket: WebSocket | null = null;

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(wsUrl);
      activeSocket = ws;
      ws.onmessage = (e) => {
        try { onMessage(JSON.parse(e.data)); } catch {}
      };
      ws.onclose = () => {
        if (!closed) {
          setTimeout(connect, 5000);
        }
      };
    };

    connect();

    return {
      close: () => {
        closed = true;
        activeSocket?.close();
      },
    };
  } catch {
    return null;
  }
}
