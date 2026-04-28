# Global Sentinel — Complete Strategy Overview
**Last updated:** 2026-04-01

## System Architecture
- **Service:** `global-sentinel.service` — Python crisis monitor running 15-min cycles (NORMAL), 5-min (ELEVATED), 1-min (CRISIS)
- **Execution:** Both `day_trade` and `medium_long` in **auto mode**
- **Bots:** @mo2darkbot (day_trade), @mo2drkbot (medium_long)
- **Paper equity:** $95,082 (day_trade), $463,494 (medium_long)
- **Data layer:** 98+ quantum_feed sources, 120+ JSON signal files

## Strategy Inventory (40 total)

### Orchestrator Strategies (17 — run every cycle via strategy_orchestrator.py)

| # | Strategy | Engine | Type | Status |
|---|----------|--------|------|--------|
| 1 | Stocks | orchestrator (inline) | Alpha scoring | Active — energy dominant (OXY, XOM, XLE) |
| 2 | Options (0DTE) | orchestrator (inline) | Session quality | **Only Kelly-approved strategy** (47.8% WR, 1.54 W/L) |
| 3 | Crypto | orchestrator (inline) | TVL/stablecoin | Risk-off (TVL contracting) |
| 4 | ETFs | orchestrator (inline) | Sector rotation | Materials/Utilities leading |
| 5 | Futures/Commodities | orchestrator (inline) | War intensity | HIGH conviction long oil |
| 6 | Bonds | orchestrator (inline) | Yield curve | Neutral |
| 7 | Currencies | orchestrator (inline) | USD strength | Neutral |
| 8 | Prediction Markets | orchestrator (inline) | Polymarket divergence | Market complacent — buy vol hedge |
| 9 | World Indices | orchestrator (inline) | ACLED conflict | Neutral |
| 10 | Sectors | orchestrator (inline) | Momentum rotation | Active |
| 11 | Kelly Sizer | kelly_sizer.py | Position sizing | 1 tradeable, 10 insufficient data |
| 12 | Scalping Engine | scalping_engine.py | Intraday scalps | 54 signals — **Backtest: Sharpe -1.45 DEGRADED** |
| 13 | ICT Smart Money | ict_smc_engine.py | Order blocks/FVG | 5 signals — **Backtest: Sharpe +0.88 OK** |
| 14 | Chart Markup | chart_markup_engine.py | Structural levels (no fib) | 38 confluence zones, 20 ideas — **NEW** |
| 15 | Power Market | power_market_engine.py | Basis/directional/DART | Basis signal active — **NEW** |
| 16 | Ranked Asset Allocation | ranked_asset_allocation.py | Monthly rebalance | DBC/VAW/RWR allocated, 40% cash — **NEW** |
| 17 | Systematic Options Selling | systematic_options_selling.py | Straddles/strangles/theta | Contango favorable — **NEW** |

### Standalone Engines (7 — daemon/scheduled)

| # | Strategy | Engine | Backtest |
|---|----------|--------|----------|
| 18 | ORB Multi-TF | orb_multi_tf_strategy.py | **Sharpe -0.79 DEGRADED** |
| 19 | Zero-DTE Picker | zero_dte_picker.py | Pre-market daily |
| 20 | Correlation Meta-Classifier | correlation_meta_classifier.py | Ensemble weighting |
| 21 | Synthetic Trade Simulator | synthetic_trade_simulator.py | Shadow trading |
| 22 | Multi-Broker Paper | multi_broker_paper.py | Simulation |
| 23 | A/B Tester | ab_tester.py | Strategy comparison |
| 24 | Overnight Gap | overnight_gap_strategy.py | **Sharpe +16.49 EXCELLENT** |

### War Strategies (30 in config/war_strategies.yaml)

**Day Trade Account ($100K, target $300/day):**
1. oil_momentum_intraday ($300/day)
2. airline_short ($150/day)
3. vix_spike_scalp
4. cyber_retaliation
5. oil_gap_persistence ($300/day)
6. oil_mean_reversion
7. europe_pre_open ($150/day)
8. us_premarket_gap ($200/day)
9. **chart_markup_structural** ($200/day) — @adriannajones.official
10. **systematic_options_selling** ($200/day) — @poojawadhwa.official

**Medium/Long Account ($500K, target $200/day):**
11-26. shipping_rate_explosion, defense_accumulation, gold_safe_haven, europe_energy_crisis, fertilizer_food_chain, nuclear_renaissance, em_capital_flight, inflation_hedge, canadian_oil_premium, wall_street_vol, refining_crack_spread, jet_fuel_squeeze, supply_shock_pairs, petro_inflation, china_oil_import_shock, commodity_currency_divergence
27. **power_market_citadel** ($150/day) — @neelsalami (Neel Somani, ex-Citadel)
28. **ranked_asset_allocation** ($100/day) — @macro_quant_rick (2018 Dow Award paper)

### Crypto Strategies (10 in config/crypto_strategies.yaml, 24/7)
C1-C10: btc_digital_gold, eth_defi_demand, sol_momentum, paxg_gold_proxy, link_oracle_demand, altcoin_momentum_basket, meme_war_momentum, xrp_cross_border, narrative_momentum, trump_event_trade

## Performance Data

### Kelly Criterion Analysis (26 trades analyzed)
| Strategy | Trades | Win Rate | W/L Ratio | Kelly% | Recommendation |
|----------|--------|----------|-----------|--------|----------------|
| options_0dte | 23 | 47.8% | 1.54x | 13.9% | **TRADE** ($3,299/position) |
| day_trade_momentum | 2 | 0% | 0 | 0% | insufficient_data |
| All others | 0 | — | — | — | insufficient_data |

### Walk-Forward Backtest Scores (2026-03-28)
| Strategy | Avg OOS Sharpe | Status |
|----------|---------------|--------|
| **Overnight Gap** | +16.49 | Excellent |
| **ICT SMC** | +0.88 | Good |
| Momentum | +0.07 | Degraded |
| ORB | -0.79 | Degraded |
| Scalping | -1.45 | Degraded |

### Real Trade P&L (from feedback dataset, 167 entries)
- Winners: XLE calls (+$0.38), NVDA puts (+$0.96, +$1.53), SPY puts (+$0.43), XLE calls (+$0.21)
- Losers: OXY calls (-$0.01, -$0.38, -$0.01), SOFI calls (-$0.17), QQQ calls (-$0.31), SOXL calls (-$0.29), GUSH (-$7.08)
- Paper medium_long weekly: -$18,952 (-2.08%)

### Ensemble Signal Weights
- Sentiment: 70.2% (dominant)
- Volatility: 11.9%
- Momentum: 8.8%
- Mean Reversion: 4.6%
- Event-Driven: 4.6%

## Cross-Pollination Insights (Current)
1. Crypto risk-off + oil surging = **stagflation regime** — favor energy, avoid growth
2. ICT SMC + Scalping both firing — high conviction intraday setups
3. Chart markup + ICT aligned — structural trade confluence
4. 38 confluence zones — strong structural session
5. Power market + oil both bullish — energy sector high conviction
6. RAAM 40% cash — weak momentum in bonds, commodities strong
7. Options selling: contango favorable, 17 DTE to monthly

## Known Issues
1. **Evaluator gap:** Crisis monitor logs "No evaluator" for chart_markup_structural, power_market_citadel, ranked_asset_allocation — need evaluators wired in
2. **Kelly starvation:** Only 1/11 strategies has 20+ trades for proper sizing
3. **Degraded strategies:** ORB (Sharpe -0.79) and Scalping (-1.45) need review
4. **Overnight Gap underutilized:** Best backtest Sharpe (+16.49) but no dedicated evaluator
5. **Ensemble sentiment-heavy:** 70% sentiment weight may mask technical signals
6. **Regime allocator error:** NoneType comparison in capital allocator (non-fatal)

## Instagram-Sourced Strategies (Added 2026-04-01)
| Source | Strategy | Engine | Key Concept |
|--------|----------|--------|-------------|
| @adriannajones.official | Chart Markup (no fib) | chart_markup_engine.py | Structural S/R levels, confluence zones |
| @neelsalami (Neel Somani) | Power Market (3 ways) | power_market_engine.py | Basis spreads, directional fuel, DART vol |
| @macro_quant_rick | Ranked Asset Allocation | ranked_asset_allocation.py | Swiss-style ranking, 2018 Dow Award paper |
| @poojawadhwa.official | Systematic Options Selling | systematic_options_selling.py | Straddles/strangles, theta decay, IV/RV |
| @radhethetrader | Fair Value Gap | (already in ict_smc_engine.py) | FVG detection — core ICT concept |
| @quantguild | 5 Foundational Quant Papers | (already embedded across GS) | MPT, Black-Scholes, CAPM, EMH |

## File Locations
```
/opt/global-sentinel/
  config/war_strategies.yaml          # 30 war strategies
  config/crypto_strategies.yaml       # 10 crypto strategies
  execution_mode.yaml                 # Auto/manual, position sizing
  control/kill_switch.json            # Emergency halt
  control/manual_veto.json            # Stop new trades
  src/strategies/                     # 19 Python strategy engines
  src/strategies/strategy_orchestrator.py  # Master runner (17 strategies)
  data/quantum_feed/                  # 120+ signal files (98 loaded)
  data/quantum_feed/strategy_master.json  # Consolidated output
  data/quantum_feed/kelly_sizing.json     # Position sizing
  reports/paper_trades/               # Paper trade history
  reports/daily_performance/          # Daily P&L
  reports/tearsheets/                 # HTML tearsheets
  reports/backtest/                   # Walk-forward results
```
