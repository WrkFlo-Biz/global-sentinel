# Global Sentinel V4 — Comprehensive System Overview

## For: ChatGPT review — recommendations and next steps

---

## 1. What Global Sentinel Is

Global Sentinel is a **24/7 shadow-mode geopolitical risk intelligence and supervised execution orchestration system**. Its core doctrine is **"geopolitical arbitrage, not HFT"** — it optimizes for 2nd/3rd-order effects of macro events (energy shocks, rate policy, geopolitical escalation, supply chain disruption), not millisecond-level execution.

The system runs on:
- **VM**: Ubuntu 22.04 at `20.124.180.8` with 3 systemd services
- **Azure Container Apps**: For isolated quantum research jobs
- **GitHub Actions**: CI/CD and research pipeline automation
- **Alpaca Paper Trading**: Two accounts (day trade $100K, medium-long $500K)

---

## 2. Architecture Layers

### 2.1 Data Ingestion Layer (13 bridges)

Every bridge implements a canonical interface: `source`, `source_tier`, `trust_weight`, and a `fetch() -> List[Dict]` method that returns structured packets.

| Bridge | Source | Tier | Trust Weight | Packet Type |
|--------|--------|------|-------------|-------------|
| `fed_bridge.py` | Federal Reserve RSS | tier_1_official | 1.0 | MacroPolicyEvent |
| `fred_bridge.py` | FRED API (macro series) | tier_1_official | 1.0 | MacroPolicyEvent |
| `bls_bridge.py` | Bureau of Labor Statistics | tier_1_official | 1.0 | MacroPolicyEvent |
| `eia_bridge.py` | EIA petroleum inventory | tier_1_official | 1.0 | PhysicalFlowEvent |
| `sec_edgar_bridge.py` | SEC EDGAR filings | tier_1_official | 1.0 | MacroPolicyEvent |
| `cftc_bridge.py` | CFTC Socrata API | tier_1_official | 1.0 | PhysicalFlowEvent |
| `whitehouse_policy_bridge.py` | WH press + Federal Register | tier_1_official | 1.0 | MacroPolicyEvent |
| `noaa_bridge.py` | NOAA severe weather alerts | tier_1_official | 1.0 | PhysicalFlowEvent |
| `gdelt_bridge.py` | GDELT geopolitical events | tier_2_operational | 0.8 | GeopoliticalEvent |
| `maritime_bridge.py` | Maritime AIS (stub) | tier_2_operational | 0.8 | PhysicalFlowEvent |
| `sentiment_bridge.py` | Finnhub news sentiment | tier_2_operational | 0.8 | MacroPolicyEvent |
| `policy_uncertainty_bridge.py` | Policy uncertainty index | tier_3_research | 0.5 | MacroPolicyEvent |
| `sec_filing_event_scorer.py` | SEC filing significance | tier_1_official | 1.0 | Scoring helper |

### 2.2 Packet Schema Layer

All data flows through a **BasePacket** pattern with typed specializations:

```
BasePacket
  ├── packet_type, packet_id (SHA-256), source, source_tier
  ├── timestamp_utc, confidence, trust_weight, provenance
  │
  ├── MacroPolicyEvent (hawkish_dovish_score, growth_inflation_score, related_assets)
  ├── GeopoliticalEvent (region, severity, energy_relevance, supply_chain_relevance)
  └── PhysicalFlowEvent (region, flow_type, disruption_score, measured_value)
```

Packet factories (`make_macro_policy_event()`, `make_geopolitical_event()`, `make_physical_flow_event()`) generate SHA-256-based packet IDs for deduplication and provenance tracking.

### 2.3 Data Trust Hierarchy (4 tiers)

| Tier | Weight | Rule |
|------|--------|------|
| tier_1_official | 1.0 | Fed, FRED, BLS, EIA, SEC, CFTC, WH, NOAA |
| tier_2_operational | 0.8 | GDELT, maritime, sentiment, market data |
| tier_3_research | 0.5 | Congressional disclosures, policy uncertainty |
| tier_4_experimental | 0.2 | Never drives execution; max 5% influence |

**Hard rule**: Execution requires tier_1 or tier_2 confirmation. Tier 4 is blocked from execution entirely.

### 2.4 Execution Layer (18 modules)

| Module | Purpose |
|--------|---------|
| `shadow_order_router.py` | Central order routing with shadow/paper modes |
| `trade_idea_packager.py` | Converts signals into executable trade ideas |
| `strategy_manager.py` | Multi-strategy orchestration (day trade, medium-long) |
| `alpaca_paper_adapter.py` | Alpaca API adapter with rate limiting |
| `position_manager.py` | Position tracking and limits |
| `order_intent_registry.py` | Intent-based order lifecycle tracking |
| `stale_intent_sweeper.py` | TTL-based cleanup of stale orders |
| `broker_state_reconciler_loop.py` | Continuous reconciliation with broker state |
| `paper_trade_reconciler.py` | Paper trade P&L reconciliation |
| `fill_simulator.py` | Simulated fill engine for shadow mode |
| `options_guardrails.py` | Options-specific risk checks |
| `time_window_ttl_policy.py` | Time-of-day execution policies |
| `performance_tracker.py` | Strategy performance measurement |
| `tca_shadow_report.py` | Transaction cost analysis |
| `execution_reliability_metrics.py` | Fill quality and reliability tracking |
| `adaptive_feedback_loop.py` | Self-adjusting execution parameters |
| `politician_alpha_executor.py` | Congressional trading signal executor |
| `tradier_sandbox_adapter.py` | Tradier options sandbox adapter |

**Sizing**: Notional-based (8-12% of equity per day trade position). Not share-count-based.

### 2.5 Operating Modes

| Mode | Polling | Shadow Drafts | Config Changes |
|------|---------|---------------|----------------|
| NORMAL | 15 min | Eligible | Allowed (staging) |
| ELEVATED | 5 min | Eligible | Restricted |
| CRISIS | 1 min | Suspended | Frozen |
| MANUAL_REVIEW | Paused | Suspended | Frozen |

### 2.6 Reports Layer

| Module | Purpose |
|--------|---------|
| `research_quantum_summary.py` | JSON aggregate of quantum vs classical wins |
| `research_quantum_markdown_summary.py` | Human-readable markdown for dashboards |
| `research_executive_brief.py` | CIO/CAIO-level executive summary |
| `research_training_status_report.py` | Training dataset + label + replay backtest status |
| `research_drift_report.py` | Weight drift detection between learning iterations |
| `manual_review_queue_report.py` | Pending manual review items |
| `weekly_process_scorecard.py` | Weekly operational scorecard |

---

## 3. Quantum Research Lane — QPanda / Origin Pilot Integration

### 3.1 Overview

The quantum lane is a **completely isolated, artifact-only research pipeline** that runs QPanda/Origin quantum optimization alongside a classical baseline to evaluate whether quantum approaches produce better trade recommendations. It **never** has access to broker credentials, execution paths, or config mutation.

### 3.2 Isolation Guardrails (config/quantum_lane_policy.yaml)

```yaml
lane_rules:
  artifact_only: true
  disable_execution_path: true
  shadow_mode_only: true
  allow_threshold_mutation: false
  allow_router_access: false
  allow_broker_credentials: false
```

### 3.3 Origin / QPanda Configuration

- **Provider**: Origin QCloud (`https://qcloud.originqc.com.cn`)
- **Backend**: 72-bit chip
- **Shots**: 1000 per optimization
- **Max candidates**: 5 per optimization run
- **Fallback**: Classical (if quantum fails or is unavailable)
- **Algorithms**: QAOA, VQE via pyqpanda

### 3.4 Maturity Stages

| Stage | Status | Description |
|-------|--------|-------------|
| Stage 1: Simulation & Research Only | **ACTIVE** | QPanda outputs are artifact-only, never influence execution |
| Stage 2: Comparative Ranking | Inactive | QPanda scores shown alongside classical, no execution influence |
| Stage 3: Bounded Contribution | Inactive | QPanda contributes max 15% weight to candidate ranking after sustained outperformance |

### 3.5 Finance Capabilities (enabled in research mode)

| Capability | Status | Description |
|------------|--------|-------------|
| Portfolio Optimization | Enabled | Multi-name allocation under sector/risk/execution constraints |
| Derivative Pricing Research | Enabled | Option/hedge scenario payoff analysis |
| Risk Management Research | Enabled | Drawdown/tail risk reduction during stress |
| Anomaly Detection Research | Enabled | Signal novelty with plausibility checking |
| Monte Carlo Acceleration | Enabled | Scenario path generation for stress testing |

### 3.6 Quantum Research Modules (32 modules)

#### Core Pipeline
| Module | Purpose |
|--------|---------|
| `quantum_optimizer_bridge.py` | 530-line QPanda/Origin integration (QAOA/VQE, cloud + local sim + fallback) |
| `classical_optimizer_baseline.py` | Deterministic greedy selector with sector concentration cap |
| `run_quantum_research_job.py` | Job orchestrator: request → classical → quantum → comparison |
| `run_research_score_pipeline.py` | Full pipeline: + trade outcome eval + research score + snapshot attach |
| `benchmark_quantum_vs_classical.py` | v2 with finance mode breakdown |

#### Request Building
| Module | Purpose |
|--------|---------|
| `build_quantum_request.py` | CLI request builder from snapshot data |
| `quantum_optimization_request_builder.py` | Extracts regime/window/microstructure from snapshots |
| `build_regime_conditioned_request.py` | **New**: Uses regime optimizer + constraints + derivatives before request |
| `regime_conditioned_optimizer.py` | Selects objective + filters candidates by regime state |

#### QFinance Objective Pipeline
| Module | Purpose |
|--------|---------|
| `qfinance_objective_library.py` | 5 reusable objectives (hedge, portfolio, derivative, risk, anomaly) |
| `qfinance_feature_encoder.py` | Normalized feature encoding for optimization input |
| `portfolio_constraint_builder.py` | Constraint construction adapting to regime/window/incident |
| `candidate_universe_ranker.py` | Pre-optimization ranking with regime alignment + liquidity + impact |
| `derivative_candidate_builder.py` | Generates option/hedge candidates from equity universe |

#### Scenario & Analysis
| Module | Purpose |
|--------|---------|
| `monte_carlo_scenario_engine.py` | Shock path generation with percentile summaries |
| `option_scenario_pricer.py` | Call/put payoff grids for hedge research |
| `historical_analog_engine.py` | 8 tagged scenarios (Gulf War, Red Sea, Hormuz, Volcker, etc.) |
| `feature_store_builder.py` | Ingests all bridges, deduplicates, builds aggregate snapshots |

#### Evaluation & Scoring
| Module | Purpose |
|--------|---------|
| `evaluate_trade_outcomes.py` | Overlap + directional + realized P&L comparison |
| `research_score_writer.py` | Bounded [0,1] score: 35% overlap + 35% directional + 30% realized delta |
| `attach_research_score_to_snapshot.py` | Adds research overlay with execution guardrails |
| `merge_trade_outcomes_from_router.py` | Normalizes router output → TradeOutcomeTelemetry |
| `trade_outcome_telemetry_schema.py` | Canonical schema (symbol, direction, realized_return_bps, slippage, fill_rate) |

#### Training & Replay Loop
| Module | Purpose |
|--------|---------|
| `qfinance_training_dataset_builder.py` | Row-wise training datasets joining features + trade outcomes |
| `alpha_candidate_labeler.py` | Labels rows (strong_positive/positive/neutral/negative/strong_negative + execution quality) |
| `replay_quantum_research_backtest.py` | Replay evaluator over historical evaluation artifacts |

#### Online Learning & Self-Improvement
| Module | Purpose |
|--------|---------|
| `qfinance_online_learning_state.py` | Persisted learning state with weights, update stats, and guardrails |
| `update_research_model_weights.py` | Bounded weight updates from labeled training rows (max_abs_weight_step=0.05) |
| `telemetry_feature_joiner.py` | Joins encoded features with trade telemetry + research scores |

#### Dataset & Publishing
| Module | Purpose |
|--------|---------|
| `regime_dataset_builder.py` | Normalized snapshots for training/replay |
| `publish_research_artifacts.py` | Publish to Azure Blob with SHA-256 manifest |
| `quantum_optimization_result_handler.py` | Result processing and artifact writing |

### 3.7 Research Score Classification

| Score Range | Classification | Meaning |
|-------------|---------------|---------|
| >= 0.70 | `research_positive` | Quantum outperforming classical |
| >= 0.55 | `research_neutral_positive` | Marginal quantum advantage |
| 0.36-0.54 | `none` | No clear advantage |
| <= 0.35 | `research_negative` | Classical outperforming quantum |

**All scores carry guardrails**: `not_for_direct_execution: true`, `bounded_secondary_signal_only: true`

### 3.8 End-to-End Research Flow

```
1. Ingest packets (13 bridges → BasePacket subtypes)
2. Build feature store / candidate universe
3. Compute regime state (geo, macro, shift probability)
4. Regime-conditioned optimizer selects objective + filters candidates
5. Portfolio constraint builder generates constraints
6. Optionally add derivative candidates
7. Build QuantumOptimizationRequest
8. Run classical baseline (deterministic greedy)
9. Run QPanda/Origin bridge (QAOA/VQE on 72-bit backend)
10. Compare outputs (benchmark_quantum_vs_classical)
11. Evaluate against real/shadow trade outcomes
12. Compute bounded research score
13. Optionally attach score to research snapshot
14. Build training dataset (join features + trade outcomes)
15. Label candidates (alpha label + execution quality)
16. Generate summaries (JSON, markdown, executive brief, training status)
17. Update online-learning weights (bounded, max 0.05 step per feature)
18. Generate drift report (weight deltas between iterations)
19. Publish artifacts to Azure Blob
20. Replay backtest over historical evaluations
```

---

## 4. Azure Infrastructure

### 4.1 VM (Production Runtime)
- `openclaw@20.124.180.8`
- 3 systemd services for monitoring/execution loops
- Python 3.10, azure-identity + azure-storage-blob installed
- `PYTHONPATH=/opt/global-sentinel`

### 4.2 Azure Container Apps (Quantum Jobs)
- Resource group: `gs-dev-rg`
- Container registry: `wrkfloopenclawacr.azurecr.io`
- Key Vault: `gs-quantum-kv` (storage account, container names, Origin API key)
- Job: `quantum-research-job` (Manual trigger, 1 CPU / 2Gi, SystemAssigned managed identity)
- Isolation: Separate managed identity with no broker credentials

### 4.3 GitHub Actions
- Workflow: `research-quantum-pipeline.yml` — tests → pipeline → summaries → upload artifacts
- Workflow: `research-online-learning.yml` — init state → update weights → drift report → upload state
- OIDC-based Azure login for blob access

---

## 5. Configuration Files

| Config | Purpose |
|--------|---------|
| `data_trust_hierarchy.yaml` | 4-tier trust weights + execution rules |
| `quantum_lane_policy.yaml` | Quantum isolation, capabilities, maturity stages, Origin config |
| `thresholds.yaml` | Regime shift probability thresholds |
| `assets_watchlist.yaml` | Monitored asset universe |
| `execution_mode.yaml` | Current operating mode |
| `incident_mode_policy.yaml` | Incident response rules |
| `intraday_timing_guardrails.yaml` | Time-of-day execution windows |
| `options_rollout.yaml` | Options trading rollout policy |
| `order_ttl_policy.yaml` | Order time-to-live rules |
| `venue_policies.yaml` | Venue-specific execution policies |
| `sanctions_policies.yaml` | Sanctions compliance rules |
| `paper_trading_graduation.yaml` | Shadow → live graduation criteria |

---

## 6. Test Coverage

**24 research tests** + **32 execution/dashboard tests** = **56 total tests**

Research tests cover:
- Quantum request building
- Classical baseline ranking + sector cap
- Trade outcome evaluation (overlap, directional, P&L)
- Option scenario pricing (call, put, grid)
- Research score bounds and guardrail enforcement
- Snapshot attachment with data preservation
- Regime-conditioned objective selection (crisis → hedge, growth → portfolio)
- QFinance feature encoding (single candidate + universe ranking)
- Training dataset building (join + no-match handling)

---

## 7. Safety Architecture

### Non-Negotiable Rules
1. **NO LIVE ORDERS** without explicit human approval
2. Human "Y" required for any sandbox order draft
3. No single-source escalations — freshness quorum required
4. Risk gate + manual veto + kill switch every cycle
5. Config freeze in CRISIS mode
6. Quantum lane is artifact-only with zero execution path

### Quantum Lane Specific
- No broker credentials in quantum environment
- No router access
- No threshold mutation
- `quantum_direct_execution_forbidden: true` on all attached scores
- Stage 3 (bounded contribution) requires sustained measured outperformance and caps at 15%

---

## 8. Current State Summary

### What's Working
- 13 data ingestion bridges producing canonical packets
- Full quantum research pipeline (request → optimize → evaluate → score)
- Classical vs quantum benchmarking with finance mode tracking
- Bounded research score with safety guardrails
- Regime-conditioned objective selection and candidate filtering
- QFinance feature encoding and constraint building
- Monte Carlo scenario generation
- Option scenario pricing
- Azure Blob artifact IO (SDK installed on VM)
- GitHub Actions workflow for automated research runs
- 56 tests passing (24 research + 32 execution/dashboard)
- Executive, markdown, training status, and drift summaries
- Training dataset builder + alpha candidate labeler
- Replay backtest harness for historical evaluations
- Online learning state with bounded weight updates
- Drift reporting between learning iterations

### What's Not Yet Active
- Origin QCloud API calls (API key configured but calls fall back to classical)
- Stage 2/3 maturity (quantum results don't yet influence any execution)
- 24/7 automated research loop on Azure Container Apps
- Learning weights feeding back into feature encoder (promotion pipeline)
- Derivative-level option chain data integration
- Real-time regime state computation (currently uses snapshot-based)
- Azure Blob persistence of learning state across job runs

---

## 9. Module Count Summary

| Layer | Module Count |
|-------|-------------|
| Ingestion bridges | 13 |
| Packet schemas | 6 |
| Research modules | 32 |
| Execution modules | 18 |
| Utils | 5 |
| Reports | 9 |
| Config files | 16 |
| Test files | 20 |
| GitHub workflows | 2 |
| **Total** | **~121 modules** |

---

## 10. Key Technical Decisions

1. **stdlib-only research modules**: No numpy/pandas dependency in research lane; keeps Azure Container Apps image small
2. **Graceful SDK fallback**: `AzureBlobArtifactIO` works with or without azure-storage-blob installed
3. **Artifact-driven pipeline**: All data flows through JSON artifacts, enabling replay and audit
4. **Classical-first**: Every quantum run has a paired classical baseline for apples-to-apples comparison
5. **Regime conditioning**: Objective selection and candidate filtering adapt to macro/geo state before optimization
6. **SHA-256 packet IDs**: Enables deduplication across bridges and audit trail
7. **Trust-weighted scoring**: Data influence is proportional to source tier trust weight
8. **Bounded self-improvement**: Online learning updates are capped at 0.05 per feature per iteration, with drift reporting
9. **Label-driven training**: Alpha labels (strong_positive → strong_negative) + execution quality labels for supervised learning
