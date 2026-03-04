#!/usr/bin/env python3
"""Global Sentinel V4 — Core Crisis Monitor

Main monitoring loop. Runs continuously or --once for a single cycle.
Produces scorecards, checks risk gates, generates flash memos when warranted.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.scoring.regime_shift import RegimeShiftScorer
from src.workflows.flash_memo import FlashMemoGenerator
from src.risk.local_risk_mcp import RiskGate


# ------------------------------------
# Macro policy event merge utilities
# ------------------------------------
def _normalize_macro_policy_packets(raw_packets):
    """Filter and keep only valid macro_policy_event packets."""
    out = []
    for p in raw_packets or []:
        if not isinstance(p, dict):
            continue
        schema = str(p.get("schema_version", ""))
        if schema.startswith("macro_policy_event"):
            out.append(p)
    return out


def _merge_macro_policy_events_into_packet(packet, macro_packets):
    """Merge macro policy bridge outputs into a scorecard packet."""
    macro_events = _normalize_macro_policy_packets(macro_packets)

    packet["macro_policy_events"] = macro_events
    packet["macro_policy_event_count"] = len(macro_events)

    urgency_scores = []
    official_count = 0
    rate_regime_any = False
    requires_cross_asset_any = False
    source_tiers_used = set()
    source_domains_used = set()
    event_types = {}
    official_policy_confirmation_count = 0

    for ev in macro_events:
        try:
            if ev.get("policy_release_urgency_score") is not None:
                urgency_scores.append(float(ev.get("policy_release_urgency_score")))
        except Exception:
            pass

        if ev.get("official_source") is True:
            official_count += 1
        if ev.get("official_source_confirmed") is True:
            official_policy_confirmation_count += 1

        if ev.get("rate_regime_shock_candidate") is True:
            rate_regime_any = True
        if ev.get("requires_rate_cross_asset_check") is True:
            requires_cross_asset_any = True

        if ev.get("source_tier"):
            source_tiers_used.add(str(ev["source_tier"]))
        if ev.get("source_domain"):
            source_domains_used.add(str(ev["source_domain"]))

        et = str(ev.get("event_type", "unknown"))
        event_types[et] = event_types.get(et, 0) + 1

    packet["macro_policy_summary"] = {
        "policy_release_urgency_score_max": max(urgency_scores) if urgency_scores else 0.0,
        "policy_release_urgency_score_avg": (sum(urgency_scores) / len(urgency_scores)) if urgency_scores else 0.0,
        "official_policy_event_count": official_count,
        "official_policy_confirmation_count": official_policy_confirmation_count,
        "rate_regime_shock_candidate_any": rate_regime_any,
        "requires_rate_cross_asset_check": requires_cross_asset_any or rate_regime_any,
        "source_tiers_used": sorted(source_tiers_used),
        "source_domains_used": sorted(source_domains_used),
        "event_type_counts": event_types,
    }

    # Promote flags to top-level for downstream logic
    packet["rate_regime_shock_candidate_any"] = rate_regime_any
    packet["requires_rate_cross_asset_check"] = requires_cross_asset_any or rate_regime_any
    packet["policy_release_urgency_score_max"] = packet["macro_policy_summary"]["policy_release_urgency_score_max"]

    # Mark likely major release day if known event types appear
    major_release_types = {"inflation_release", "labor_release", "central_bank_statement"}
    packet["major_release_day"] = any(t in major_release_types for t in event_types.keys())

    # Operator summary
    top_types = ", ".join([f"{k}:{v}" for k, v in sorted(event_types.items())]) if event_types else "none"
    packet["macro_policy_operator_summary"] = (
        f"macro_events={len(macro_events)} | "
        f"official_confirmations={official_policy_confirmation_count} | "
        f"rate_regime_any={rate_regime_any} | "
        f"urgency_max={packet['policy_release_urgency_score_max']:.2f} | "
        f"types={top_types}"
    )

    return packet


class CrisisMonitor:
    """Main monitoring loop for Global Sentinel."""

    MODES = ["NORMAL", "ELEVATED", "CRISIS", "MANUAL_REVIEW"]

    def __init__(self, config_path: str = None):
        cfg_path = config_path or PROJECT_ROOT / "config" / "thresholds.yaml"
        with open(cfg_path) as f:
            self.config = yaml.safe_load(f)

        self.mode = "NORMAL"
        self.cycles_in_mode = 0
        self.scorer = RegimeShiftScorer(self.config)
        self.memo_gen = FlashMemoGenerator()
        self.risk_gate = RiskGate(PROJECT_ROOT)
        self.heartbeat_file = os.getenv("HEARTBEAT_FILE", "/tmp/global-sentinel-heartbeat")

    def poll_interval_seconds(self) -> int:
        intervals = {"NORMAL": 900, "ELEVATED": 300, "CRISIS": 60, "MANUAL_REVIEW": 0}
        return intervals.get(self.mode, 900)

    def check_controls(self) -> dict:
        """Check kill switch and manual veto."""
        veto_path = PROJECT_ROOT / "control" / "manual_veto.json"
        kill_path = PROJECT_ROOT / "control" / "kill_switch.json"

        veto = json.loads(veto_path.read_text()) if veto_path.exists() else {"manual_veto": False}
        kill = json.loads(kill_path.read_text()) if kill_path.exists() else {"kill_switch": False}

        return {
            "manual_veto": veto.get("manual_veto", False),
            "kill_switch": kill.get("kill_switch", False),
            "veto_reason": veto.get("reason"),
            "kill_reason": kill.get("reason"),
        }

    def evaluate_mode_transition(self, regime_prob: float, controls: dict) -> str:
        """Determine mode with hysteresis."""
        if controls["kill_switch"] or controls["manual_veto"]:
            return "MANUAL_REVIEW"

        thresholds = self.config["mode_thresholds"]
        hysteresis = self.config["hysteresis"]
        min_up = hysteresis["min_cycles_before_upgrade"]
        min_down = hysteresis["min_cycles_before_downgrade"]

        if self.mode == "NORMAL":
            if regime_prob >= thresholds["normal_to_elevated"] and self.cycles_in_mode >= min_up:
                return "ELEVATED"
        elif self.mode == "ELEVATED":
            if regime_prob >= thresholds["elevated_to_crisis"] and self.cycles_in_mode >= min_up:
                return "CRISIS"
            if regime_prob < thresholds["elevated_to_normal"] and self.cycles_in_mode >= min_down:
                return "NORMAL"
        elif self.mode == "CRISIS":
            if regime_prob < thresholds["crisis_to_elevated"] and self.cycles_in_mode >= min_down:
                return "ELEVATED"
        elif self.mode == "MANUAL_REVIEW":
            if not controls["kill_switch"] and not controls["manual_veto"]:
                return "ELEVATED"  # conservative re-entry

        return self.mode

    def _poll_macro_bridges(self) -> list:
        """Poll available macro policy bridges and collect packets.
        Bridges are loaded in-process if available, or from cached output files."""
        macro_packets = []
        bridge_classes = [
            ("fed_board_bridge", "FedBoardBridge"),
            ("bls_release_bridge", "BLSReleaseBridge"),
            ("treasury_ofac_bridge", "TreasuryOFACBridge"),
            ("whitehouse_policy_bridge", "WhiteHousePolicyBridge"),
            ("fred_bridge", "FredBridge"),
            ("eia_bridge", "EIABridge"),
            ("finnhub_bridge", "FinnhubBridge"),
        ]
        for module_name, class_name in bridge_classes:
            try:
                import importlib
                mod = importlib.import_module(f"src.bridges.{module_name}")
                bridge_cls = getattr(mod, class_name)
                bridge = bridge_cls(PROJECT_ROOT)
                packets = bridge.poll()
                macro_packets.extend(packets)
            except Exception:
                pass  # Bridge not available or failed — non-fatal
        return macro_packets

    def run_cycle(self) -> dict:
        """Execute one monitoring cycle."""
        now = datetime.now(timezone.utc)
        controls = self.check_controls()

        if controls["kill_switch"]:
            self.mode = "MANUAL_REVIEW"
            return self._produce_output(now, controls, halted=True, reason="kill_switch")

        # Score regime
        score_result = self.scorer.score()
        regime_prob = score_result["regime_shift_probability"]

        # Mode transition
        new_mode = self.evaluate_mode_transition(regime_prob, controls)
        if new_mode != self.mode:
            self.mode = new_mode
            self.cycles_in_mode = 0
        else:
            self.cycles_in_mode += 1

        # Risk gate check
        risk_status = self.risk_gate.evaluate(score_result, self.mode, controls)

        # Shadow draft eligibility
        shadow_eligible = (
            self.mode not in ("CRISIS", "MANUAL_REVIEW")
            and not controls["manual_veto"]
            and score_result.get("confidence", 0) >= self.config["confidence"]["min_confidence_for_shadow_draft"]
            and risk_status.get("approved", False)
        )

        # Poll macro policy bridges (non-fatal if bridges unavailable)
        macro_packets = []
        try:
            macro_packets = self._poll_macro_bridges()
        except Exception:
            pass

        # Produce output
        output = self._produce_output(
            now, controls,
            regime_prob=regime_prob,
            score_result=score_result,
            risk_status=risk_status,
            shadow_eligible=shadow_eligible,
            macro_packets=macro_packets,
        )

        # Write scorecard
        self._write_scorecard(output)

        # Generate flash memo if warranted (mode-based or high-urgency macro event)
        macro_urgency_max = output.get("policy_release_urgency_score_max", 0.0)
        if self.mode in ("ELEVATED", "CRISIS") or regime_prob > 0.6 or macro_urgency_max >= 0.90:
            self.memo_gen.generate(output)

        # Update heartbeat
        Path(self.heartbeat_file).write_text(now.isoformat())

        return output

    def _produce_output(self, now, controls, halted=False, reason=None, **kwargs):
        output = {
            "timestamp_utc": now.isoformat(),
            "mode": self.mode,
            "regime_shift_probability": kwargs.get("regime_prob", None),
            "component_scores": kwargs.get("score_result", {}).get("component_scores", {}),
            "confidence": kwargs.get("score_result", {}).get("confidence", 0),
            "evidence": kwargs.get("score_result", {}).get("evidence", []),
            "data_freshness_status": kwargs.get("score_result", {}).get("freshness", {}),
            "threshold_values_used": self.config.get("mode_thresholds", {}),
            "risk_gate_status": kwargs.get("risk_status", {}).get("status", "halted" if halted else "unknown"),
            "manual_veto_status": controls["manual_veto"],
            "kill_switch_status": controls["kill_switch"],
            "fallback_mode_status": kwargs.get("score_result", {}).get("fallback_mode", False),
            "shadow_execution_eligible": kwargs.get("shadow_eligible", False),
            "hedge_draft": None,
        }
        if halted:
            output["halt_reason"] = reason

        # Merge macro policy events into output packet
        macro_packets = kwargs.get("macro_packets", [])
        if macro_packets:
            _merge_macro_policy_events_into_packet(output, macro_packets)

        return output

    def _write_scorecard(self, output: dict):
        ts = output["timestamp_utc"].replace(":", "-")
        path = PROJECT_ROOT / "logs" / "scorecards" / f"scorecard-{ts}.json"
        path.write_text(json.dumps(output, indent=2, default=str))

    def run(self, once: bool = False):
        """Main loop."""
        print(f"[Global Sentinel V4] Starting in {self.mode} mode (once={once})")
        while True:
            try:
                result = self.run_cycle()
                print(f"[{result['timestamp_utc']}] mode={result['mode']} "
                      f"regime_p={result['regime_shift_probability']:.3f} "
                      f"conf={result['confidence']:.2f} "
                      f"shadow={result['shadow_execution_eligible']}")
            except Exception as e:
                print(f"[ERROR] Cycle failed: {e}", file=sys.stderr)
                # Dead-letter the error
                dl_path = PROJECT_ROOT / "logs" / "dead_letter" / f"error-{datetime.now(timezone.utc).isoformat().replace(':', '-')}.json"
                dl_path.write_text(json.dumps({"error": str(e), "timestamp": datetime.now(timezone.utc).isoformat()}))

            if once:
                break

            interval = self.poll_interval_seconds()
            if interval == 0:
                print("[MANUAL_REVIEW] Monitoring paused. Waiting for control reset...")
                time.sleep(60)
            else:
                time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Global Sentinel V4 Crisis Monitor")
    parser.add_argument("--once", action="store_true", help="Run single cycle and exit")
    parser.add_argument("--config", type=str, help="Path to thresholds.yaml")
    args = parser.parse_args()

    monitor = CrisisMonitor(config_path=args.config)
    monitor.run(once=args.once)


if __name__ == "__main__":
    main()
