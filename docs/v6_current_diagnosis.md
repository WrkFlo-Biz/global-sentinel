# V6 Current Diagnosis — 2026-03-08 22:00 UTC

## System State
- Mode: NORMAL, regime_p=0.350, confidence=0.720
- Strategies: 11/15 firing (4 waiting for market-hours data)
- Exposure: 68% effective (101% raw, 78 pending close orders)
- Scanner: 19-24 discoveries per cycle, top 5 in Telegram digest
- Search: Exa exhausted, SerpAPI fallback active, all 28 categories covered
- Dashboard: live at port 8501, warroom at /warroom

## Accounts
- Day Trade (PA3F6696XKWK): $100K equity, 71 positions, 176 orders (66 pending close)
- Medium Long (PA36T8OFBNXB): $500K equity, 21 positions, 62 orders (12 pending close)

## V6 Modules Status

| Module | Status | Output |
|--------|--------|--------|
| ExposureBook | Active | Dual-account snapshot every cycle |
| EdgeDetector | Active | Cascade/divergence/lag detection |
| CrossAssetSignals | Active | Bond/currency/commodity signals |
| StrategyEngine | Active | 11/15 strategies, 15-25 ideas/cycle |
| WarOpportunityScanner | Active | 19-24 discoveries/cycle |
| DeescalationDetector | Active | Monitoring ceasefire keywords |
| ScenarioSimulator | Active | 6 stress scenarios (every 20 cycles) |
| AlertManager | Active | 14 rules, 4 severity levels |
| PIT DataStore | Active | Gzip snapshots every cycle |
| OilShockRegime | Building | Thresholds at $95/$100/$105 |
| BrokerOrderAudit | Building | Classify 78 pending closes |

## Guardrails (current)
- Max gross exposure: 200%
- Max sector: 50%
- Max single position: 8%
- Daily loss halt: $3,000
- Correlation group: 40%
- Kill switch: active (control/kill_switch.json)

## Architecture Summary

The V6 system runs as three systemd services on the Azure VM (`openclaw@20.124.180.8`):
1. `global-sentinel` — main crisis monitor loop
2. `global-sentinel-dashboard` — Streamlit dashboard on port 8501
3. `global-sentinel-telegram` — Telegram digest bot with hourly batching

Each cycle (~60s) the crisis monitor:
1. Fetches live data (news, prices, geopolitical feeds)
2. Runs EdgeDetector for cascade/divergence/lag signals
3. Runs CrossAssetSignals for bond/currency/commodity correlations
4. Runs StrategyEngine to produce 15-25 trade ideas
5. Runs ExposureBook to compute dual-account exposure
6. Applies guardrails (position limits, sector caps, correlation checks)
7. Submits qualifying orders via Alpaca broker adapter
8. Saves PIT snapshot to gzip archive
9. Pushes alerts/digest to Telegram

## Data Pipeline

| Source | Type | Refresh | Status |
|--------|------|---------|--------|
| Alpaca Market Data | Prices, bars, quotes | Real-time | Active |
| FMP API | Congress trades, financials | Per cycle | Active |
| SerpAPI | News search (28 categories) | Per cycle | Active (fallback) |
| Exa AI | Deep search, research | N/A | Exhausted |
| Alpaca Broker | Orders, positions, account | Per cycle | Active |
| Origin Quantum | Optimization backend | Per cycle | Fallback to classical |

## Exposure Breakdown

### Day Trade Account
- Gross exposure: ~$71K across 71 positions
- Average position size: ~$1K (1% of equity)
- 66 pending close orders queued for Monday market open
- Strategy mix: momentum (40%), mean-reversion (25%), event-driven (20%), defense (15%)

### Medium Long Account
- Gross exposure: ~$105K across 21 positions
- Average position size: ~$5K (1% of equity)
- 12 pending close orders queued
- Strategy mix: sector rotation (35%), macro (30%), geopolitical (20%), hedges (15%)

### Combined
- Raw gross: 101% (sum of both accounts relative to combined equity)
- Effective gross: 68% (after netting pending closes)
- Net exposure: +42% (long-biased)

## Strategy Performance (last 48h)

| Strategy | Ideas | Fills | Win Rate | Avg P&L |
|----------|-------|-------|----------|---------|
| momentum_breakout | 12 | 8 | 62% | +$45 |
| mean_reversion | 8 | 5 | 60% | +$32 |
| defense_sector | 6 | 4 | 75% | +$67 |
| oil_energy | 4 | 3 | 33% | -$18 |
| geopolitical_event | 5 | 3 | 67% | +$52 |
| Others (6 strategies) | 15 | 10 | 50% | +$12 |

## Known Issues

### 1. Exa AI Credits Exhausted
- **Impact**: Deep research queries fall back to SerpAPI
- **Severity**: Medium — SerpAPI covers all 28 categories but with less depth
- **Mitigation**: SerpAPI fallback is active and functional
- **Resolution**: Purchase additional Exa credits or wait for monthly reset

### 2. Origin Quantum Backend Misconfigured
- **Impact**: Quantum optimization routes fall back to classical scipy optimizer
- **Severity**: Low — classical optimizer produces valid results, just slower
- **Mitigation**: Automatic fallback to classical is working
- **Resolution**: Fix `ORIGINQ_API_KEY` configuration or reconfigure Azure quantum resources

### 3. Chokepoint Composite Low (0.07)
- **Impact**: Hormuz/Suez chokepoint risk score may not reflect actual geopolitical status
- **Severity**: Medium — could miss an oil shock trigger
- **Mitigation**: Oil price thresholds ($95/$100/$105) provide independent trigger
- **Resolution**: Verify chokepoint data sources are returning current data; cross-reference with news feeds

### 4. Pending Close Order Backlog (78 orders)
- **Impact**: 78 close orders will execute at Monday open, potentially causing slippage
- **Severity**: Medium — concentrated execution at open can move prices
- **Mitigation**: BrokerOrderAudit (building) will classify and stagger these
- **Resolution**: Run broker audit Monday pre-market to identify stale/duplicate orders

### 5. Rate Limiter Near Capacity
- **Impact**: 180 req/min token bucket shared across all API calls
- **Severity**: Low — current usage ~140 req/min
- **Mitigation**: Rate limiter gracefully queues excess requests
- **Resolution**: Monitor; increase bucket size if cycle times extend

## Recommendations for Monday
1. Run broker order audit before market open to clean stale/duplicate orders
2. Monitor oil futures pre-market — if WTI > $95, consider switching to ELEVATED mode early
3. Watch first 5 minutes of market open for order rejection spikes
4. Verify all 66 day-trade pending closes execute cleanly
5. Review chokepoint data source for accuracy
