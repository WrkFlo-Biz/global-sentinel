# Global Sentinel V4 Module Inventory

Source of truth:
- `~/.codex-coordination/global-sentinel-terminal-contract.md`
- repo scan of `src/`, `config/`, and `tests/` on 2026-03-07

Status legend:
- `complete`: explicitly marked done in the contract or locally verified in the current tree
- `present`: file exists with tests or runtime wiring in the repo, but the contract does not contain a separate completion line for that exact file
- `skipped`: explicitly skipped by user decision
- `in_progress`: explicitly marked in progress in the contract

Test file note:
- `Test file` shows the primary direct suite when one exists.
- `Indirect / shared` means coverage is folded into a broader pack suite rather than a file dedicated to that one module.

## Pack 0 — Governance, Audit, and Safety

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/execution/politician_alpha_executor.py` | `PoliticianAlphaExecutor` | `0.1` | `skipped` | `User decision / legacy active` | `No dedicated V4 test file in current tree` | Political disclosure executor retained active because the quarantine pack was explicitly skipped. |
| `src/bridges/politician_alpha_bridge.py` | `PoliticianAlphaBridge` | `0.1` | `skipped` | `User decision / legacy active` | `No dedicated V4 test file in current tree` | Congressional disclosure bridge feeding the still-active political alpha path. |
| `src/core/policy_engine.py` | `PolicyEngine`, `PolicyDecision` | `0.2` | `complete` | `Claude CLI` | `tests/test_policy_engine.py` | Single evaluation point for trade ideas, research score attachment, weight promotion, and mode changes. |
| `src/core/structured_logger.py` | `StructuredLogger`, `StructuredFormatter` | `0.2` | `complete` | `Claude CLI` | `tests/test_structured_logger.py` | JSON logging with trace/span propagation and rotating file sinks. |
| `src/core/telemetry.py` | `start_span()`, `record_metric()` | `0.2` | `complete` | `Codex CLI` | `Indirect / shared` | OTLP-aware telemetry helpers with local JSONL fallbacks. |
| `src/reports/policy_audit_report.py` | `build_policy_audit_report()` | `0.2` | `present` | `Codex CLI` | `No dedicated test file in current tree` | Summarizes policy decisions, counts blocked vs allowed outcomes, and flags near misses. |
| `src/research/research_guardrail_checker.py` | `ResearchGuardrailChecker`, `GuardrailResult` | `0.3` | `complete` | `Claude CLI` | `tests/research/test_research_guardrail_checker.py` | Blocks malformed research scores, unsafe weight updates, and invalid training datasets. |
| `config/policy_engine_config.yaml` | `N/A (config)` | `0.2` | `complete` | `Claude CLI` | `tests/test_policy_engine.py` | Canonical policy thresholds: trust floors, mode rules, quantum caps, and weight limits. |
| `config/alerting_rules.yaml` | `N/A (config)` | `0.2` | `complete` | `Codex CLI` | `No dedicated test file in current tree` | Alert thresholds for bridge failures, queue depth, drift, and policy block counts. |

## Pack 1 — Feature Lineage and Promotion Governance

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/research/feature_store_builder.py` | `FeatureStoreBuilder` | `1.1` | `complete` | `Claude CLI` | `tests/research/test_feature_store.py` | Canonical feature-store ingest spine and bridge snapshot builder. |
| `src/research/feature_store/feature_group_registry.py` | `FeatureGroupRegistry` | `1.1` | `complete` | `Claude CLI` | `tests/research/test_feature_store.py` | Versioned registry for feature groups and schemas. |
| `src/research/feature_store/point_in_time_joiner.py` | `PointInTimeJoiner` | `1.1` | `complete` | `Claude CLI` | `tests/research/test_feature_store.py` | Point-in-time joins that prevent future leakage. |
| `src/research/feature_store/feature_lineage_tracker.py` | `FeatureLineageTracker` | `1.1` | `complete` | `Claude CLI` | `tests/research/test_feature_store.py` | Tracks raw packet to feature lineage and downstream artifact chains. |
| `src/research/feature_store/dataset_manifest.py` | `DatasetManifest` | `1.1` | `complete` | `Claude CLI` | `tests/research/test_feature_store.py` | Immutable manifest for datasets, feature versions, labels, and source packet counts. |
| `src/research/signal_graduation_report.py` | `SignalGraduationReport` | `1.2` | `complete` | `Claude CLI` | `tests/research/test_promotion_gates.py` | Evaluates signal readiness against promotion thresholds. |
| `src/research/encoder_promotion_gate.py` | `EncoderPromotionGate`, `PromotionDecision` | `1.3` | `complete` | `Claude CLI` | `tests/research/test_promotion_gates.py` | Hard gate for learned-weight promotion with policy, guardrail, dual-run, and canary checks. |
| `src/research/research_promotion_readiness_report.py` | `ResearchPromotionReadinessReport` | `1.3` | `complete` | `Claude CLI` | `tests/research/test_promotion_gates.py` | Weekly promotion-readiness summary across signals and blocker states. |
| `config/research_promotion_registry.yaml` | `N/A (config)` | `1.2` | `complete` | `Claude CLI` | `tests/research/test_promotion_gates.py` | Stage and threshold registry for promotion and graduation decisions. |

## Governance Hardening — Promotion Policy, Feature Registry, and Artifact Manifests

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `config/promotion_policy.yaml` | `N/A (config)` | `GH` | `complete` | `Claude CLI` | `Indirect / shared` | Centralized promotion eligibility, rollback, and canary thresholds for all promotable signals. |
| `config/feature_registry.yaml` | `N/A (config)` | `GH` | `complete` | `Claude CLI` | `Indirect / shared` | Canonical feature metadata registry with ranges, sources, versions, and freshness TTLs. |
| `config/feature_group_registry.yaml` | `N/A (config)` | `GH` | `complete` | `Claude CLI` | `Indirect / shared` | Logical feature groupings, downstream consumers, and freshness-policy strategy definitions. |
| `src/lineage/__init__.py` | `N/A (package marker)` | `GH` | `complete` | `Claude CLI` | `tests/lineage/test_artifact_manifest.py` | Package root for typed artifact lineage and manifest tooling. |
| `src/lineage/artifact_manifest_builder.py` | `ArtifactManifest`, `ArtifactManifestBuilder`, `LineageResolver` | `GH` | `complete` | `Claude CLI` | `tests/lineage/test_artifact_manifest.py` | Immutable artifact manifests, builder pattern, and ancestry / validation resolver for lineage integrity. |

## Pack 2 — Freshness, Trust, Packet Governance, and New Bridge Feeds

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/core/event_clock.py` | `EventClock` | `2.1` | `complete` | `Claude CLI` | `tests/test_freshness_governance.py` | Separates event time from processing time and annotates lag. |
| `src/core/source_quorum_engine.py` | `SourceQuorumEngine` | `2.1` | `complete` | `Claude CLI` | `tests/test_freshness_governance.py` | Checks fresh-source quorum before escalation or regime transition. |
| `src/core/packet_dedup_index.py` | `PacketDedupIndex` | `2.1` | `complete` | `Claude CLI` | `tests/test_freshness_governance.py` | Sliding-window duplicate suppression keyed by packet id. |
| `src/core/late_packet_handler.py` | `LatePacketHandler` | `2.1` | `complete` | `Claude CLI` | `tests/test_freshness_governance.py` | Stale-packet annotation, degradation, or discard policy. |
| `src/packets/schemas.py` | `BasePacket`, `MacroPolicyEvent`, `GeopoliticalEvent`, `PhysicalFlowEvent` | `2.2` | `complete` | `Codex CLI` | `Indirect via bridge packet tests` | Canonical packet dataclasses with schema-version support. |
| `src/packets/macro_policy_event.py` | `make_macro_policy_event()` | `2.2` | `complete` | `Codex CLI` | `Indirect via bridge packet tests` | Factory for schema-versioned macro policy packets. |
| `src/packets/geopolitical_event.py` | `make_geopolitical_event()` | `2.2` | `complete` | `Codex CLI` | `Indirect via bridge packet tests` | Factory for schema-versioned geopolitical packets. |
| `src/packets/physical_flow_event.py` | `make_physical_flow_event()` | `2.2` | `complete` | `Codex CLI` | `Indirect via bridge packet tests` | Factory for schema-versioned physical flow packets. |
| `src/bridges/maritime_bridge.py` | `MaritimeBridge` | `2.3` | `complete` | `Claude CLI` | `tests/bridges/test_new_bridges.py` | AIS chokepoint disruption scoring normalized into `PhysicalFlowEvent` packets. |
| `src/bridges/cds_sovereign_bridge.py` | `CDSSovereignBridge` | `2.3` | `complete` | `Claude CLI` | `tests/bridges/test_new_bridges.py` | Sovereign CDS stress feed normalized into `MacroPolicyEvent` packets. |
| `src/bridges/gpr_index_bridge.py` | `GPRIndexBridge` | `2.3` | `complete` | `Claude CLI` | `tests/bridges/test_new_bridges.py` | Geopolitical Risk index bridge normalized into `GeopoliticalEvent` packets. |
| `src/bridges/semiconductor_supply_bridge.py` | `SemiconductorSupplyBridge` | `2.3` | `complete` | `Claude CLI` | `tests/bridges/test_new_bridges.py` | Semiconductor supply and export-control proxy bridge with lineage-rich packets. |
| `config/freshness_policy.yaml` | `N/A (config)` | `2.1` | `complete` | `Claude CLI` | `tests/test_freshness_governance.py` | Per-source TTLs, backoff, stale actions, and quorum requirements. |
| `config/data_trust_hierarchy.yaml` | `N/A (config)` | `2.3` | `complete` | `Claude CLI` | `tests/test_policy_engine.py`, `tests/bridges/test_new_bridges.py` | Trust tiers and weights for official, operational, research, and experimental sources. |
| `config/packet_schema_versions.yaml` | `N/A (config)` | `2.2` | `complete` | `Codex CLI` | `Indirect via bridge packet tests` | Canonical schema versions for packet factories. |

## Pack 3 — Online Learning State and Encoder State Management

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/research/learning_state_persistence.py` | `LearningStatePersistence` | `3.1` | `complete` | `Claude CLI` | `No dedicated test file found in current tree` | Versioned persistence layer for online-learning state with blob/local fallback. |
| `src/research/online_weighted_feature_encoder.py` | `OnlineWeightedFeatureEncoder` | `3.2` | `present` | `Claude CLI` | `tests/research/test_online_encoder.py` | Feature encoder that loads learned weights, supports freeze logic, and tracks versions. |
| `src/research/telemetry_feature_joiner.py` | `TelemetryFeatureJoiner` | `3.3` | `present` | `Claude CLI` | `Indirect / shared` | Joins encoded features with trade telemetry and attached research scores. |
| `src/research/trade_outcome_telemetry_schema.py` | `TradeOutcomeRecord`, `TradeOutcomeTelemetry` | `3.3` | `present` | `Claude CLI` | `tests/research/test_evaluate_trade_outcomes.py` | Canonical schema for shadow/live trade outcome telemetry. |

## Pack 4 — Execution Hardening and Realism

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/execution/pre_trade_controls.py` | `PreTradeControls`, `PreTradeResult` | `4.0` | `complete` | `Claude CLI` | `tests/execution/test_execution_hardening.py` | Notional, concentration, spread, and message-rate guardrails before order construction. |
| `src/execution/order_state_machine.py` | `OrderStateMachine`, `OrderState`, `InvalidTransitionError` | `4.0` | `complete` | `Claude CLI` | `tests/execution/test_execution_hardening.py` | Legal order-state transitions with history and validation. |
| `src/execution/circuit_breaker.py` | `CircuitBreaker`, `CircuitOpenError` | `4.0` | `complete` | `Claude CLI` | `tests/execution/test_execution_hardening.py` | Broker-call circuit breaker with closed/open/half-open semantics. |
| `src/execution/microstructure_regime_classifier.py` | `MicrostructureRegimeClassifier`, `MicrostructureRegime` | `4.1` | `complete` | `Claude CLI` | `tests/execution/test_execution_hardening.py` | Regime classification for spread widening, delayed acks, queue decay, and stale broker state. |
| `src/execution/market_data_sanity_check.py` | `MarketDataSanityCheck`, `SanityCheckResult` | `4.2` | `present` | `Claude CLI` | `tests/execution/test_market_data_sanity.py` | Rejects stale, crossed, impossible, or low-quality quote inputs. |
| `src/execution/execution_realism.py` | `ExecutionRealismEngine` | `4.3` | `present` | `Claude CLI` | `tests/execution/test_execution_realism.py` | Models queue position, cancel/replace latency, auctions, LULD, and overnight gap realism. |
| `config/pre_trade_controls.yaml` | `N/A (config)` | `4.0` | `complete` | `Codex CLI` | `tests/execution/test_execution_hardening.py`, `tests/execution/test_market_data_sanity.py` | Canonical thresholds for order notional, spread, freshness, and concentration checks. |

## Pack 5 — Options Realism

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/options/__init__.py` | `N/A (package marker)` | `5.0` | `complete` | `Codex CLI` | `N/A` | Package root for research-only options realism helpers. |
| `src/options/options_chain_normalizer.py` | `OptionsChainNormalizer`, `CanonicalOptionContract` | `5.1` | `complete` | `Codex CLI` | `tests/options/test_options_chain_normalizer.py` | Normalizes heterogeneous option-chain payloads into a canonical research schema. |
| `src/options/options_liquidity_filter.py` | `OptionsLiquidityFilter` | `5.1` | `complete` | `Codex CLI` | `tests/options/test_options_liquidity_filter.py` | Rejects contracts with low open interest, thin volume, or excessive spread. |
| `src/options/spread_feasibility_checker.py` | `SpreadFeasibilityChecker` | `5.2` | `complete` | `Codex CLI` | `tests/options/test_spread_feasibility.py` | Validates leg availability, combined spread quality, and net premium math for multi-leg structures. |
| `src/options/options_margin_policy.py` | `OptionsMarginPolicy` | `5.2` | `complete` | `Codex CLI` | `tests/options/test_options_margin_policy.py` | Applies conservative margin heuristics to covered calls, spreads, and naked shorts. |
| `src/research/option_scenario_pricer.py` | `OptionScenarioPricer`, `OptionScenarioInput`, `OptionScenarioResult` | `5.3` | `present` | `Claude CLI` | `tests/research/test_option_scenario_pricer.py`, `tests/research/test_evaluate_trade_outcomes.py` | Research-only scenario payoff pricer for option hedge analysis and stress tests. |

## Pack 6 — Classical Baselines, Quantum Utility, and Higher-Order Objectives

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/research/classical_strong_baseline.py` | `ClassicalStrongBaseline` | `6.1` | `complete` | `Claude CLI` | `tests/research/test_quantum_enhancements.py` | Strong classical benchmark used to evaluate quantum-value claims. |
| `src/research/quantum_utility_score.py` | `QuantumUtilityScorer` | `6.2` | `complete` | `Claude CLI` | `tests/research/test_quantum_enhancements.py` | Scores quantum runs on objective improvement, runtime, feasibility, stability, and significance. |
| `src/research/qaoa_hyperparameter_library.py` | `QAOAHyperparameterLibrary` | `6.3` | `complete` | `Claude CLI` | `tests/research/test_quantum_enhancements.py` | Warm-start store for regime-conditioned QAOA parameters. |
| `src/research/higher_order_objectives.py` | `HigherOrderObjectives` | `6.4` | `complete` | `Claude CLI` | `tests/research/test_quantum_enhancements.py` | CVaR, drawdown, and skew-aware objective registry. |
| `src/research/rqaoa_structured_optimizer.py` | `RQAOAStructuredOptimizer` | `6.5` | `complete` | `Claude CLI` | `tests/research/test_quantum_enhancements.py` | Recursive QAOA reduction engine for structured optimization. |

## Pack 7 — Formulation Registry, Decomposition, and Quantum Request Plumbing

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/research/quantum_formulation_validator.py` | `QuantumFormulationValidator` | `7.1` | `complete` | `Claude CLI` | `tests/research/test_quantum_enhancements.py` | Validates quantum requests against registered formulation limits. |
| `src/research/quantum_decomposition_policy.py` | `QuantumDecompositionPolicy` | `7.2` | `present` | `Claude CLI` | `tests/research/test_decomposition_policy.py` | Enforces decomposition-first pruning before quantum optimization. |
| `src/research/quantum_optimizer_bridge.py` | `QuantumOptimizerBridge`, `OriginProviderConfig` | `7.2` | `present` | `Claude CLI` | `tests/research/test_quantum_vs_classical_job.py` | Hybrid bridge for classical preprocessing, quantum backends, and artifact-only result writing. |
| `src/research/build_quantum_request.py` | `QuantumRequestBuilder` | `7.3` | `present` | `Claude CLI` | `tests/research/test_build_quantum_request.py` | Builds `QuantumOptimizationRequest` packets from candidate, regime, and constraint inputs. |
| `src/research/build_regime_conditioned_request.py` | `main() / CLI script` | `7.3` | `present` | `Claude CLI` | `Indirect / shared` | Regime-conditioned request builder that composes optimizer, constraints, and request packaging. |
| `src/research/regime_conditioned_optimizer.py` | `RegimeConditionedOptimizer` | `7.3` | `present` | `Claude CLI` | `tests/research/test_regime_conditioned_optimizer.py` | Selects objectives and filters candidate universes based on regime state. |
| `config/qfinance_formulation_registry.yaml` | `N/A (config)` | `7.1` | `complete` | `Claude CLI` | `tests/research/test_quantum_enhancements.py` | Registry of allowed problem families, encodings, ansatz limits, and classical comparators. |
| `config/quantum_lane_policy.yaml` | `N/A (config)` | `7.2` | `present` | `Claude CLI` | `tests/research/test_decomposition_policy.py`, `tests/research/test_quantum_vs_classical_job.py` | Quantum lane policy, maturity stages, and decomposition/mitigation limits. |

## Pack 8 — Eval Harnesses and Frontier R&D

| Module path | Class / entry point | Pack | Status | Owner | Test file | Description |
| --- | --- | --- | --- | --- | --- | --- |
| `src/research/research_eval_harness.py` | `ResearchEvalHarness` | `8.0` | `present` | `Claude CLI` | `tests/research/test_research_eval_harness.py` | Multi-dimension evaluation harness for every research change before promotion. |
| `src/research/canary_encoder_comparator.py` | `CanaryEncoderComparator` | `8.0` | `present` | `Claude CLI` | `tests/research/test_canary_promotion.py` | Side-by-side current vs candidate encoder comparison for canary promotion. |
| `src/research/rollback_encoder_version.py` | `EncoderVersionManager` | `8.0` | `present` | `Claude CLI` | `tests/research/test_canary_promotion.py` | Saves, lists, and rolls back encoder versions. |
| `src/research/z3_safety_invariant_checks.py` | `Z3SafetyInvariantChecker`, `SafetyInvariant` | `8.1` | `complete` | `Claude CLI` | `tests/research/test_z3_safety_invariants.py` | Formal invariant checking for safety gates and execution prohibitions. |
| `src/research/cryptographic_reasoning_packet_signer.py` | `ReasoningPacketSigner` | `8.2` | `complete` | `Claude CLI` | `tests/research/test_cryptographic_packet_signer.py` | HMAC signer for reasoning packets and chain-of-custody traces. |
| `src/research/experimental_qntk_ucb_lane.py` | `ExperimentalQNTKUCBLane` | `8.3` | `complete` | `Claude CLI` | `tests/research/test_experimental_qntk_ucb.py` | Research-only QNTK-inspired diversification lane with UCB exploration. |
| `src/research/experimental_sipqc_mc_lane.py` | `ExperimentalSIPQCMCLane` | `8.4` | `complete` | `Claude CLI` | `tests/research/test_experimental_sipqc_mc.py` | Parameterized-circuit-inspired Monte Carlo scenario research lane. |
| `src/research/knowledge_graph_fusion_prototype.py` | `KnowledgeGraphFusionPrototype`, `GraphNode`, `GraphEdge` | `8.5` | `in_progress` | `Claude CLI / agent build` | `tests/research/test_knowledge_graph_fusion.py` | Research-only geopolitical and market graph fusion prototype. |

## Totals Snapshot

- Core / governance modules tracked here: 9
- Research modules tracked here: 26
- Execution modules tracked here: 7
- Options modules tracked here: 6
- Bridge modules tracked here: 5 including the legacy political bridge, 4 net-new data bridges
- Governance-hardening adjunct modules tracked here: 5
- Config surfaces tracked here: 11
- Contract headline: `358` tests passing, `100/100` validation, deployed to VM

## Notes

- Pack `0.1` is intentionally listed as `skipped`; the user explicitly chose not to quarantine political disclosure paths.
- Some modules that exist in the repo are marked `present` rather than `complete` because the contract excerpt does not include a separate completion line for that exact file even though the code and tests are present.
- `tests/research/test_quantum_enhancements.py` is a shared suite covering multiple Pack 6 and Pack 7 quantum research modules.
