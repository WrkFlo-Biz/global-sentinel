#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Crisis Monitor (Main Loop)

The 24/7 monitoring loop that:
1. Polls all bridges (FRED, EIA, Finnhub, GDELT, Aviation, Microstructure)
2. Scores regime shift probability
3. Classifies time windows
4. Checks risk gates
5. Emits scorecards and flash memos
6. Manages operating mode transitions (NORMAL/ELEVATED/CRISIS/MANUAL_REVIEW)

Safety:
- Shadow mode only (all order operations are shadow/paper)
- Kill switch and manual veto checked every cycle
- Config frozen in CRISIS mode
"""

from __future__ import annotations

import argparse
import json
import os  # noqa: F401
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_yaml_safe(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# --- Operating modes and thresholds ---
MODES = ["NORMAL", "ELEVATED", "CRISIS", "MANUAL_REVIEW"]
MODE_POLL_INTERVALS = {
    "NORMAL": 300,       # 5 min (day-trading speed)
    "ELEVATED": 120,     # 2 min
    "CRISIS": 30,        # 30 sec
    "MANUAL_REVIEW": 0,  # paused (manual trigger only)
}


class CrisisMonitor:
    """Main monitoring loop for Global Sentinel."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.running = True
        self.cycle_count = 0
        self._shutdown_started = False

        # Load config
        self.thresholds = load_yaml_safe(repo_root / "config" / "thresholds.yaml")

        # State
        self.current_mode = "NORMAL"
        self.last_scorecard: Dict[str, Any] = {}

        # Paths
        self.control_dir = repo_root / "control"
        self.logs_dir = repo_root / "logs"
        self.scorecards_dir = self.logs_dir / "scorecards"
        self.events_dir = self.logs_dir / "events"
        self.risk_dir = self.logs_dir / "risk_checks"
        for d in [self.scorecards_dir, self.events_dir, self.risk_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # Heartbeat file for healthcheck
        self.heartbeat_path = self.logs_dir / "heartbeat.json"

        # Alerting
        self.alerter = self._load_alerter()

        # Dual-strategy manager and Telegram notifier
        try:
            from src.execution.strategy_manager import StrategyManager
            self.strategy_manager = StrategyManager(repo_root)
        except Exception:
            self.strategy_manager = None

        try:
            from src.monitoring.telegram_notifier import TelegramNotifier
            self.notifier = TelegramNotifier(repo_root)
        except Exception:
            self.notifier = None

        # Start hourly position updates
        if self.notifier and self.strategy_manager:
            try:
                self.notifier.start_hourly_updates(self._fetch_all_positions, self.strategy_manager)
            except Exception:
                pass

        # Re-enabled: OpenClaw gateway container app was removed; bots need a
        # local handler for both /gs_ commands and general LLM chat.
        try:
            from src.monitoring.telegram_bot_manager import TelegramBotManager
            self.bot_manager = TelegramBotManager(repo_root)
            self.bot_manager.start()
        except Exception as _bm_err:
            print(f"[{iso_now()}] bot_manager start failed: {_bm_err}")
            self.bot_manager = None

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def run(self, interval_override: Optional[int] = None):
        """Main loop. Runs until SIGTERM/SIGINT."""
        print(f"[{iso_now()}] Crisis Monitor starting (mode={self.current_mode})")

        # Send startup notification
        if self.alerter:
            try:
                self.alerter.send_startup_alert()
            except Exception:
                pass

        try:
            while self.running:
                try:
                    self._run_cycle()
                except KeyboardInterrupt:
                    self.running = False
                    break
                except BaseException as e:
                    self._log_event("cycle_error", {"error": str(e), "cycle": self.cycle_count})
                    print(f"[{iso_now()}] Cycle error: {e}", file=sys.stderr)

                # Determine poll interval
                if interval_override is not None:
                    sleep_sec = interval_override
                else:
                    sleep_sec = MODE_POLL_INTERVALS.get(self.current_mode, 900)

                if sleep_sec <= 0:
                    # MANUAL_REVIEW mode — wait for external trigger
                    print(f"[{iso_now()}] MANUAL_REVIEW mode — paused. Send SIGUSR1 to trigger cycle.")
                    signal.signal(signal.SIGUSR1, lambda s, f: None)
                    signal.pause()
                    continue

                # Sleep with interruptibility
                for _ in range(sleep_sec):
                    if not self.running:
                        break
                    time.sleep(1)
        finally:
            self._shutdown_background_workers()
            print(f"[{iso_now()}] Crisis Monitor shutting down after {self.cycle_count} cycles")

    def _run_cycle(self):
        """Execute one monitoring cycle."""
        # Ensure repo root stays in sys.path (guards against long-running corruption)
        repo_str = str(self.repo_root)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)
        self.cycle_count += 1
        cycle_start = iso_now()
        print(f"[{cycle_start}] Cycle {self.cycle_count} starting (mode={self.current_mode})")

        # 1. Check kill switch and manual veto
        kill_switch = load_json(self.control_dir / "kill_switch.json")
        manual_veto = load_json(self.control_dir / "manual_veto.json")

        if kill_switch.get("active", False):
            self._log_event("kill_switch_active", {"cycle": self.cycle_count})
            self._update_heartbeat("kill_switch_active")
            if self.alerter:
                try:
                    self.alerter.send_kill_switch_alert()
                except Exception:
                    pass
            print(f"[{iso_now()}] KILL SWITCH ACTIVE — skipping cycle")
            return

        # 2. Poll bridges (with error isolation)
        bridge_results = self._poll_bridges()
        bridge_results = self._stabilize_bridge_inputs_for_scorecard(bridge_results)

        # 3. Build composite snapshot
        snapshot = self._build_snapshot(bridge_results, kill_switch, manual_veto)

        # 3.5. Feature freshness check (V4 operational hardening)
        freshness_result = self._normalize_feature_freshness_result(
            self._check_feature_freshness(bridge_results)
        )

        # 4. Score regime shift (with freshness-aware confidence penalty)
        regime_score = self._score_regime(snapshot)
        freshness_penalty = freshness_result.get("max_confidence_penalty", 0.0)
        if freshness_penalty > 0:
            original_confidence = regime_score.get("confidence", 0.0)
            regime_score["confidence"] = max(0.0, original_confidence * (1.0 - freshness_penalty))
            regime_score["_freshness_penalty_applied"] = freshness_penalty
            regime_score["_original_confidence"] = original_confidence

        # 5. Classify time window
        time_window = self._classify_time_window(snapshot)

        # 6. Determine operating mode (capture decision trace for replay)
        mode_decision = self._resolve_mode_with_trace(regime_score, snapshot)
        new_mode = mode_decision["final_mode"]
        if new_mode != self.current_mode:
            self._log_event("mode_transition", {
                "from": self.current_mode,
                "to": new_mode,
                "regime_shift_probability": regime_score.get("regime_shift_probability"),
            })
            print(f"[{iso_now()}] MODE TRANSITION: {self.current_mode} -> {new_mode}")
            if self.alerter:
                try:
                    self.alerter.send_mode_transition(self.current_mode, new_mode, {
                        "regime_shift_probability": regime_score.get("regime_shift_probability"),
                        "confidence": regime_score.get("confidence"),
                        "cycle": self.cycle_count,
                        "evidence": regime_score.get("evidence", []),
                    })
                except Exception:
                    pass
            self.current_mode = new_mode

        # 6.5. Compute config fingerprint for replayability
        config_fp = self._compute_config_fingerprint()

        # 6.6. Check Blob persistence health
        blob_health = self._check_blob_health()

        # 7. Build and persist scorecard
        scorecard = {
            "schema_version": "scorecard.v6",
            "timestamp_utc": cycle_start,
            "cycle": self.cycle_count,
            "mode": self.current_mode,
            "regime_shift_probability": regime_score.get("regime_shift_probability", 0.0),
            "component_scores": regime_score.get("component_scores", {}),
            "confidence": regime_score.get("confidence", 0.0),
            "evidence": regime_score.get("evidence", []),
            "data_freshness_status": bridge_results.get("freshness", {}),
            "threshold_values_used": self.thresholds,
            "risk_gate_status": "active",
            "manual_veto_status": manual_veto.get("active", False),
            "kill_switch_status": kill_switch.get("active", False),
            "fallback_mode_status": bridge_results.get("fallback_mode", False),
            "shadow_execution_eligible": self._shadow_eligible(snapshot, time_window),
            "time_window": time_window,
            "bridge_summary": bridge_results.get("summary", {}),
            "bridge_errors": bridge_results.get("bridge_errors", []),
            "gss_signal": None,  # populated in step 8.7 if non-neutral
            "v4_governance": self._v4_governance_check(bridge_results),
            # V4 replay-grade decision fields
            "mode_decision_trace": mode_decision,
            "quorum_state": mode_decision.get("quorum_evaluation"),
            "policy_decision_trace": mode_decision.get("policy_evaluation"),
            # V4 operational hardening fields
            "feature_freshness": freshness_result,
            "freshness_penalty": regime_score.get("_freshness_penalty_applied", 0.0),
            "original_confidence": regime_score.get("_original_confidence"),
            "config_fingerprint": config_fp.get("combined_fingerprint", ""),
            "config_versions": config_fp.get("configs", {}),
            "degraded_mode": freshness_result.get("active_degraded_groups", 0) > 0,
            "persistence_mode": blob_health.get("persistence_mode", "unknown") if blob_health else "unchecked",
            "blob_health_status": blob_health.get("status") if blob_health else None,
        }

        # 7.5. Chokepoint risk scoring (live geopolitical scenario monitor)
        try:
            from src.research.training.chokepoint_scenarios import (
                compute_chokepoint_risk_score,
                get_chokepoint_telegram_summary,
            )
            chokepoint_scores = compute_chokepoint_risk_score(bridge_results)
            scorecard["chokepoint_risk"] = chokepoint_scores

            if chokepoint_scores.get("scenario_match"):
                scorecard["_chokepoint_playbook"] = {
                    "scenario": chokepoint_scores["scenario_match"],
                    "recommended_playbook": chokepoint_scores.get("recommended_playbook"),
                    "informational_only": True,
                    "not_for_direct_execution": True,
                }

            # Add to Telegram digest on full cycles or when active > 1
            if chokepoint_scores.get("active_chokepoints", 0) >= 2 and self.alerter:
                try:
                    self.alerter._dispatch(
                        "chokepoint_risk",
                        get_chokepoint_telegram_summary(chokepoint_scores),
                        throttle=True,
                    )
                except Exception:
                    pass
        except Exception as e:
            print("[%s] Chokepoint scoring error (non-fatal): %s" % (iso_now(), e), file=sys.stderr)

        self.last_scorecard = scorecard

        # V4 operational alerts (blocked escalations, freshness, config drift)
        try:
            from src.monitoring.operational_alerts import OperationalAlerts
            op_alerts = OperationalAlerts(self.repo_root, alerter=self.alerter)
            emitted = op_alerts.check_and_alert(scorecard)
            if emitted:
                scorecard["operational_alerts"] = [a["alert_type"] for a in emitted]
        except Exception as e:
            print(f"[{iso_now()}] Operational alerts error (non-fatal): {e}", file=sys.stderr)

        # 8. Shadow execution: generate trade ideas and route to paper broker
        if scorecard.get("shadow_execution_eligible"):
            shadow_result = self._run_shadow_execution(scorecard, bridge_results)
            if shadow_result and shadow_result.get("submitted_open_or_ack_count", 0) > 0:
                strategy_breakdown = {}
                for strategy_name, sres in (shadow_result.get("strategy_results") or {}).items():
                    strategy_breakdown[strategy_name] = {
                        "submit_attempt_count": sres.get("submit_attempt_count"),
                        "submitted_open_or_ack_count": sres.get("submitted_open_or_ack_count"),
                        "broker_rejected_count": sres.get("broker_rejected_count"),
                        "candidate_count_in_package": sres.get("candidate_count_in_package"),
                        "selected_candidate_count": len(sres.get("selected_candidates", []) or []),
                        "time_window_name": sres.get("time_window_name"),
                    }
                self._log_event("shadow_orders_submitted", {
                    "cycle": self.cycle_count,
                    "orders_submitted": shadow_result.get("submitted_open_or_ack_count", 0),
                    "candidates": len(shadow_result.get("selected_candidates", [])),
                    "strategy_breakdown": strategy_breakdown,
                })
                # Only send Telegram alert for new orders (not duplicate cycles)
                if self.alerter:
                    try:
                        self.alerter.send_shadow_execution_alert(shadow_result, scorecard)
                    except Exception:
                        pass

        # 8.5. Position management: check profit targets and stop losses
        try:
            from src.execution.position_manager import PositionManager
            pm = PositionManager(self.repo_root)
            pm_result = pm.run_check()
            if pm_result.get("actions_taken", 0) > 0:
                self._log_event("position_management", {
                    "cycle": self.cycle_count,
                    "actions": pm_result.get("actions_taken", 0),
                    "profits_taken": pm_result.get("profits_taken", 0),
                    "stops_hit": pm_result.get("stops_hit", 0),
                    "eod_flattened": pm_result.get("eod_flattened", 0),
                })
                if self.alerter:
                    try:
                        self._send_position_alert(pm_result)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[{iso_now()}] Position management error: {e}", file=sys.stderr)

        # 8.7. GSS Signal Analysis: consciousness-market axis detection
        try:
            from src.alpha.gss_execution_engine import GSSExecutionEngine
            gss = GSSExecutionEngine(self.repo_root)
            gss_result = gss.analyze(snapshot, scorecard)

            if gss_result.get("gss_signal") != "NEUTRAL":
                self._log_event("gss_signal_detected", {
                    "cycle": self.cycle_count,
                    "signal": gss_result.get("gss_signal"),
                    "action": gss_result.get("action"),
                    "confidence": gss_result.get("confidence"),
                    "reason": gss_result.get("reason"),
                })

                # Send Telegram alert for non-neutral GSS signals
                if self.alerter:
                    try:
                        signal_val = gss_result.get("gss_signal", "UNKNOWN")
                        action = gss_result.get("action", "UNKNOWN")
                        reason = gss_result.get("reason", "")
                        confidence = gss_result.get("confidence", 0)
                        hedges = gss_result.get("hedge_recommendations", [])

                        msg_lines = [
                            f"GSS SIGNAL: {signal_val}",
                            f"Action: {action}",
                            f"Confidence: {confidence:.0%}",
                            f"Reason: {reason}",
                        ]
                        if hedges:
                            msg_lines.append(f"Hedge Recommendations: {len(hedges)}")
                            for h in hedges[:5]:
                                msg_lines.append(f"  - {h.get('symbol', '?')} {h.get('action', '?')}: {h.get('reason', '')[:60]}")

                        self.alerter._dispatch(
                            f"GSS: {signal_val}",
                            "\n".join(msg_lines),
                            level="warning" if signal_val in ("BLACK_SWAN_SHIELD", "GAMMA_SQUEEZE") else "info",
                            extra={"event": "gss_signal", "signal": signal_val},
                        )
                    except Exception:
                        pass

                # Add GSS signal to scorecard for dashboard
                scorecard["gss_signal"] = {
                    "signal": gss_result.get("gss_signal"),
                    "action": gss_result.get("action"),
                    "confidence": gss_result.get("confidence"),
                    "reason": gss_result.get("reason"),
                    "hedge_count": len(gss_result.get("hedge_recommendations", [])),
                }
        except Exception as e:
            print(f"[{iso_now()}] GSS analysis error: {e}", file=sys.stderr)

        # 8.9. Multi-backend quantum research comparison (artifact-only, never influences execution)
        try:
            self._run_quantum_research_comparison(scorecard, bridge_results)
        except Exception as e:
            print(f"[{iso_now()}] Quantum research comparison error (non-fatal): {e}", file=sys.stderr)

        # ═══════════════════════════════════════════════════════════════
        # V6 INTEGRATION: Research + Execution modules
        # ═══════════════════════════════════════════════════════════════

        # V6.1: Point-in-Time data capture (every cycle)
        try:
            from src.research.pit_data_store import PointInTimeDataStore
            pit = PointInTimeDataStore(repo_root=self.repo_root)
            pit.capture(bridge_results=bridge_results, scorecard=scorecard)
        except Exception as e:
            print(f"[{iso_now()}] PIT capture error (non-fatal): {e}", file=sys.stderr)

        # V6.0: Exposure tracking (lightweight - from strategy config)
        try:
            from src.risk.exposure_book import ExposureBook
            from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter

            adapters = {}
            seen_creds = set()
            for strategy_name in ("day_trade", "medium_long"):
                creds = self._resolve_alpaca_credentials(strategy_name)
                key = (
                    (creds or {}).get("api_key") or "default",
                    (creds or {}).get("api_secret") or "default",
                )
                if key in seen_creds:
                    continue
                seen_creds.add(key)
                if creds:
                    adapters[strategy_name] = AlpacaPaperAdapter(
                        api_key=creds.get("api_key"),
                        api_secret=creds.get("api_secret"),
                    )
                elif not adapters:
                    adapters["default"] = AlpacaPaperAdapter()

            if adapters:
                eb = ExposureBook(adapters)
                v6_exposure = eb.snapshot()
                account_rows = list((v6_exposure.get("accounts") or {}).values())
                scorecard["v6_exposure"] = v6_exposure
                scorecard["v6_exposure_summary"] = {
                    "combined_equity": ((v6_exposure.get("combined") or {}).get("total_equity", 0.0)),
                    "gross_exposure_pct": ((v6_exposure.get("combined") or {}).get("gross_exposure_pct", 0.0)),
                    "net_exposure_pct": ((v6_exposure.get("combined") or {}).get("net_exposure_pct", 0.0)),
                    "raw_gross_exposure_pct": ((v6_exposure.get("combined") or {}).get("raw_gross_exposure_pct", 0.0)),
                    "pending_close_orders": sum(int((row or {}).get("pending_close_orders", 0)) for row in account_rows),
                    "pending_close_notional": sum(float((row or {}).get("pending_close_notional", 0.0) or 0.0) for row in account_rows),
                    "open_order_count": sum(len((row or {}).get("open_orders", []) or []) for row in account_rows),
                    "oil_delta": ((v6_exposure.get("risk_metrics") or {}).get("oil_delta", 0.0)),
                }
        except Exception as e:
            print(f"[{iso_now()}] Exposure book error (non-fatal): {e}", file=sys.stderr)

        # V6.2: Edge Detector — find cascades, divergences, signal lag
        v6_edge_findings = {}
        try:
            from src.alpha.edge_detector import EdgeDetector
            ed = EdgeDetector(repo_root=self.repo_root)
            v6_edge_findings = ed.scan(
                bridge_results=bridge_results,
                scorecard=scorecard,
            )
            scorecard["v6_edge_findings"] = v6_edge_findings
            scorecard["v6_edge_summary"] = ed.format_telegram(v6_edge_findings)
            if v6_edge_findings.get("cascade_findings") and self.alerter:
                try:
                    self.alerter._dispatch(
                        "edge_detector",
                        ed.format_telegram(),
                        throttle=True,
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[{iso_now()}] Edge detector error (non-fatal): {e}", file=sys.stderr)

        # V6.3: Cross-Asset Signals — bonds, currencies, commodities
        try:
            from src.alpha.cross_asset_signals import CrossAssetSignals
            cas = CrossAssetSignals(repo_root=self.repo_root)
            cas_result = cas.scan(bridge_results=bridge_results)
            scorecard["v6_cross_asset_signals"] = cas_result
        except Exception as e:
            print(f"[{iso_now()}] Cross-asset signals error (non-fatal): {e}", file=sys.stderr)

        # V6.4: Strategy Engine — evaluate 15 war strategies
        try:
            from src.alpha.strategy_engine import StrategyEngine
            se = StrategyEngine(repo_root=self.repo_root)
            strategy_ideas = se.evaluate_entries(
                scorecard=scorecard,
                bridge_results=bridge_results,
            )
            scorecard["v6_strategy_ideas"] = [
                {k: v for k, v in idea.items() if k != "raw_data"}
                for idea in strategy_ideas
            ] if strategy_ideas else []
            active_strategies = sorted({idea.get("strategy") for idea in strategy_ideas if idea.get("strategy")})
            scorecard["v6_strategy_summary"] = {
                "active_count": len(active_strategies),
                "idea_count": len(strategy_ideas or []),
                "active_strategies": active_strategies[:15],
                "ideas": [
                    {k: v for k, v in idea.items() if k != "raw_data"}
                    for idea in (strategy_ideas or [])[:15]
                ],
            }
            if strategy_ideas and self.alerter:
                try:
                    self.alerter._dispatch(
                        "strategy_engine",
                        f"Strategy ideas: {len(strategy_ideas)} — " +
                        ", ".join(f"{i['strategy']}:{i['symbol']}" for i in strategy_ideas[:5]),
                        throttle=True,
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[{iso_now()}] Strategy engine error (non-fatal): {e}", file=sys.stderr)

        # V6.5: War Opportunity Scanner — hidden opportunities
        try:
            from src.alpha.war_opportunity_scanner import WarOpportunityScanner
            wos = WarOpportunityScanner(repo_root=self.repo_root)
            scanner_result = wos.scan(
                bridge_results=bridge_results,
                scorecard=scorecard,
            )
            scanner_discoveries = scanner_result.get("discoveries", [])
            scorecard["v6_scanner_discoveries"] = scanner_discoveries

            scanner_review_ideas = []
            for discovery in sorted(
                [d for d in scanner_discoveries if isinstance(d, dict)],
                key=lambda item: float(item.get("confidence", 0.0)),
                reverse=True,
            ):
                confidence = float(discovery.get("confidence", 0.0))
                if confidence < 0.50:
                    continue
                symbol = str(discovery.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                category = str(discovery.get("category") or "scanner").strip().lower()
                action = str(discovery.get("action") or "watch").strip().lower()
                scanner_review_ideas.append(
                    {
                        "symbol": symbol,
                        "direction": "short" if action == "short" else "long",
                        "strategy": f"scanner_{category}",
                        "entry_reason": (
                            f"Scanner discovery: {category.replace('_', ' ')} "
                            f"via {discovery.get('source', 'scanner')}"
                        ),
                        "confidence": confidence,
                        "requires_human_approval": True,
                        "informational_only": True,
                        "not_for_direct_execution": True,
                        "execution_influence_forbidden": True,
                        "source": "war_opportunity_scanner",
                    }
                )

            if scanner_review_ideas:
                scorecard["v6_scanner_review_ideas"] = scanner_review_ideas[:10]
                existing_ideas = list(scorecard.get("v6_strategy_ideas") or [])
                existing_keys = {
                    (
                        str(idea.get("symbol") or "").upper(),
                        str(idea.get("strategy") or ""),
                    )
                    for idea in existing_ideas
                    if isinstance(idea, dict)
                }
                for idea in scanner_review_ideas[:10]:
                    key = (str(idea.get("symbol") or "").upper(), str(idea.get("strategy") or ""))
                    if key not in existing_keys:
                        existing_ideas.append(idea)
                        existing_keys.add(key)
                scorecard["v6_strategy_ideas"] = existing_ideas[:25]

                summary = dict(scorecard.get("v6_strategy_summary") or {})
                active_strategies = set(summary.get("active_strategies") or [])
                for idea in scorecard["v6_strategy_ideas"]:
                    strat = idea.get("strategy")
                    if strat:
                        active_strategies.add(strat)
                summary["active_count"] = len(active_strategies)
                summary["idea_count"] = len(scorecard["v6_strategy_ideas"])
                summary["active_strategies"] = sorted(active_strategies)[:20]
                summary["ideas"] = [
                    {k: v for k, v in idea.items() if k != "raw_data"}
                    for idea in scorecard["v6_strategy_ideas"][:15]
                ]
                summary["scanner_review_count"] = len(scanner_review_ideas[:10])
                scorecard["v6_strategy_summary"] = summary

            high_conviction = [
                item for item in scanner_discoveries
                if isinstance(item, dict) and float(item.get("confidence", 0.0)) >= 0.80
            ]
            if high_conviction and self.alerter:
                top = sorted(
                    high_conviction,
                    key=lambda item: float(item.get("confidence", 0.0)),
                    reverse=True,
                )[0]
                try:
                    self.alerter._dispatch(
                        "scanner_high_conviction",
                        (
                            "High-conviction scanner hit: "
                            f"{top.get('symbol', '?')} "
                            f"{str(top.get('category') or top.get('signal_type') or 'discovery').replace('_', ' ')} "
                            f"({float(top.get('confidence', 0.0)):.0%})"
                        ),
                        throttle=True,
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[{iso_now()}] War scanner error (non-fatal): {e}", file=sys.stderr)

        # V6.9: Oil-Shock Regime Classification
        try:
            from src.alpha.oil_shock_regime import OilShockRegime
            osr = OilShockRegime()
            oil_regime_result = osr.run_cycle(
                bridge_results=bridge_results,
                scorecard=scorecard,
            )
            scorecard["v6_oil_regime"] = oil_regime_result["regime"]
            scorecard["v6_oil_regime_detail"] = oil_regime_result
            scorecard["v6_oil_regime_modifiers"] = oil_regime_result["modifiers"]

            # Apply oil regime to strategy ideas if they exist
            strategy_ideas = scorecard.get("v6_strategy_ideas", [])
            if strategy_ideas and oil_regime_result["regime"] != "NORMAL":
                modified_ideas = osr.apply_to_ideas(strategy_ideas, oil_regime_result["regime"])
                scorecard["v6_strategy_ideas"] = modified_ideas
                scorecard["_oil_modified_count"] = len(modified_ideas)

            # Fire alert if regime is SHOCK or DISLOCATION
            if oil_regime_result["regime"] in ("SHOCK", "DISLOCATION") and self.alerter:
                try:
                    self.alerter._dispatch(
                        "OIL_REGIME",
                        oil_regime_result["telegram_line"],
                        level="warning",
                        throttle=True,
                    )
                except Exception:
                    pass

            # Log risk warnings
            for warning in oil_regime_result.get("risk_warnings", []):
                print(f"[{iso_now()}] Oil regime risk: {warning}", file=sys.stderr)
        except Exception as e:
            print(f"[{iso_now()}] Oil regime error (non-fatal): {e}", file=sys.stderr)

        # V6.6: Deescalation Detector — ceasefire/peace signals
        try:
            from src.monitoring.deescalation_detector import DeescalationDetector
            dd = DeescalationDetector()
            deesc_result = dd.check(bridge_results=bridge_results, scorecard=scorecard)
            scorecard["v6_deescalation"] = deesc_result
            if deesc_result.get("detected") and deesc_result.get("confidence", 0) > 0.5 and self.alerter:
                try:
                    self.alerter._dispatch(
                        "CEASEFIRE_SIGNAL",
                        dd.format_telegram(),
                        level="warning",
                        throttle=False,
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[{iso_now()}] Deescalation detector error (non-fatal): {e}", file=sys.stderr)

        # V6.7: Scenario Simulator (every 20 cycles ~ hourly)
        if self.cycle_count % 20 == 0:
            try:
                from src.risk.scenario_simulator import ScenarioSimulator
                sim = ScenarioSimulator()
                sim_results = sim.simulate_all({})
                scorecard["v6_scenarios"] = {
                    name: {"pnl_impact_usd": r.get("pnl_impact_usd", 0)}
                    for name, r in sim_results.items()
                }
            except Exception as e:
                print(f"[{iso_now()}] Scenario simulator error (non-fatal): {e}", file=sys.stderr)

        # V6.8: Alert Manager — centralized alerting
        try:
            from src.monitoring.alert_manager import AlertManager
            am = AlertManager(repo_root=self.repo_root)
            am.fire_all(scorecard)
        except Exception as e:
            print(f"[{iso_now()}] Alert manager error (non-fatal): {e}", file=sys.stderr)

        # V6 Telegram digest — consolidated summary of all V6 modules
        self._send_v6_telegram_digest(scorecard)

        # ═══════════════════════════════════════════════════════════════
        # END V6 INTEGRATION
        # ═══════════════════════════════════════════════════════════════

        # Persist the fully-enriched scorecard after all additive V6 modules run.
        self.last_scorecard = scorecard
        self._persist_scorecard(scorecard)
        self._update_heartbeat("ok")

        # Send scorecard summary alerts in ELEVATED/CRISIS modes after V6 enrichment.
        if self.current_mode in ("ELEVATED", "CRISIS") and self.alerter:
            try:
                self.alerter.send_scorecard_summary(scorecard)
            except Exception:
                pass

        # 9. Periodic performance summary (every 10 cycles)
        if self.cycle_count % 10 == 0:
            try:
                from src.execution.performance_tracker import PerformanceTracker
                tracker = PerformanceTracker(self.repo_root)
                summary = tracker.generate_summary()
                if summary.get("total_trades", 0) > 0 and self.alerter:
                    self.alerter.send_performance_summary(summary)
            except Exception:
                pass

        print(f"[{iso_now()}] Cycle {self.cycle_count} complete — mode={self.current_mode}, "
              f"regime_p={scorecard['regime_shift_probability']:.3f}, "
              f"confidence={scorecard['confidence']:.3f}")

    # --- V4 governance integration ---
    def _v4_governance_check(self, bridge_results: Dict[str, Any]) -> Dict[str, Any]:
        """Run V4 governance modules and return status dict.

        Gracefully degrades if any V4 module is unavailable or errors out.
        """
        result: Dict[str, Any] = {
            "source_quorum": None,
            "policy_mode": None,
            "freshness_status": None,
        }

        # Source quorum check
        try:
            from src.core.source_quorum_engine import SourceQuorumEngine
            sqe = SourceQuorumEngine(config_dir=self.repo_root / "config")
            freshness_map = bridge_results.get("freshness", {})
            # Convert bool freshness values to timestamp strings where needed
            source_timestamps: Dict[str, str] = {}
            for src_name, val in freshness_map.items():
                if isinstance(val, str):
                    source_timestamps[src_name] = val
                elif val is True:
                    source_timestamps[src_name] = iso_now()
            trust_cfg_path = self.repo_root / "config" / "data_trust_hierarchy.yaml"
            trust_hierarchy = load_yaml_safe(trust_cfg_path)
            result["source_quorum"] = sqe.check_execution_quorum(source_timestamps, trust_hierarchy)
        except Exception as e:
            result["source_quorum"] = {"error": str(e)}

        # Policy engine mode
        try:
            from src.core.policy_engine import PolicyEngine
            pe = PolicyEngine(config_dir=self.repo_root / "config")
            result["policy_mode"] = pe._current_mode()
        except Exception as e:
            result["policy_mode"] = f"error: {e}"

        # Event clock freshness on latest packets
        try:
            from src.core.event_clock import EventClock
            ec = EventClock()
            stale_count = 0
            checked = 0
            for src_name, data in bridge_results.items():
                if src_name in ("freshness", "summary", "fallback_mode"):
                    continue
                packets = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
                for pkt in packets[:5]:  # cap per-source to avoid perf hit
                    if isinstance(pkt, dict) and pkt.get("timestamp_utc"):
                        annotated = ec.annotate_packet(dict(pkt))
                        checked += 1
                        if annotated.get("_event_clock", {}).get("stale"):
                            stale_count += 1
            result["freshness_status"] = {
                "packets_checked": checked,
                "stale_count": stale_count,
                "all_fresh": stale_count == 0 and checked > 0,
            }
        except Exception as e:
            result["freshness_status"] = {"error": str(e)}

        return result

    def _check_feature_freshness(self, bridge_results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run feature freshness enforcement and return summary for scorecard.

        Bridge freshness uses source names (fred, gdelt) while the feature
        registry uses feature names (base_score, event_score). We map bridge
        sources to feature-registry sources so the enforcer can match them.
        If no features match bridge sources, return a clean result instead
        of applying max penalty for unrecognized names.
        """
        try:
            from src.core.feature_freshness_enforcer import FeatureFreshnessEnforcer
            ffe = FeatureFreshnessEnforcer(config_dir=self.repo_root / "config")
            if not ffe.is_loaded:
                return None
            now = datetime.now(timezone.utc)

            # Build bridge source timestamps
            freshness_map = bridge_results.get("freshness", {})
            source_timestamps: Dict[str, Any] = {}
            for src_name, val in freshness_map.items():
                if isinstance(val, str):
                    try:
                        source_timestamps[src_name] = datetime.fromisoformat(
                            val.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        source_timestamps[src_name] = None
                elif val is True:
                    source_timestamps[src_name] = now
                else:
                    source_timestamps[src_name] = None

            # Map bridge sources to feature names via feature registry
            # Features list their source (e.g. base_score.source = qfinance_feature_encoder)
            # Bridge sources are names like fred, gdelt, market_microstructure
            # For features sourced from a bridge, inherit the bridge timestamp
            feature_timestamps: Dict[str, Any] = {}
            if hasattr(ffe, '_features') and ffe._features:
                for feat_name, feat_def in ffe._features.items():
                    feat_source = feat_def.get("source", "") if isinstance(feat_def, dict) else ""
                    # Direct match: feature source matches a bridge source
                    if feat_source in source_timestamps:
                        feature_timestamps[feat_name] = source_timestamps[feat_source]
                    # Bridge source matches feature name directly
                    elif feat_name in source_timestamps:
                        feature_timestamps[feat_name] = source_timestamps[feat_name]
                    # Feature from internal encoder — mark as fresh if any bridge is fresh
                    elif feat_source in ("qfinance_feature_encoder", "regime_conditioned_optimizer",
                                         "online_weighted_feature_encoder"):
                        any_fresh = any(v is not None and v is not False for v in source_timestamps.values())
                        feature_timestamps[feat_name] = now if any_fresh else None
                    else:
                        # Prefix match: bridge key "options_greeks" matches source "options_greeks_bridge"
                        matched_bridge = next(
                            (src for src in source_timestamps if feat_source.startswith(src)),
                            None,
                        )
                        if matched_bridge is not None:
                            feature_timestamps[feat_name] = source_timestamps[matched_bridge]
                        # No bridge exists for this feature — not applicable, not stale
                        # Omit from timestamps so enforcer doesn't penalize for missing bridges

            # If no features could be mapped, return clean result
            if not feature_timestamps:
                fresh_count = sum(1 for v in source_timestamps.values() if v is not None)
                return {
                    "source": "bridge_level_only",
                    "fresh_sources": fresh_count,
                    "total_sources": len(source_timestamps),
                    "max_confidence_penalty": 0,
                    "critical_max_confidence_penalty": 0,
                    "overall_max_confidence_penalty": 0,
                    "degraded_groups": 0,
                    "advisory_degraded_groups": 0,
                    "active_degraded_groups": 0,
                }

            summary = ffe.summary(feature_timestamps, now)

            # Recalculate penalties excluding groups with no active inputs.
            # Those groups represent undeployed bridges, not stale data.
            active_penalties = []
            critical_active_penalties = []
            active_degraded = 0
            advisory_degraded = 0
            for _gname, ginfo in summary.get("groups", {}).items():
                has_active = ginfo.get("active")
                if has_active is None:
                    has_active = ginfo.get("fresh", 0) + ginfo.get("stale", 0) > 0
                if has_active:
                    penalty = float(ginfo.get("confidence_penalty", 0.0) or 0.0)
                    active_penalties.append(penalty)
                    if ginfo.get("operational_critical", True):
                        critical_active_penalties.append(penalty)
                        if ginfo.get("degraded"):
                            active_degraded += 1
                    elif ginfo.get("degraded"):
                        advisory_degraded += 1
            if active_penalties:
                summary["overall_max_confidence_penalty"] = max(active_penalties)
            else:
                summary["overall_max_confidence_penalty"] = 0.0
            if critical_active_penalties:
                summary["max_confidence_penalty"] = max(critical_active_penalties)
            else:
                summary["max_confidence_penalty"] = 0.0
            summary["critical_max_confidence_penalty"] = summary["max_confidence_penalty"]
            summary["active_degraded_groups"] = active_degraded
            summary["advisory_degraded_groups"] = advisory_degraded

            return summary
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                raise
            tb = traceback.format_exc()
            self._log_event("feature_freshness_error", {"error": str(e), "traceback": tb})
            return {"error": str(e), "traceback": tb[:500], "max_confidence_penalty": 0}

    @staticmethod
    def _normalize_feature_freshness_result(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {
                "status": "unavailable",
                "max_confidence_penalty": 0.0,
                "active_degraded_groups": 0,
                "degraded_groups": 0,
            }

        normalized = dict(result)
        try:
            penalty = float(normalized.get("max_confidence_penalty", 0.0) or 0.0)
        except (TypeError, ValueError):
            penalty = 0.0

        def _coerce_group_count(value: Any) -> int:
            try:
                return max(int(value), 0)
            except (TypeError, ValueError):
                return 0

        normalized["max_confidence_penalty"] = max(0.0, min(penalty, 1.0))
        degraded_groups = _coerce_group_count(normalized.get("degraded_groups", 0))
        normalized["degraded_groups"] = degraded_groups
        normalized["active_degraded_groups"] = _coerce_group_count(
            normalized.get("active_degraded_groups", degraded_groups)
        )
        normalized.setdefault("status", "ok")
        return normalized

    def _stabilize_bridge_inputs_for_scorecard(self, bridge_results: Dict[str, Any]) -> Dict[str, Any]:
        """Re-apply safe bridge fallbacks before scorecard persistence."""
        if not isinstance(bridge_results, dict):
            return bridge_results

        freshness = bridge_results.setdefault("freshness", {})
        summary = bridge_results.setdefault("summary", {})
        bridge_errors = bridge_results.setdefault("bridge_errors", [])

        self._stabilize_options_greeks_for_scorecard(
            bridge_results=bridge_results,
            freshness=freshness,
            summary=summary,
            bridge_errors=bridge_errors,
        )
        return bridge_results

    def _stabilize_options_greeks_for_scorecard(
        self,
        bridge_results: Dict[str, Any],
        freshness: Dict[str, Any],
        summary: Dict[str, Any],
        bridge_errors: list[str],
    ) -> None:
        current = bridge_results.get("options_greeks")
        if isinstance(current, dict) and current.get("fresh"):
            return

        try:
            from src.bridges.options_greeks_bridge import OptionsGreeksBridge

            bridge = OptionsGreeksBridge(self.repo_root)
            stabilized = bridge.load_latest_cached_snapshot()
            if not (isinstance(stabilized, dict) and stabilized.get("fresh")):
                retry_snapshot = bridge.fetch()
                if isinstance(retry_snapshot, dict) and retry_snapshot.get("fresh"):
                    stabilized = retry_snapshot

            if isinstance(stabilized, dict) and stabilized.get("fresh"):
                bridge_results["options_greeks"] = stabilized
                freshness["options_greeks"] = True
                summary["put_call_ratio"] = stabilized.get("put_call_ratio", 0.0)
                summary["gamma_squeeze_risk"] = stabilized.get("gamma_squeeze_risk", "unknown")
                if not any("options_greeks_scorecard_stabilized" in str(item) for item in bridge_errors):
                    bridge_errors.append(
                        "options_greeks_scorecard_stabilized_from_cached_or_retry_snapshot"
                    )
                return
        except Exception as exc:
            bridge_errors.append(f"options_greeks_scorecard_stabilization: {exc}")

        if isinstance(current, dict):
            freshness["options_greeks"] = bool(current.get("fresh", False))
            summary["put_call_ratio"] = current.get("put_call_ratio", 0.0)
            summary["gamma_squeeze_risk"] = current.get("gamma_squeeze_risk", "unknown")

    def _check_blob_health(self) -> Optional[Dict[str, Any]]:
        """Check Blob persistence health for scorecard and alerting."""
        try:
            from src.core.blob_persistence_health import BlobPersistenceHealthChecker
            checker = BlobPersistenceHealthChecker(self.repo_root)
            health = checker.check()
            return health.to_dict()
        except Exception:
            return None

    def _compute_config_fingerprint(self) -> Dict[str, Any]:
        """Compute config fingerprint for scorecard replayability."""
        try:
            from src.core.config_fingerprint import compute_config_fingerprint
            return compute_config_fingerprint(config_dir=self.repo_root / "config")
        except Exception:
            return {"combined_fingerprint": "", "configs": {}}

    # --- Bridge polling ---
    def _poll_bridges(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {"freshness": {}, "summary": {}, "fallback_mode": False}
        bridge_errors = []

        # Aviation disruption bridge
        try:
            from src.bridges.aviation_disruption_bridge import AviationDisruptionBridge
            avb = AviationDisruptionBridge(self.repo_root)
            disruption_events = avb.poll()
            results["aviation_disruptions"] = disruption_events
            results["freshness"]["aviation_disruption"] = True  # polled successfully
            results["summary"]["aviation_disruption_count"] = len(disruption_events)
        except Exception as e:
            bridge_errors.append(f"aviation_disruption: {e}")
            results["freshness"]["aviation_disruption"] = False

        # Market microstructure bridge
        try:
            from src.bridges.market_microstructure_bridge import MarketMicrostructureBridge
            mmb = MarketMicrostructureBridge(self.repo_root)
            micro = mmb.build_snapshot_section()
            results["market_microstructure"] = micro.get("symbols", {})
            results["freshness"]["market_microstructure"] = micro.get("symbol_count", 0) > 0
            results["summary"]["microstructure_symbols"] = micro.get("symbol_count", 0)
        except Exception as e:
            bridge_errors.append(f"market_microstructure: {e}")
            results["freshness"]["market_microstructure"] = False

        # GDELT bridge (free, no API key)
        try:
            from src.bridges.gdelt_bridge import GDELTBridge
            gb = GDELTBridge(self.repo_root)
            gdelt_section = gb.build_snapshot_section()
            results["gdelt_events"] = gdelt_section.get("events", [])
            results["freshness"]["gdelt"] = True  # polled successfully
            results["summary"]["gdelt_event_count"] = gdelt_section.get("event_count", 0)
        except Exception as e:
            bridge_errors.append(f"gdelt: {e}")
            results["freshness"]["gdelt"] = False

        # Finnhub bridge
        try:
            from src.bridges.finnhub_bridge import FinnhubBridge
            fb = FinnhubBridge(self.repo_root)
            finnhub_packets = fb.poll()
            results["finnhub"] = finnhub_packets
            results["freshness"]["finnhub"] = len(finnhub_packets) > 0
            results["summary"]["finnhub_packet_count"] = len(finnhub_packets)
        except Exception as e:
            bridge_errors.append(f"finnhub: {e}")
            results["freshness"]["finnhub"] = False

        # FRED bridge
        try:
            from src.bridges.fred_bridge import FREDBridge
            frb = FREDBridge(self.repo_root)
            fred_packets = frb.poll()
            results["fred"] = fred_packets
            results["freshness"]["fred"] = len(fred_packets) > 0
        except Exception as e:
            bridge_errors.append(f"fred: {e}")
            results["freshness"]["fred"] = False

        # EIA bridge
        try:
            from src.bridges.eia_bridge import EIABridge
            eb = EIABridge(self.repo_root)
            eia_packets = eb.poll()
            results["eia"] = eia_packets
            results["freshness"]["eia"] = len(eia_packets) > 0
        except Exception as e:
            bridge_errors.append(f"eia: {e}")
            results["freshness"]["eia"] = False

        # GCP Consciousness bridge
        try:
            from src.bridges.gcp_consciousness_bridge import GCPConsciousnessBridge
            gcpb = GCPConsciousnessBridge(self.repo_root)
            gcp_data = gcpb.build_snapshot_section()
            results["gcp_consciousness"] = gcp_data
            results["freshness"]["gcp_consciousness"] = gcp_data.get("fresh", False)
            results["summary"]["gcp_coherence_level"] = gcp_data.get("coherence_level", "unknown")
        except Exception as e:
            bridge_errors.append(f"gcp_consciousness: {e}")
            results["freshness"]["gcp_consciousness"] = False

        # Narrative Velocity bridge
        try:
            from src.bridges.narrative_velocity_bridge import NarrativeVelocityBridge
            nvb = NarrativeVelocityBridge(self.repo_root)
            finnhub_headlines = results.get("finnhub", [])
            narrative_data = nvb.poll(finnhub_headlines=finnhub_headlines)
            results["narrative_velocity"] = narrative_data
            results["freshness"]["narrative_velocity"] = narrative_data.get("fresh", False)
            results["summary"]["narrative_velocity_score"] = narrative_data.get("velocity_score", 0)
        except Exception as e:
            bridge_errors.append(f"narrative_velocity: {e}")
            results["freshness"]["narrative_velocity"] = False

        # Options Greeks bridge
        try:
            from src.bridges.options_greeks_bridge import OptionsGreeksBridge
            ogb = OptionsGreeksBridge(self.repo_root)
            greeks_data = ogb.fetch()
            if (not greeks_data.get("fresh")):
                cached_snapshot = ogb.load_latest_cached_snapshot()
                if cached_snapshot and cached_snapshot.get("fresh"):
                    greeks_data = cached_snapshot
            if not isinstance(greeks_data, dict):
                raise TypeError("options_greeks returned non-dict payload")
            results["options_greeks"] = greeks_data
            results["freshness"]["options_greeks"] = greeks_data.get("fresh", False)
            results["summary"]["put_call_ratio"] = greeks_data.get("put_call_ratio", 0)
            results["summary"]["gamma_squeeze_risk"] = greeks_data.get("gamma_squeeze_risk", "low")
        except Exception as e:
            try:
                cached_snapshot = OptionsGreeksBridge(self.repo_root).load_latest_cached_snapshot()
            except Exception:
                cached_snapshot = None
            if cached_snapshot and cached_snapshot.get("fresh"):
                results["options_greeks"] = cached_snapshot
                results["freshness"]["options_greeks"] = True
                results["summary"]["put_call_ratio"] = cached_snapshot.get("put_call_ratio", 0)
                results["summary"]["gamma_squeeze_risk"] = cached_snapshot.get("gamma_squeeze_risk", "low")
                bridge_errors.append(f"options_greeks_live_failed_using_cache: {e}")
            else:
                bridge_errors.append(f"options_greeks: {e}")
                results["freshness"]["options_greeks"] = False
                results["summary"]["put_call_ratio"] = 0.0
                results["summary"]["gamma_squeeze_risk"] = "unknown"

        # Politician Alpha bridge
        try:
            from src.bridges.politician_alpha_bridge import PoliticianAlphaBridge
            pab = PoliticianAlphaBridge(self.repo_root)
            pol_data = pab.poll()
            results["politician_alpha"] = pol_data
            results["freshness"]["politician_alpha"] = pol_data.get("fresh", False)
        except Exception as e:
            bridge_errors.append(f"politician_alpha: {e}")
            results["freshness"]["politician_alpha"] = False

        # Fed Board bridge (free, no API key)
        try:
            from src.bridges.fed_board_bridge import FedBoardBridge
            fbb = FedBoardBridge(self.repo_root)
            fed_data = fbb.poll()
            results["fed_board"] = fed_data
            results["freshness"]["fed_board"] = fed_data.get("fresh", False) if isinstance(fed_data, dict) else bool(fed_data)
        except Exception as e:
            bridge_errors.append(f"fed_board: {e}")
            results["freshness"]["fed_board"] = False

        # Treasury OFAC bridge (free, no API key)
        try:
            from src.bridges.treasury_ofac_bridge import TreasuryOFACBridge
            tob = TreasuryOFACBridge(self.repo_root)
            ofac_data = tob.poll()
            results["treasury_ofac"] = ofac_data
            results["freshness"]["treasury_ofac"] = ofac_data.get("fresh", False) if isinstance(ofac_data, dict) else bool(ofac_data)
        except Exception as e:
            bridge_errors.append(f"treasury_ofac: {e}")
            results["freshness"]["treasury_ofac"] = False

        # White House Policy bridge (free, no API key)
        try:
            from src.bridges.whitehouse_policy_bridge import WhiteHousePolicyBridge
            whb = WhiteHousePolicyBridge(self.repo_root)
            wh_data = whb.poll()
            results["whitehouse_policy"] = wh_data
            results["freshness"]["whitehouse_policy"] = wh_data.get("fresh", False) if isinstance(wh_data, dict) else bool(wh_data)
        except Exception as e:
            bridge_errors.append(f"whitehouse_policy: {e}")
            results["freshness"]["whitehouse_policy"] = False

        # BLS Release bridge (free, optional API key)
        try:
            from src.bridges.bls_release_bridge import BLSReleaseBridge
            blsb = BLSReleaseBridge(self.repo_root)
            bls_data = blsb.poll()
            results["bls_releases"] = bls_data
            results["freshness"]["bls_releases"] = bls_data.get("fresh", False) if isinstance(bls_data, dict) else bool(bls_data)
        except Exception as e:
            bridge_errors.append(f"bls_releases: {e}")
            results["freshness"]["bls_releases"] = False

        # Exa AI Search bridge (real-time news & disruption search)
        try:
            from src.bridges.exa_search_bridge import ExaSearchBridge
            exb = ExaSearchBridge(self.repo_root)
            exa_section = exb.build_snapshot_section()
            results["exa_search"] = exa_section.get("packets", [])
            results["freshness"]["exa_search"] = exa_section.get("fresh", False)
            results["summary"]["exa_packet_count"] = exa_section.get("packet_count", 0)
            results["summary"]["exa_high_severity"] = exa_section.get("high_severity_count", 0)
        except Exception as e:
            bridge_errors.append(f"exa_search: {e}")
            results["freshness"]["exa_search"] = False

        results["bridge_errors"] = bridge_errors
        fresh_count = sum(1 for v in results["freshness"].values() if v)
        total_bridges = len(results["freshness"])
        results["fallback_mode"] = fresh_count < (total_bridges * 0.5)

        return results

    # --- Snapshot assembly ---
    def _build_snapshot(self, bridge_results: Dict[str, Any], kill_switch: Dict, manual_veto: Dict) -> Dict[str, Any]:
        return {
            "timestamp_utc": iso_now(),
            "market_microstructure": bridge_results.get("market_microstructure", {}),
            "aviation_disruptions": bridge_results.get("aviation_disruptions", []),
            "gdelt_events": bridge_results.get("gdelt_events", []),
            "finnhub": bridge_results.get("finnhub", []),
            "fred": bridge_results.get("fred", []),
            "eia": bridge_results.get("eia", []),
            "gcp_consciousness": bridge_results.get("gcp_consciousness", {}),
            "narrative_velocity": bridge_results.get("narrative_velocity", {}),
            "options_greeks": bridge_results.get("options_greeks", {}),
            "politician_alpha": bridge_results.get("politician_alpha", {}),
            "fed_board": bridge_results.get("fed_board", {}),
            "treasury_ofac": bridge_results.get("treasury_ofac", {}),
            "whitehouse_policy": bridge_results.get("whitehouse_policy", {}),
            "bls_releases": bridge_results.get("bls_releases", {}),
            "exa_search": bridge_results.get("exa_search", []),
            "data_freshness": bridge_results.get("freshness", {}),
            "fallback_mode": bridge_results.get("fallback_mode", False),
            "controls": {
                "kill_switch": kill_switch.get("active", False),
                "manual_veto": manual_veto.get("active", False),
            },
            "runtime_flags": {},
            "portfolio": {},  # populated when paper/live trading is active
        }

    # --- Regime scoring ---
    def _score_regime(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from src.scoring.regime_shift import RegimeShiftScorer
            scorer = RegimeShiftScorer(self.thresholds)
            return scorer.score(snapshot)
        except Exception as e:
            # Fallback: basic heuristic if scorer fails to load
            self._log_event("regime_scorer_fallback", {"error": str(e)})
            disruptions = snapshot.get("aviation_disruptions", [])
            high_severity = sum(1 for d in disruptions if d.get("severity") == "high")
            medium_severity = sum(1 for d in disruptions if d.get("severity") == "medium")

            base_p = 0.15
            base_p += high_severity * 0.08
            base_p += medium_severity * 0.03
            base_p = min(base_p, 0.95)

            return {
                "regime_shift_probability": base_p,
                "component_scores": {
                    "aviation_disruption": high_severity * 0.15 + medium_severity * 0.05,
                    "data_freshness": 0.1 if not snapshot.get("fallback_mode") else 0.3,
                },
                "confidence": 0.5 if disruptions else 0.3,
                "evidence": [d.get("title", "") for d in disruptions[:5]],
            }

    # --- Time window ---
    def _classify_time_window(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from src.alpha.time_window_policy import TimeWindowPolicyEngine
            engine = TimeWindowPolicyEngine(self.repo_root)
            return engine.classify(
                controls=snapshot.get("controls", {}),
                data_quality={
                    "quorum_pass": not snapshot.get("fallback_mode", False),
                    "fallback_mode": snapshot.get("fallback_mode", False),
                },
            )
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._log_event("time_window_error", {"error": str(e), "traceback": tb})
            return {"current_window": "unknown", "shadow_execution_window_blocked": True, "error": str(e), "traceback": tb[:500]}

    # --- Mode resolution ---
    def _resolve_mode_with_trace(self, regime_score: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve mode and return full decision trace for replay-grade scorecards."""
        p = regime_score.get("regime_shift_probability", 0.0)
        thresholds = self.thresholds.get("mode_thresholds", {})
        pre_mode = self.current_mode

        if snapshot.get("controls", {}).get("manual_veto"):
            trace = {
                "pre_transition_mode": pre_mode,
                "proposed_mode": "MANUAL_REVIEW",
                "final_mode": "MANUAL_REVIEW",
                "reason": "manual_veto_active",
                "regime_shift_probability": p,
                "policy_evaluation": None,
                "quorum_evaluation": None,
                "blocked": False,
                "blocking_reason": None,
                "cycle": self.cycle_count,
            }
            self._log_event("mode_decision_trace", trace)
            return trace

        crisis_threshold = float(thresholds.get("crisis", 0.85))
        elevated_threshold = float(thresholds.get("elevated", 0.55))
        hysteresis = 0.05

        if self.current_mode == "CRISIS":
            proposed = "ELEVATED" if p < crisis_threshold - hysteresis and p >= elevated_threshold else (
                "NORMAL" if p < crisis_threshold - hysteresis else "CRISIS")
        elif self.current_mode == "ELEVATED":
            if p >= crisis_threshold:
                proposed = "CRISIS"
            elif p < elevated_threshold - hysteresis:
                proposed = "NORMAL"
            else:
                proposed = "ELEVATED"
        else:
            if p >= crisis_threshold:
                proposed = "CRISIS"
            elif p >= elevated_threshold:
                proposed = "ELEVATED"
            else:
                proposed = "NORMAL"

        policy_result = None
        quorum_result = None
        final_mode = proposed
        is_escalation = MODES.index(proposed) > MODES.index(pre_mode) if proposed in MODES and pre_mode in MODES else False

        if is_escalation:
            try:
                from src.core.policy_engine import PolicyEngine
                pe = PolicyEngine(config_dir=self.repo_root / "config")
                policy_result = pe.evaluate({
                    "action": "mode_transition",
                    "from_mode": pre_mode,
                    "to_mode": proposed,
                    "regime_shift_probability": p,
                    "cycle": self.cycle_count,
                })
                if isinstance(policy_result, dict) and not policy_result.get("allowed", True):
                    final_mode = pre_mode
            except Exception as e:
                policy_result = {"error": str(e), "allowed": True}

            try:
                from src.core.source_quorum_engine import SourceQuorumEngine
                sqe = SourceQuorumEngine(config_dir=self.repo_root / "config")
                freshness_map = snapshot.get("bridge_freshness", {})
                source_timestamps: Dict[str, str] = {}
                for src_name, val in freshness_map.items():
                    if isinstance(val, str):
                        source_timestamps[src_name] = val
                    elif val is True:
                        source_timestamps[src_name] = iso_now()
                trust_cfg = load_yaml_safe(self.repo_root / "config" / "data_trust_hierarchy.yaml")
                quorum_result = sqe.check_execution_quorum(source_timestamps, trust_cfg)
                if isinstance(quorum_result, dict) and not quorum_result.get("quorum_met", True):
                    final_mode = pre_mode
            except Exception as e:
                quorum_result = {"error": str(e), "quorum_met": True}

        trace = {
            "pre_transition_mode": pre_mode,
            "proposed_mode": proposed,
            "final_mode": final_mode,
            "reason": "threshold_based",
            "regime_shift_probability": p,
            "thresholds_used": {"crisis": crisis_threshold, "elevated": elevated_threshold, "hysteresis": hysteresis},
            "policy_evaluation": policy_result,
            "quorum_evaluation": quorum_result,
            "blocked": proposed != final_mode,
            "blocking_reason": None,
            "cycle": self.cycle_count,
        }
        if trace["blocked"]:
            if policy_result and not policy_result.get("allowed", True):
                trace["blocking_reason"] = "policy_engine_denied"
            elif quorum_result and not quorum_result.get("quorum_met", True):
                trace["blocking_reason"] = "quorum_not_met"
        self._log_event("mode_decision_trace", trace)
        return trace

    def _resolve_mode(self, regime_score: Dict[str, Any], snapshot: Dict[str, Any]) -> str:
        p = regime_score.get("regime_shift_probability", 0.0)
        thresholds = self.thresholds.get("mode_thresholds", {})
        pre_mode = self.current_mode

        if snapshot.get("controls", {}).get("manual_veto"):
            self._log_mode_decision(pre_mode, "MANUAL_REVIEW", "manual_veto_active",
                                    regime_prob=p, policy_result=None, quorum_result=None)
            return "MANUAL_REVIEW"

        crisis_threshold = float(thresholds.get("crisis", 0.85))
        elevated_threshold = float(thresholds.get("elevated", 0.55))

        # Hysteresis: require higher threshold to escalate, lower to de-escalate
        hysteresis = 0.05
        if self.current_mode == "CRISIS":
            if p < crisis_threshold - hysteresis:
                proposed = "ELEVATED" if p >= elevated_threshold else "NORMAL"
            else:
                proposed = "CRISIS"
        elif self.current_mode == "ELEVATED":
            if p >= crisis_threshold:
                proposed = "CRISIS"
            elif p < elevated_threshold - hysteresis:
                proposed = "NORMAL"
            else:
                proposed = "ELEVATED"
        else:  # NORMAL
            if p >= crisis_threshold:
                proposed = "CRISIS"
            elif p >= elevated_threshold:
                proposed = "ELEVATED"
            else:
                proposed = "NORMAL"

        # V4 governance: consult PolicyEngine and SourceQuorumEngine on escalations
        policy_result = None
        quorum_result = None
        final_mode = proposed

        is_escalation = MODES.index(proposed) > MODES.index(pre_mode) if proposed in MODES and pre_mode in MODES else False

        if is_escalation:
            # Policy engine check
            try:
                from src.core.policy_engine import PolicyEngine
                pe = PolicyEngine(config_dir=self.repo_root / "config")
                policy_result = pe.evaluate({
                    "action": "mode_transition",
                    "from_mode": pre_mode,
                    "to_mode": proposed,
                    "regime_shift_probability": p,
                    "cycle": self.cycle_count,
                })
                if isinstance(policy_result, dict) and not policy_result.get("allowed", True):
                    final_mode = pre_mode  # Policy blocked escalation
            except Exception as e:
                policy_result = {"error": str(e), "allowed": True}

            # Source quorum check — require quorum before escalation
            try:
                from src.core.source_quorum_engine import SourceQuorumEngine
                sqe = SourceQuorumEngine(config_dir=self.repo_root / "config")
                freshness_map = snapshot.get("bridge_freshness", {})
                source_timestamps: Dict[str, str] = {}
                for src_name, val in freshness_map.items():
                    if isinstance(val, str):
                        source_timestamps[src_name] = val
                    elif val is True:
                        source_timestamps[src_name] = iso_now()
                trust_cfg = load_yaml_safe(self.repo_root / "config" / "data_trust_hierarchy.yaml")
                quorum_result = sqe.check_execution_quorum(source_timestamps, trust_cfg)
                if isinstance(quorum_result, dict) and not quorum_result.get("quorum_met", True):
                    final_mode = pre_mode  # Quorum not met, block escalation
            except Exception as e:
                quorum_result = {"error": str(e), "quorum_met": True}

        self._log_mode_decision(pre_mode, final_mode, "threshold_based",
                                regime_prob=p, policy_result=policy_result,
                                quorum_result=quorum_result, proposed=proposed)
        return final_mode

    def _log_mode_decision(
        self,
        pre_mode: str,
        final_mode: str,
        reason: str,
        regime_prob: float = 0.0,
        policy_result: Optional[Dict] = None,
        quorum_result: Optional[Dict] = None,
        proposed: Optional[str] = None,
    ) -> None:
        """Log a structured mode decision trace for auditability."""
        trace = {
            "pre_transition_mode": pre_mode,
            "proposed_mode": proposed or final_mode,
            "final_mode": final_mode,
            "reason": reason,
            "regime_shift_probability": regime_prob,
            "policy_evaluation": policy_result,
            "quorum_evaluation": quorum_result,
            "blocked": proposed is not None and final_mode != proposed,
            "blocking_reason": None,
            "cycle": self.cycle_count,
        }
        if trace["blocked"]:
            if policy_result and not policy_result.get("allowed", True):
                trace["blocking_reason"] = "policy_engine_denied"
            elif quorum_result and not quorum_result.get("quorum_met", True):
                trace["blocking_reason"] = "quorum_not_met"
        self._log_event("mode_decision_trace", trace)

    # --- Shadow eligibility ---
    def _shadow_eligible(self, snapshot: Dict[str, Any], time_window: Dict[str, Any]) -> bool:
        if self.current_mode in ("CRISIS", "MANUAL_REVIEW"):
            return False
        if snapshot.get("controls", {}).get("kill_switch"):
            return False
        if snapshot.get("controls", {}).get("manual_veto"):
            return False
        if time_window.get("shadow_execution_window_blocked"):
            return False
        return True

    # --- Shadow Execution ---
    def _run_shadow_execution(self, scorecard: Dict[str, Any], bridge_results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Run trade analysis → split by strategy → package → shadow order routing."""
        try:
            from src.alpha.trade_analysis_engine import TradeAnalysisEngine
            from src.execution.trade_idea_packager import TradeIdeaPackager
            from src.execution.shadow_order_router import ShadowOrderRouter

            # Adaptive feedback loop: learn from past trades, adjust signal weights
            try:
                from src.execution.adaptive_feedback_loop import AdaptiveFeedbackLoop
                feedback = AdaptiveFeedbackLoop(self.repo_root)
                feedback_result = feedback.analyze_and_adjust()
                learned_adjustments = feedback.get_signal_adjustments()
                # Store feedback state in bridge_results for packager to use
                bridge_results["_feedback_adjustments"] = learned_adjustments
                bridge_results["_feedback_strategy_confidence_adjustments"] = feedback.get_strategy_confidence_adjustments()
                bridge_results["_feedback_strategy_adjustments"] = feedback.state.get("strategy_adjustments", {})
                bridge_results["_feedback_daily_target"] = feedback_result.get("daily_target", {})
                if feedback_result.get("status") == "active":
                    self._log_event("feedback_loop_active", {
                        "trades_analyzed": feedback_result.get("trades_analyzed", 0),
                        "adjustments_count": len(learned_adjustments),
                        "daily_target": feedback_result.get("daily_target", {}),
                    })
            except Exception as e:
                print(f"[{iso_now()}] Feedback loop error (non-fatal): {e}", file=sys.stderr)

            # Get previous mode for transition detection
            scorecards_dir = self.repo_root / "logs" / "scorecards"
            prev_files = sorted(scorecards_dir.glob("scorecard_*.json"), reverse=True)
            prev_mode = None
            if len(prev_files) > 1:
                try:
                    prev_sc = json.loads(prev_files[1].read_text(encoding="utf-8"))
                    prev_mode = prev_sc.get("mode")
                except Exception:
                    pass

            # Generate trade analysis
            micro = bridge_results.get("market_microstructure", {})
            engine = TradeAnalysisEngine(self.repo_root)
            analysis = engine.analyze(scorecard, previous_mode=prev_mode, microstructure=micro)
            idea_count = len(analysis.get("trade_ideas") or [])

            if analysis.get("error"):
                self._log_shadow_diagnostic(
                    "shadow_execution_skipped",
                    {
                        "reason": "analysis_error",
                        "analysis_error": analysis.get("error"),
                        "idea_count": idea_count,
                    },
                    f"Shadow execution skipped: analysis_error={analysis.get('error')}",
                )
                return None

            if not analysis.get("trade_ideas"):
                self._log_shadow_diagnostic(
                    "shadow_execution_skipped",
                    {
                        "reason": "no_trade_ideas",
                        "idea_count": idea_count,
                    },
                    "Shadow execution skipped: no trade ideas from analysis",
                )
                return None

            self._log_shadow_diagnostic(
                "shadow_execution_analysis_ready",
                {
                    "idea_count": idea_count,
                },
                f"Shadow execution analysis ready: idea_count={idea_count}",
            )

            # --- Dual-strategy routing ---
            if self.strategy_manager:
                return self._route_dual_strategy(
                    analysis, scorecard, micro, engine,
                    politician_alpha=bridge_results.get("politician_alpha"),
                    bridge_signals=bridge_results,
                )

            # Fallback: original single-strategy pipeline
            packager = TradeIdeaPackager()
            package = packager.build_package(
                trade_analysis=analysis,
                scorecard=scorecard,
                microstructure=micro,
                max_ideas=10,
                politician_alpha=bridge_results.get("politician_alpha"),
                bridge_signals=bridge_results,
            )

            if not package.get("candidates") or package.get("global_blocks"):
                return None

            router = ShadowOrderRouter(self.repo_root)
            result = router.route_package(
                package=package,
                max_orders=999,
                min_confidence=0.15,
            )

            return result

        except Exception as e:
            self._log_event("shadow_execution_error", {"error": str(e), "cycle": self.cycle_count})
            print(f"[{iso_now()}] Shadow execution error: {e}", file=sys.stderr)
            return None

    @staticmethod
    def _resolve_alpaca_credentials(strategy_name: str) -> Optional[Dict[str, str]]:
        """Resolve Alpaca credentials for a given strategy.

        Env var lookup order:
          day_trade   -> ALPACA_API_KEY_DAYTRADE / ALPACA_SECRET_KEY_DAYTRADE
          medium_long -> ALPACA_API_KEY_MEDLONG  / ALPACA_SECRET_KEY_MEDLONG
        Falls back to the generic ALPACA_API_KEY / ALPACA_SECRET_KEY if the
        strategy-specific vars are not set.  Returns None when no override is
        needed (adapter will use its own env-var defaults).
        """
        if strategy_name == "day_trade":
            api_key = os.getenv("ALPACA_API_KEY_DAYTRADE")
            api_secret = os.getenv("ALPACA_SECRET_KEY_DAYTRADE")
        elif strategy_name == "medium_long":
            api_key = os.getenv("ALPACA_API_KEY_MEDLONG")
            api_secret = os.getenv("ALPACA_SECRET_KEY_MEDLONG")
        else:
            return None

        if api_key and api_secret:
            return {"api_key": api_key, "api_secret": api_secret}

        # Fallback: let adapter use default ALPACA_API_KEY / ALPACA_SECRET_KEY
        return None

    def _route_dual_strategy(
        self,
        analysis: Dict[str, Any],
        scorecard: Dict[str, Any],
        micro: Dict[str, Any],
        engine,
        politician_alpha: Optional[Dict[str, Any]] = None,
        bridge_signals: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Route trade ideas through day_trade and medium_long strategy pipelines."""
        from src.execution.trade_idea_packager import TradeIdeaPackager
        from src.execution.shadow_order_router import ShadowOrderRouter

        sm = self.strategy_manager
        split = sm.split_ideas_by_strategy(analysis["trade_ideas"])

        combined_result: Dict[str, Any] = {
            "submitted_open_or_ack_count": 0,
            "selected_candidates": [],
            "strategy_results": {},
        }

        for strategy_name, ideas in split.items():
            initial_idea_count = len(ideas or [])
            # Always process medium_long even if initial split has 0 ideas,
            # because re-analysis generates its own ideas from MEDIUM_LONG_PLAYBOOK
            if not ideas and strategy_name != "medium_long":
                self._log_shadow_diagnostic(
                    "shadow_strategy_skipped",
                    {
                        "strategy": strategy_name,
                        "reason": "empty_split_bucket",
                        "initial_idea_count": initial_idea_count,
                    },
                    f"Strategy {strategy_name} skipped: empty split bucket",
                )
                continue

            strategy_cfg = dict(sm.get_strategy_config(strategy_name) or {})
            strategy_cfg.setdefault("name", strategy_name)
            max_ideas = strategy_cfg.get("max_ideas_per_cycle", 10)
            max_orders = strategy_cfg.get("max_orders_per_cycle", 8)
            tif = strategy_cfg.get("time_in_force", "day")
            exec_mode = sm.get_execution_mode(strategy_name)
            strategy_label = "Day Trade" if strategy_name == "day_trade" else "Medium/Long Hold"

            # Build strategy-specific analysis
            strategy_analysis = dict(analysis)
            strategy_analysis["trade_ideas"] = ideas

            # For medium_long, re-analyze with strategy_type hint if engine supports it
            if strategy_name == "medium_long":
                try:
                    ml_analysis = engine.analyze(
                        scorecard,
                        previous_mode=None,
                        microstructure=micro,
                        strategy_type="medium_long",
                    )
                    if ml_analysis.get("trade_ideas"):
                        strategy_analysis["trade_ideas"] = ml_analysis["trade_ideas"]
                except TypeError:
                    # engine.analyze doesn't support strategy_type kwarg yet
                    pass

            strategy_idea_count = len(strategy_analysis.get("trade_ideas") or [])

            # Strategy-scoped duplicate filter:
            # avoid cross-account suppression by checking open symbols per strategy account.
            existing_symbols = self._get_open_order_symbols(strategy_name=strategy_name)
            before_filter_count = len(strategy_analysis.get("trade_ideas") or [])
            if existing_symbols:
                strategy_analysis["trade_ideas"] = [
                    idea for idea in strategy_analysis.get("trade_ideas", [])
                    if idea.get("symbol") not in existing_symbols
                ]
            after_filter_count = len(strategy_analysis.get("trade_ideas") or [])
            if not strategy_analysis.get("trade_ideas"):
                sm.log_strategy_event("strategy_skipped_existing_symbols", {
                    "strategy": strategy_name,
                    "open_symbol_count": len(existing_symbols),
                })
                self._log_shadow_diagnostic(
                    "shadow_strategy_skipped",
                    {
                        "strategy": strategy_name,
                        "reason": "existing_symbols_filter",
                        "initial_split_idea_count": initial_idea_count,
                        "strategy_idea_count": strategy_idea_count,
                        "before_filter_count": before_filter_count,
                        "after_filter_count": after_filter_count,
                        "open_symbol_count": len(existing_symbols),
                        "open_symbol_sample": sorted(existing_symbols)[:12],
                    },
                    (
                        f"Strategy {strategy_name} skipped after existing-symbol filter: "
                        f"{before_filter_count}->{after_filter_count}, "
                        f"open_symbol_count={len(existing_symbols)}"
                    ),
                )
                continue

            self._log_shadow_diagnostic(
                "shadow_strategy_analysis_ready",
                {
                    "strategy": strategy_name,
                    "execution_mode": exec_mode,
                    "initial_split_idea_count": initial_idea_count,
                    "strategy_idea_count": strategy_idea_count,
                    "before_filter_count": before_filter_count,
                    "after_filter_count": after_filter_count,
                    "open_symbol_count": len(existing_symbols),
                },
                (
                    f"Strategy {strategy_name} analysis ready: "
                    f"ideas={strategy_idea_count}, after_filter={after_filter_count}, "
                    f"exec_mode={exec_mode}"
                ),
            )

            # Package ideas
            packager = TradeIdeaPackager()
            package = packager.build_package(
                trade_analysis=strategy_analysis,
                scorecard=scorecard,
                microstructure=micro,
                max_ideas=max_ideas,
                politician_alpha=politician_alpha,
                bridge_signals=bridge_signals,
            )
            package["strategy_name"] = strategy_name

            if not package.get("candidates") or package.get("global_blocks"):
                blocked_candidates = package.get("blocked_candidates") or []
                blocked_reason_sample = []
                seen_reasons = set()
                for item in blocked_candidates:
                    reason = item.get("reason")
                    if not reason or reason in seen_reasons:
                        continue
                    seen_reasons.add(reason)
                    blocked_reason_sample.append(reason)
                    if len(blocked_reason_sample) >= 5:
                        break
                self._log_shadow_diagnostic(
                    "shadow_strategy_package_skipped",
                    {
                        "strategy": strategy_name,
                        "execution_mode": exec_mode,
                        "candidate_count": len(package.get("candidates") or []),
                        "blocked_candidate_count": len(blocked_candidates),
                        "blocked_reason_sample": blocked_reason_sample,
                        "global_blocks": package.get("global_blocks") or [],
                        "after_filter_count": after_filter_count,
                    },
                    (
                        f"Strategy {strategy_name} package skipped: "
                        f"candidates={len(package.get('candidates') or [])}, "
                        f"blocked={len(blocked_candidates)}, "
                        f"global_blocks={len(package.get('global_blocks') or [])}"
                    ),
                )
                continue

            blocked_candidates = package.get("blocked_candidates") or []
            self._log_shadow_diagnostic(
                "shadow_strategy_package_ready",
                {
                    "strategy": strategy_name,
                    "execution_mode": exec_mode,
                    "candidate_count": len(package.get("candidates") or []),
                    "blocked_candidate_count": len(blocked_candidates),
                    "global_blocks": package.get("global_blocks") or [],
                    "time_in_force": tif,
                    "max_orders": max_orders,
                    "max_ideas": max_ideas,
                },
                (
                    f"Strategy {strategy_name} package ready: "
                    f"candidates={len(package.get('candidates') or [])}, exec_mode={exec_mode}"
                ),
            )

            # Apply time_in_force override for medium_long
            if tif == "gtc":
                for cand in package["candidates"]:
                    cand["time_in_force"] = "gtc"

            # Build order summary for notifications
            order_summary = sm.build_order_summary(
                package["candidates"], strategy_name, scorecard
            )
            formatted_msg = sm.format_telegram_order_alert(order_summary)

            if exec_mode == "auto":
                # Auto mode: submit orders and send instant notification
                # Use strategy-specific Alpaca credentials when available
                alpaca_creds = self._resolve_alpaca_credentials(strategy_name)
                router = ShadowOrderRouter(
                    self.repo_root,
                    alpaca_credentials=alpaca_creds,
                )
                result = router.route_package(
                    package=package,
                    max_orders=max_orders,
                    min_confidence=0.15,
                    strategy_config=strategy_cfg,
                )

                if result:
                    submitted = result.get("submitted_open_or_ack_count", 0)
                    combined_result["submitted_open_or_ack_count"] += submitted
                    combined_result["selected_candidates"].extend(
                        result.get("selected_candidates", [])
                    )
                    combined_result["strategy_results"][strategy_name] = result

                    # Instant Telegram alert for auto-submitted orders
                    if submitted > 0 and self.notifier:
                        try:
                            self.notifier.notify_new_orders(order_summary, formatted_msg)
                        except Exception:
                            pass

                    sm.log_strategy_event("auto_orders_submitted", {
                        "strategy": strategy_name,
                        "orders_submitted": submitted,
                        "candidates": len(result.get("selected_candidates", [])),
                    })
                else:
                    self._log_shadow_diagnostic(
                        "shadow_strategy_router_no_result",
                        {
                            "strategy": strategy_name,
                            "execution_mode": exec_mode,
                            "candidate_count": len(package.get("candidates") or []),
                        },
                        f"Strategy {strategy_name} router returned no result",
                    )
            else:
                # Manual mode: do NOT submit orders, send for approval
                if self.notifier:
                    try:
                        self.notifier.send_manual_mode_summary(order_summary, formatted_msg)
                    except Exception:
                        pass

                sm.log_strategy_event("manual_approval_requested", {
                    "strategy": strategy_name,
                    "order_count": len(package["candidates"]),
                })
                self._log_shadow_diagnostic(
                    "shadow_strategy_manual_review",
                    {
                        "strategy": strategy_name,
                        "candidate_count": len(package.get("candidates") or []),
                    },
                    f"Strategy {strategy_name} awaiting manual approval: candidates={len(package.get('candidates') or [])}",
                )

                combined_result["strategy_results"][strategy_name] = {
                    "mode": "manual",
                    "pending_approval": len(package["candidates"]),
                }

        if combined_result["submitted_open_or_ack_count"] > 0 or combined_result["strategy_results"]:
            return combined_result
        return None

    def _fetch_all_positions(self):
        """Fetch all open positions for the hourly updater."""
        from src.execution.position_manager import PositionManager
        pm = PositionManager(self.repo_root)
        return pm._get_open_positions()

    def _get_open_order_symbols(self, strategy_name: Optional[str] = None) -> set:
        """Get symbols with existing open orders/positions for one strategy/account."""
        symbols = set()
        try:
            from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter
            creds = self._resolve_alpaca_credentials(strategy_name) if strategy_name else None
            if creds:
                adapter = AlpacaPaperAdapter(
                    api_key=creds.get("api_key"),
                    api_secret=creds.get("api_secret"),
                )
            else:
                adapter = AlpacaPaperAdapter()
            # Check open orders
            for order in adapter.list_open_orders():
                sym = order.get("symbol")
                if sym:
                    symbols.add(sym)
            # Check positions
            for pos in adapter.list_positions():
                sym = pos.get("symbol")
                if sym:
                    symbols.add(sym)
        except Exception:
            pass
        return symbols

    # --- Alerting ---
    def _send_position_alert(self, pm_result: Dict[str, Any]):
        """Send Telegram alert when positions are closed by the position manager."""
        actions = pm_result.get("actions_taken", 0)
        profits = pm_result.get("profits_taken", 0)
        stops = pm_result.get("stops_hit", 0)
        eod = pm_result.get("eod_flattened", 0)

        title = f"Position Manager: {actions} position(s) closed"

        body = f"Profits taken: {profits} | Stops hit: {stops} | EOD flattened: {eod}\n"

        for detail in pm_result.get("close_details", []):
            symbol = detail.get("symbol", "?")
            reason = detail.get("reason", "?")
            plpc = detail.get("unrealized_plpc", 0)
            pl = detail.get("unrealized_pl", 0)
            body += f"  {symbol}: {reason} (P&L: {plpc:+.2f}%, ${pl:+,.2f})\n"

        if pm_result.get("errors"):
            body += f"Errors: {len(pm_result['errors'])}\n"

        self.alerter._dispatch(title, body, level="info", extra={
            "event": "position_management",
            "actions": actions,
            "profits_taken": profits,
            "stops_hit": stops,
            "eod_flattened": eod,
        })

    def _load_alerter(self):
        try:
            from src.monitoring.alerting import AlertDispatcher
            return AlertDispatcher(self.repo_root)
        except Exception:
            return None

    def _send_v6_telegram_digest(self, scorecard: Dict[str, Any]):
        """Send consolidated V6 module summary via Telegram."""
        if not self.alerter:
            return
        try:
            from src.monitoring.telegram_topic_notifier import TelegramTopicNotifier

            lines = []
            exposure = scorecard.get("v6_exposure_summary", {}) or {}
            edge_summary = str(scorecard.get("v6_edge_summary") or "").strip()
            strategy_summary = scorecard.get("v6_strategy_summary", {}) or {}
            strategy_ideas = scorecard.get("v6_strategy_ideas", []) or []
            scanner_discoveries = scorecard.get("v6_scanner_discoveries", []) or []
            deescalation = scorecard.get("v6_deescalation", {}) or {}

            if exposure:
                exposure_line = (
                    f"\U0001f4b0 Equity: ${float(exposure.get('combined_equity', 0.0)):,.0f} | "
                    f"Gross: {float(exposure.get('gross_exposure_pct', 0.0)):.0%} | "
                    f"Net: {float(exposure.get('net_exposure_pct', 0.0)):+.0%} | "
                    f"Oil\u0394: ${float(exposure.get('oil_delta', 0.0)):+,.0f}/pt"
                )
                pending_close_orders = int(exposure.get("pending_close_orders", 0) or 0)
                if pending_close_orders > 0:
                    exposure_line += (
                        f" | Raw: {float(exposure.get('raw_gross_exposure_pct', 0.0)):.0%}"
                        f" | Pending closes: {pending_close_orders}"
                    )
                lines.append(exposure_line)

            if edge_summary and "no actionable signals" not in edge_summary.lower():
                lines.append(edge_summary)

            if strategy_summary:
                lines.append(
                    f"\U0001f4ca Strategies: {int(strategy_summary.get('active_count', 0))}/15 firing | "
                    f"Ideas: {int(strategy_summary.get('idea_count', len(strategy_ideas)))}"
                )

            scanner_count = len(scanner_discoveries)
            ranked_discoveries = sorted(
                [d for d in scanner_discoveries if isinstance(d, dict)],
                key=lambda item: float(item.get("confidence", 0.0)),
                reverse=True,
            )
            if ranked_discoveries:
                top5 = ranked_discoveries[:5]
                lines.append(f"\U0001f50d Top discoveries ({scanner_count} total):")
                for d in top5:
                    sym = d.get("symbol", "?")
                    cat = str(d.get("category") or d.get("signal_type") or "discovery").replace("_", " ")
                    action = d.get("action", "watch")
                    conf = float(d.get("confidence", 0.0))
                    lines.append(f"  {sym} ({cat}) {action} {conf:.2f}")
            else:
                lines.append(f"\U0001f50d Scanner: 0 discoveries")

            # High-conviction scanner alerts (confidence > 0.75)
            high_conv = [d for d in ranked_discoveries if float(d.get("confidence", 0.0)) > 0.75]
            if high_conv and self.alerter:
                hc_lines = [f"\U0001f6a8 {len(high_conv)} high-conviction scanner discovery(s):"]
                for d in high_conv:
                    sym = d.get("symbol", "?")
                    cat = str(d.get("category") or d.get("signal_type") or "").replace("_", " ")
                    action = d.get("action", "watch")
                    conf = float(d.get("confidence", 0.0))
                    src_name = d.get("source", "")
                    hc_lines.append(f"  {sym} ({cat}) {action} {conf:.2f} [{src_name}]")
                hc_body = "\n".join(hc_lines)
                try:
                    self.alerter._dispatch(
                        "Scanner: High-Conviction Discoveries",
                        hc_body,
                        level="warning",
                        extra={"event": "scanner_high_conviction", "count": len(high_conv)},
                    )
                except Exception:
                    pass

            # Oil regime line
            oil_regime_detail = scorecard.get("v6_oil_regime_detail", {}) or {}
            oil_regime_tg = oil_regime_detail.get("telegram_line")
            if oil_regime_tg:
                lines.append(oil_regime_tg)
            elif scorecard.get("v6_oil_regime"):
                lines.append(f"\u26fd Oil Regime: {scorecard['v6_oil_regime']}")

            if deescalation.get("detected") and deescalation.get("confidence", 0) > 0.5:
                conf = deescalation["confidence"]
                lines.append(f"\u26a0\ufe0f DEESCALATION: confidence={conf:.0%} - review shorts")

            body = "\n".join(lines)

            # Always log the digest
            self.alerter._log_alert("V6 Digest", body, "info", {"event": "v6_digest"})

            # Throttle to once per hour
            event_type = "v6_digest"
            now = self.alerter._time.time()
            last = self.alerter._last_sent.get(event_type, 0)
            if now - last < self.alerter._throttle_seconds:
                return
            self.alerter._last_sent[event_type] = now

            # Route to mo bot topics (forum thread) if configured, else direct send
            # Uses mo2darkbot token, group chat, and v6_digest topic thread
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN_DARKBOT") or os.getenv("TELEGRAM_BOT_TOKEN", "")
            topic_chat = os.getenv("TELEGRAM_TOPIC_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID_DARKBOT") or os.getenv("TELEGRAM_CHAT_ID", "")
            notifier = TelegramTopicNotifier(
                bot_token=bot_token,
                chat_id=topic_chat,
                topic="v6_digest",
            )
            result = notifier.send_message(f"📊 V6 Digest\n\n{body}")
            if not result.ok:
                # Fallback: send directly via alerter if topic routing fails (not if muted)
                if not result.reason.startswith("muted_until:"):
                    try:
                        self.alerter._send_telegram(f"📊 V6 Digest\n\n{body}")
                    except Exception:
                        pass
                print(
                    f"[{iso_now()}] V6 topic routing failed, used fallback: {result.reason}",
                    file=sys.stderr,
                )

            if self.alerter.slack_webhook:
                try:
                    self.alerter._send_slack("V6 Digest", body)
                except Exception:
                    pass
        except Exception as e:
            print(f"[{iso_now()}] V6 telegram digest error (non-fatal): {e}", file=sys.stderr)

    # --- Quantum research comparison (artifact-only) ---
    def _run_quantum_research_comparison(
        self, scorecard: Dict[str, Any], bridge_results: Dict[str, Any],
    ):
        """Run multi-backend quantum research comparison (two-tier frequency).

        SAFETY: This is artifact-only. It NEVER influences execution decisions.
        All outputs carry not_for_direct_execution=true.
        Gated by config/quantum_lane_policy.yaml research_backends.operational_comparison_enabled.

        Two tiers:
          - Lightweight: every cycle (~5 min), quick mode, 30s timeout, no Telegram
          - Full: every 12 cycles (~1 hour), all backends, 120s timeout, Telegram digest
        """
        from pathlib import Path

        # Check policy gate
        try:
            from src.research.quantum_optimizer_bridge import load_lane_policy
            policy = load_lane_policy(self.repo_root / "config" / "quantum_lane_policy.yaml")
        except Exception:
            return
        rb = policy.get("research_backends", {})
        if not rb.get("operational_comparison_enabled", False):
            return

        # Determine tier
        lightweight_interval = int(rb.get("lightweight_interval_cycles", 1))
        full_interval = int(rb.get("full_comparison_interval_cycles", 12))
        is_full_cycle = (self.cycle_count % full_interval == 0)
        is_lightweight_cycle = (self.cycle_count % lightweight_interval == 0)

        if not is_full_cycle and not is_lightweight_cycle:
            return

        try:
            from src.research.backends.multi_backend_orchestrator import MultiBackendOrchestrator
        except ImportError:
            return

        artifact_dir = self.repo_root / "reports" / "research" / "operational"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        orchestrator = MultiBackendOrchestrator(artifact_dir=artifact_dir)

        # Build request from current trade candidates
        candidates = []
        try:
            from src.alpha.trade_analysis_engine import TradeAnalysisEngine
            engine = TradeAnalysisEngine(self.repo_root)
            analysis = engine.analyze(scorecard)
            for idea in (analysis.get("trade_ideas") or [])[:12]:
                candidates.append({
                    "symbol": idea.get("symbol", "?"),
                    "score": idea.get("score", idea.get("composite_score", 0.5)),
                    "expected_return": idea.get("expected_return", idea.get("score", 0.05)),
                    "volatility": idea.get("volatility", 0.2),
                    "sector": idea.get("sector", "unknown"),
                })
        except Exception:
            return

        if len(candidates) < 2:
            return

        # --- Track 2: Enrich candidates with raw bridge context ---
        bridge_context = self._extract_bridge_context(bridge_results)
        for candidate in candidates:
            candidate["bridge_context"] = bridge_context

        # Select tier parameters
        if is_full_cycle:
            mode = "full"
            max_runtime = float(rb.get("max_full_runtime_seconds", 120))
            tier_label = "full"
        else:
            mode = str(rb.get("comparison_mode", "quick"))
            max_runtime = float(rb.get("max_lightweight_runtime_seconds", 30))
            tier_label = "lightweight"

        request = {
            "request_id": "crisis-monitor-cycle-%d-%s" % (self.cycle_count, tier_label),
            "package_id": "operational-comparison",
            "objective": {"type": "portfolio_optimization"},
            "constraints": {"budget": min(len(candidates), 5)},
            "config": {"risk_factor": 0.5},
            "regime_state": scorecard.get("regime_state", {}),
            "regime_components": scorecard.get("components", {}),
            "candidates": candidates,
            "tier": tier_label,
        }

        import time as _time
        t0 = _time.monotonic()
        try:
            report = orchestrator.run_comparison(request, mode=mode)
        except Exception as exc:
            print(
                "[%s] Quantum %s comparison failed: %s" % (iso_now(), tier_label, exc),
                file=sys.stderr,
            )
            return
        elapsed = _time.monotonic() - t0

        # Enforce runtime budget — log warning if exceeded
        if elapsed > max_runtime:
            print(
                "[%s] Quantum %s comparison took %.1fs (budget: %.0fs)"
                % (iso_now(), tier_label, elapsed, max_runtime),
                file=sys.stderr,
            )

        # Persist artifact
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        artifact_path = artifact_dir / ("operational_comparison_%s_%s.json" % (tier_label, tag))
        artifact_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        # Log to experiment tracker
        try:
            from src.research.experiment_tracker import ExperimentTracker
            tracker = ExperimentTracker(self.repo_root)
            tracker.log_result(report)
        except Exception:
            pass

        # Run analog match against crisis library
        crisis_analog = None
        try:
            from src.research.historical_analog_engine import HistoricalAnalogEngine
            analog_engine = HistoricalAnalogEngine(repo_root=self.repo_root)
            regime_state = scorecard.get("regime_state", {})
            if not regime_state:
                # Build from component scores
                regime_state = {}
                comps = scorecard.get("component_scores", {})
                marker_map = {
                    "geopolitical_tension": "energy_disruption",
                    "market_volatility": "vol_spike",
                    "commodity_shock": "energy_disruption",
                    "credit_spread": "banking_stress",
                    "policy_uncertainty": "trade_stress",
                    "currency_stress": "flight_to_quality",
                }
                for comp_name, marker in marker_map.items():
                    val = comps.get(comp_name, 0.0)
                    if isinstance(val, (int, float)):
                        regime_state[marker] = max(regime_state.get(marker, 0.0), float(val))
            matches = analog_engine.find_matches(regime_state, top_n=1, min_similarity=0.5)
            if matches:
                top = matches[0]
                crisis_analog = {
                    "matched_event": top["label"],
                    "similarity": top["similarity"],
                    "category": top.get("category"),
                    "severity": top.get("severity"),
                    "informational_only": True,
                }
                report["crisis_analog"] = crisis_analog
        except Exception:
            pass

        # Append summary to scorecard (non-blocking)
        quantum_summary: Dict[str, Any] = {
            "tier": tier_label,
            "backends_succeeded": report.get("backends_succeeded", []),
            "backends_failed": report.get("backends_failed", []),
            "comparison": report.get("comparison", {}),
            "runtime_seconds": round(elapsed, 2),
            "artifact_path": str(artifact_path),
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
        }
        if crisis_analog:
            quantum_summary["crisis_analog"] = crisis_analog
        scorecard["quantum_research_comparison"] = quantum_summary

        # Send summary to Telegram ONLY on full comparison (lightweight is too frequent)
        if is_full_cycle and self.alerter and report.get("backends_succeeded"):
            try:
                comp = report.get("comparison", {})
                analog_line = ""
                if crisis_analog and crisis_analog.get("similarity", 0) > 0.5:
                    analog_line = "\nAnalog: %s (%.0f%%)" % (
                        crisis_analog.get("matched_event", "?"),
                        crisis_analog.get("similarity", 0) * 100,
                    )
                msg = (
                    "Quantum Research [cycle %d, full]\n"
                    "Backends: %d/%d succeeded\n"
                    "Best: %s\n"
                    "Q vs Classical delta: %s\n"
                    "Runtime: %.1fs%s\n"
                    "[artifact-only, not for execution]"
                ) % (
                    self.cycle_count,
                    len(report.get("backends_succeeded", [])),
                    len(report.get("backends_attempted", [])),
                    comp.get("best_objective_backend", "?"),
                    comp.get("quantum_vs_strong_classical_delta", "N/A"),
                    elapsed,
                    analog_line,
                )
                self.alerter._dispatch("quantum_research", msg, throttle=True)
            except Exception:
                pass

    @staticmethod
    def _extract_bridge_context(bridge_results: Dict[str, Any]) -> Dict[str, Any]:
        """Extract key features from raw bridge results for quantum enrichment.

        This gives quantum backends richer context without changing their output contract.
        """
        ctx: Dict[str, Any] = {}

        # Options greeks
        options = bridge_results.get("options_greeks", {})
        if isinstance(options, dict):
            ctx["put_call_ratio"] = options.get("put_call_ratio")
            ctx["vix_level"] = options.get("vix_level") or options.get("vix")
            ctx["gamma_squeeze_risk"] = options.get("gamma_squeeze_risk")

        # GDELT geopolitical
        gdelt = bridge_results.get("gdelt", {})
        if isinstance(gdelt, dict):
            ctx["geo_severity"] = gdelt.get("max_severity") or gdelt.get("severity")
            ctx["geo_event_count"] = gdelt.get("event_count")

        # EIA energy
        eia = bridge_results.get("eia", {})
        if isinstance(eia, dict):
            ctx["crude_inventory_change"] = eia.get("inventory_change")
            ctx["gas_storage_change"] = eia.get("gas_storage_change")

        # FRED rates
        fred = bridge_results.get("fred", {})
        if isinstance(fred, dict):
            ctx["fed_funds_rate"] = fred.get("fed_funds_rate")
            ctx["spread_10y_2y"] = fred.get("spread_10y_2y") or fred.get("yield_spread")

        # Narrative velocity / sentiment
        narrative = bridge_results.get("narrative_velocity", {})
        if isinstance(narrative, dict):
            ctx["narrative_velocity_score"] = narrative.get("velocity_score") or narrative.get("score")

        # Exa search
        exa = bridge_results.get("exa_search", {})
        if isinstance(exa, dict):
            ctx["crisis_alert_count"] = exa.get("crisis_alert_count") or exa.get("alert_count")
            ctx["hormuz_alert"] = exa.get("hormuz_alert")

        # Market microstructure summary
        micro = bridge_results.get("market_microstructure", {})
        if isinstance(micro, dict):
            ctx["symbols_tracked"] = len(micro) if not micro.get("error") else 0

        # Strip None values
        return {k: v for k, v in ctx.items() if v is not None}

    # --- Persistence ---
    def _persist_scorecard(self, scorecard: Dict[str, Any]):
        try:
            tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            path = self.scorecards_dir / f"scorecard_{tag}.json"
            path.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"[{iso_now()}] scorecard persistence failed: {exc}", file=sys.stderr)

    def _log_shadow_diagnostic(self, event_type: str, payload: Dict[str, Any], message: str):
        diag_payload = dict(payload)
        diag_payload.setdefault("cycle", self.cycle_count)
        self._log_event(event_type, diag_payload)
        print(f"[{iso_now()}] {message}")

    def _log_event(self, event_type: str, payload: Dict[str, Any]):
        row = {
            "schema_version": "crisis_monitor_event.v1",
            "timestamp_utc": iso_now(),
            "component": "crisis_monitor",
            "event_type": event_type,
            "payload": payload,
        }
        try:
            log_path = self.events_dir / "crisis_monitor_events.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(
                f"[{iso_now()}] event log write failed for {event_type}: {exc}",
                file=sys.stderr,
            )

    def _update_heartbeat(self, status: str):
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            self.heartbeat_path.write_text(json.dumps({
                "timestamp_utc": iso_now(),
                "status": status,
                "mode": self.current_mode,
                "cycle": self.cycle_count,
            }, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"[{iso_now()}] heartbeat update failed: {exc}", file=sys.stderr)

    def _shutdown_background_workers(self):
        if self._shutdown_started:
            return
        self._shutdown_started = True
        if self.notifier:
            try:
                self.notifier.stop_hourly_updates()
            except Exception:
                pass
        if self.bot_manager:
            try:
                self.bot_manager.stop()
            except Exception:
                pass

    def _handle_shutdown(self, signum, frame):
        print(f"\n[{iso_now()}] Received signal {signum}, shutting down gracefully...")
        self.running = False
        self._shutdown_background_workers()
        raise KeyboardInterrupt


# --- CLI ---
def parse_args():
    p = argparse.ArgumentParser(description="Global Sentinel Crisis Monitor")
    p.add_argument("--repo-root", default=".", help="Repository root path")
    p.add_argument("--interval", type=int, default=None, help="Override poll interval (seconds)")
    p.add_argument("--single-cycle", action="store_true", help="Run one cycle and exit")
    return p.parse_args()


def main():
    args = parse_args()
    try:
        monitor = CrisisMonitor(Path(args.repo_root).resolve())
        if args.single_cycle:
            monitor._run_cycle()
        else:
            monitor.run(interval_override=args.interval)
        return 0
    except KeyboardInterrupt:
        return 0
    except BaseException as exc:
        print(f"[{iso_now()}] Fatal monitor error: {exc}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
