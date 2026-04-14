#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.monitoring.position_threshold_monitor import PositionThresholdMonitor


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor held positions for threshold crossings.")
    parser.add_argument("--repo-root", default="/opt/global-sentinel")
    parser.add_argument("--dashboard-base-url", default="http://127.0.0.1:8501")
    parser.add_argument("--config", default="")
    parser.add_argument("--state-path", default="")
    parser.add_argument("--event-log-path", default="")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    monitor = PositionThresholdMonitor(
        repo_root,
        dashboard_base_url=args.dashboard_base_url,
        config_path=Path(args.config).resolve() if args.config else None,
        state_path=Path(args.state_path).resolve() if args.state_path else None,
        event_log_path=Path(args.event_log_path).resolve() if args.event_log_path else None,
    )

    if args.once:
        print(json.dumps(monitor.poll_once(), indent=2, sort_keys=True))
        return

    monitor.run_forever()


if __name__ == "__main__":
    main()
