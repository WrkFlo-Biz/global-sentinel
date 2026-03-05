#!/usr/bin/env python3
"""
Render CI job summary to GitHub Actions Job Summary.

Reads JSON summaries produced by replay runner / smoke pack.
Expected each summary JSON to include canonical fields:
- scenario_package_id
- bound_order_attempt_count
- broker_rejected_count
- pass_rate
- failed_checks (list[str]) OR failures (list[object])
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    summary_path = Path(os.environ.get("CI_SUMMARY_JSON", "artifacts/ci_summary.json"))
    out_path = os.environ.get("GITHUB_STEP_SUMMARY")

    if not summary_path.exists():
        print(f"No summary JSON found at {summary_path}")
        return 0

    data = load_json(summary_path)

    scenario_package_id = data.get("scenario_package_id", "unknown")
    bound_attempts = int(data.get("bound_order_attempt_count", 0))
    broker_rejects = int(data.get("broker_rejected_count", 0))
    pass_rate = data.get("pass_rate")

    failures: List[str] = []
    if isinstance(data.get("failed_checks"), list):
        failures = [str(x) for x in data["failed_checks"]]
    elif isinstance(data.get("failures"), list):
        for f in data["failures"]:
            if isinstance(f, dict):
                failures.append(str(f.get("check") or f.get("name") or f))
            else:
                failures.append(str(f))

    lines: List[str] = []
    lines.append("## CI Replay Summary\n")
    lines.append(f"- **scenario_package_id:** `{scenario_package_id}`")
    lines.append(f"- **bound_order_attempt_count:** `{bound_attempts}`")
    lines.append(f"- **broker_rejected_count:** `{broker_rejects}`")
    if pass_rate is not None:
        lines.append(f"- **pass_rate:** `{pass_rate}`")
    lines.append("")

    if failures:
        lines.append("### Failed Checks")
        lines.append("")
        lines.append("| check |")
        lines.append("|---|")
        for c in failures:
            lines.append(f"| `{c}` |")
        lines.append("")
    else:
        lines.append("No failed checks reported.\n")

    text = "\n".join(lines)

    if out_path:
        out_p = Path(out_path)
        existing = out_p.read_text(encoding="utf-8") if out_p.exists() else ""
        out_p.write_text(existing + "\n" + text if existing else text, encoding="utf-8")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
