#!/usr/bin/env python3
"""
CLI runner for the Broker Order Audit tool.

Usage:
  python3 scripts/ops/run_broker_order_audit.py --dry-run
  python3 scripts/ops/run_broker_order_audit.py --execute
  python3 scripts/ops/run_broker_order_audit.py --verify-flat
  python3 scripts/ops/run_broker_order_audit.py --dry-run --telegram
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from src.execution.broker_order_audit import BrokerOrderAudit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Broker Order Audit — classify and clean up pending orders"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Report only, do not cancel anything (default)",
    )
    mode.add_argument(
        "--execute", dest="execute", action="store_true",
        help="Actually cancel stale/duplicate/crypto orders",
    )
    mode.add_argument(
        "--verify-flat", dest="verify_flat", action="store_true",
        help="Post-open check: verify both accounts have 0 positions",
    )
    parser.add_argument(
        "--telegram", action="store_true",
        help="Send Telegram alert for actionable items",
    )
    parser.add_argument(
        "--output-dir", default="reports/operational",
        help="Directory for report output",
    )
    parser.add_argument(
        "--json-only", action="store_true",
        help="Print JSON summary only (no text report)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dry_run = not args.execute

    audit = BrokerOrderAudit(repo_root=REPO_ROOT, dry_run=dry_run)

    # Verify-flat mode
    if args.verify_flat:
        result = audit.verify_flat()
        if args.json_only:
            print(json.dumps(result, indent=2, default=str))
        else:
            print("\nFLAT VERIFICATION")
            print("=" * 40)
            for acct_name, acct in result.get("accounts", {}).items():
                status = "FLAT" if acct["is_flat"] else "NOT FLAT"
                print(f"  {acct_name} ({acct['account_id']}): {status}")
                print(f"    Positions: {acct['positions']}, Open Orders: {acct['open_orders']}")
                if acct["position_symbols"]:
                    print(f"    Symbols: {', '.join(acct['position_symbols'])}")
            all_flat = result.get("all_flat", False)
            print(f"\n  All accounts flat: {'YES' if all_flat else 'NO'}")
        return 0 if result.get("all_flat", False) else 1

    # Normal audit mode
    report = audit.run_audit()
    paths = audit.save_report(report, output_dir=args.output_dir)

    if args.json_only:
        summary = report.get("global_summary", {})
        summary["saved_to"] = paths
        print(json.dumps(summary, indent=2, default=str))
    else:
        # Print the text report
        print(report.get("text_report", ""))
        print(f"\nReports saved to:")
        for k, v in paths.items():
            print(f"  {k}: {v}")

    # Telegram alert
    if args.telegram:
        sent = audit.send_telegram_alert(report)
        if sent:
            print("\nTelegram alert sent.")
        else:
            print("\nNo Telegram alert needed (no actionable items).")

    # Exit code: 0 if flat-ready, 1 if actionable items
    gs = report.get("global_summary", {})
    return 0 if gs.get("flat_ready", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
