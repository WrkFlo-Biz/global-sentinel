#!/usr/bin/env python3
"""Global Sentinel V4 — Local Risk Gate MCP

Evaluates risk gates: threshold checks, veto/kill switch, mode constraints.
Exposes as MCP server for Claude CLI orchestration.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class RiskGate:
    """Evaluate risk gates for a monitoring cycle."""

    def __init__(self, project_root: Path = None):
        self.root = project_root or PROJECT_ROOT

    def evaluate(self, score_result: dict, mode: str, controls: dict) -> dict:
        """Check all risk gates and return approval status."""
        checks = []

        # Kill switch
        if controls.get("kill_switch", False):
            return {"approved": False, "status": "HALTED", "reason": "kill_switch_active", "checks": []}

        # Manual veto
        if controls.get("manual_veto", False):
            return {"approved": False, "status": "VETOED", "reason": "manual_veto_active", "checks": []}

        # Confidence minimum
        confidence = score_result.get("confidence", 0)
        conf_check = confidence >= 0.3
        checks.append({"gate": "min_confidence", "passed": conf_check, "value": confidence})

        # Fallback mode caution
        fallback = score_result.get("fallback_mode", False)
        checks.append({"gate": "fallback_mode", "passed": not fallback, "value": fallback})

        # Crisis config freeze check
        crisis_frozen = mode == "CRISIS"
        checks.append({"gate": "crisis_config_freeze", "passed": True, "value": crisis_frozen,
                        "note": "Config changes blocked" if crisis_frozen else "Config changes allowed"})

        all_passed = all(c["passed"] for c in checks)
        return {
            "approved": all_passed,
            "status": "APPROVED" if all_passed else "BLOCKED",
            "checks": checks,
        }


def serve_mcp():
    """Simple MCP server over stdin/stdout (JSON-RPC style)."""
    gate = RiskGate()
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method", "")

            if method == "evaluate":
                params = request.get("params", {})
                result = gate.evaluate(
                    score_result=params.get("score_result", {}),
                    mode=params.get("mode", "NORMAL"),
                    controls=params.get("controls", {}),
                )
                response = {"id": request.get("id"), "result": result}
            elif method == "status":
                response = {"id": request.get("id"), "result": {"status": "ok", "service": "local-risk-mcp"}}
            else:
                response = {"id": request.get("id"), "error": f"Unknown method: {method}"}

            print(json.dumps(response), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)


if __name__ == "__main__":
    serve_mcp()
