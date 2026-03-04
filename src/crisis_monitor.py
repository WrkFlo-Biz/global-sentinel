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

        # Produce output
        output = self._produce_output(
            now, controls,
            regime_prob=regime_prob,
            score_result=score_result,
            risk_status=risk_status,
            shadow_eligible=shadow_eligible,
        )

        # Write scorecard
        self._write_scorecard(output)

        # Generate flash memo if warranted
        if self.mode in ("ELEVATED", "CRISIS") or regime_prob > 0.6:
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
