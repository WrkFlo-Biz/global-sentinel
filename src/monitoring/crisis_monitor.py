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


def _format_gss_recommendation(item: Dict[str, Any]) -> str:
    """Render one GSS recommendation without assuming legacy field names."""
    instrument = (
        item.get("instrument")
        or item.get("symbol")
        or item.get("name")
        or "Unnamed recommendation"
    )
    action = item.get("action") or item.get("direction") or "HOLD"
    summary = (
        item.get("rationale")
        or item.get("reason")
        or item.get("spec")
        or item.get("sizing")
        or item.get("options_note")
        or ""
    )

    if summary:
        return f"{instrument} | {action}: {summary}"
    return f"{instrument} | {action}"


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

        # Telegram command handlers (remote control via bot messages)
        self.bot_manager = None
        try:
            from src.monitoring.telegram_bot_manager import TelegramBotManager
            self.bot_manager = TelegramBotManager(repo_root)
            self.bot_manager.start()
        except Exception as e:
            print(f"[{iso_now()}] Telegram bot manager failed to start: {e}", file=sys.stderr)

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

        while self.running:
            try:
                self._run_cycle()
            except Exception as e:
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

        print(f"[{iso_now()}] Crisis Monitor shutting down after {self.cycle_count} cycles")

    def _run_cycle(self):
        """Execute one monitoring cycle."""
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

        # 3. Build composite snapshot
        snapshot = self._build_snapshot(bridge_results, kill_switch, manual_veto)

        # 4. Score regime shift
        regime_score = self._score_regime(snapshot)

        # 5. Classify time window
        time_window = self._classify_time_window(snapshot)

        # 6. Determine operating mode
        new_mode = self._resolve_mode(regime_score, snapshot)
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

        # 7. Build and persist scorecard
        scorecard = {
            "schema_version": "scorecard.v5",
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
            "gss_signal": None,  # populated in step 8.7 if non-neutral
        }

        self.last_scorecard = scorecard
        self._persist_scorecard(scorecard)
        self._update_heartbeat("ok")

        # Send scorecard summary alerts in ELEVATED/CRISIS modes
        if self.current_mode in ("ELEVATED", "CRISIS") and self.alerter:
            try:
                self.alerter.send_scorecard_summary(scorecard)
            except Exception:
                pass

        # 8. Shadow execution: generate trade ideas and route to paper broker
        if scorecard.get("shadow_execution_eligible"):
            shadow_result = self._run_shadow_execution(scorecard, bridge_results)
            if shadow_result and shadow_result.get("submitted_open_or_ack_count", 0) > 0:
                self._log_event("shadow_orders_submitted", {
                    "cycle": self.cycle_count,
                    "orders_submitted": shadow_result.get("submitted_open_or_ack_count", 0),
                    "candidates": len(shadow_result.get("selected_candidates", [])),
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
                                msg_lines.append(f"  - {_format_gss_recommendation(h)}")

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
            greeks_data = ogb.build_snapshot_section()
            results["options_greeks"] = greeks_data
            results["freshness"]["options_greeks"] = greeks_data.get("fresh", False)
            results["summary"]["put_call_ratio"] = greeks_data.get("put_call_ratio", 0)
            results["summary"]["gamma_squeeze_risk"] = greeks_data.get("gamma_squeeze_risk", "low")
        except Exception as e:
            bridge_errors.append(f"options_greeks: {e}")
            results["freshness"]["options_greeks"] = False

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
        except Exception:
            return {"current_window": "unknown", "shadow_execution_window_blocked": True}

    # --- Mode resolution ---
    def _resolve_mode(self, regime_score: Dict[str, Any], snapshot: Dict[str, Any]) -> str:
        p = regime_score.get("regime_shift_probability", 0.0)
        thresholds = self.thresholds.get("mode_thresholds", {})

        if snapshot.get("controls", {}).get("manual_veto"):
            return "MANUAL_REVIEW"

        crisis_threshold = float(thresholds.get("crisis", 0.85))
        elevated_threshold = float(thresholds.get("elevated", 0.55))

        # Hysteresis: require higher threshold to escalate, lower to de-escalate
        hysteresis = 0.05
        if self.current_mode == "CRISIS":
            if p < crisis_threshold - hysteresis:
                return "ELEVATED" if p >= elevated_threshold else "NORMAL"
            return "CRISIS"
        elif self.current_mode == "ELEVATED":
            if p >= crisis_threshold:
                return "CRISIS"
            if p < elevated_threshold - hysteresis:
                return "NORMAL"
            return "ELEVATED"
        else:  # NORMAL
            if p >= crisis_threshold:
                return "CRISIS"
            if p >= elevated_threshold:
                return "ELEVATED"
            return "NORMAL"

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
                bridge_results["_feedback_daily_target"] = feedback_result.get("daily_target", {})
                if feedback_result.get("status") == "active":
                    self._log_event("feedback_loop_active", {
                        "trades_analyzed": feedback_result.get("trades_analyzed", 0),
                        "adjustments_count": len(learned_adjustments),
                        "daily_target": feedback_result.get("daily_target", {}),
                    })
            except Exception as e:
                print(f"[{iso_now()}] Feedback loop error (non-fatal): {e}", file=sys.stderr)

            # Check for existing open orders to avoid duplicates
            existing_symbols = self._get_open_order_symbols()

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

            if analysis.get("error") or not analysis.get("trade_ideas"):
                return None

            # Filter out symbols we already have open orders/positions for
            if existing_symbols:
                analysis["trade_ideas"] = [
                    idea for idea in analysis["trade_ideas"]
                    if idea.get("symbol") not in existing_symbols
                ]
                if not analysis["trade_ideas"]:
                    return None

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
            if not ideas:
                continue

            strategy_cfg = sm.get_strategy_config(strategy_name)
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

            if not package.get("candidates") or package.get("global_blocks"):
                continue

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

    def _get_open_order_symbols(self) -> set:
        """Get symbols with existing open orders or positions to avoid duplicates."""
        symbols = set()
        try:
            from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter
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

    # --- Persistence ---
    def _persist_scorecard(self, scorecard: Dict[str, Any]):
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = self.scorecards_dir / f"scorecard_{tag}.json"
        path.write_text(json.dumps(scorecard, indent=2), encoding="utf-8")

    def _log_event(self, event_type: str, payload: Dict[str, Any]):
        row = {
            "timestamp_utc": iso_now(),
            "event_type": event_type,
            "payload": payload,
        }
        log_path = self.events_dir / "crisis_monitor_events.jsonl"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _update_heartbeat(self, status: str):
        self.heartbeat_path.write_text(json.dumps({
            "timestamp_utc": iso_now(),
            "status": status,
            "mode": self.current_mode,
            "cycle": self.cycle_count,
        }, indent=2), encoding="utf-8")

    def _handle_shutdown(self, signum, frame):
        print(f"\n[{iso_now()}] Received signal {signum}, shutting down gracefully...")
        self.running = False
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


# --- CLI ---
def parse_args():
    p = argparse.ArgumentParser(description="Global Sentinel Crisis Monitor")
    p.add_argument("--repo-root", default=".", help="Repository root path")
    p.add_argument("--interval", type=int, default=None, help="Override poll interval (seconds)")
    p.add_argument("--single-cycle", action="store_true", help="Run one cycle and exit")
    return p.parse_args()


def main():
    args = parse_args()
    monitor = CrisisMonitor(Path(args.repo_root).resolve())

    if args.single_cycle:
        monitor._run_cycle()
    else:
        monitor.run(interval_override=args.interval)


if __name__ == "__main__":
    main()
