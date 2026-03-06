#!/usr/bin/env python3
"""
Validate the dashboard /api/portfolio response shape.

Usage:
  curl -s http://127.0.0.1:8501/api/portfolio?account=all | python3 scripts/verify/portfolio_schema_check.py
  python3 scripts/verify/portfolio_schema_check.py --input /tmp/portfolio.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def _load_payload(input_path: str | None) -> Dict[str, Any]:
    if input_path:
        return json.loads(Path(input_path).read_text(encoding="utf-8"))

    raw = sys.stdin.read().strip()
    if not raw:
        raise SystemExit("Expected JSON on stdin or via --input.")
    return json.loads(raw)


def _error(errors: List[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="Path to a JSON file. If omitted, stdin is used.")
    args = parser.parse_args()

    payload = _load_payload(args.input)
    errors: List[str] = []

    required_keys = [
        "status",
        "positions",
        "accounts",
        "account_errors",
        "position_count_total",
        "position_count_by_account",
        "account_count",
        "consistency",
        "timestamp_utc",
    ]
    for key in required_keys:
        if key not in payload:
            _error(errors, f"Missing top-level key: {key}")

    status = payload.get("status")
    if status not in {"ok", "partial", "error"}:
        _error(errors, f"Invalid status: {status!r}")

    positions = payload.get("positions")
    accounts = payload.get("accounts")
    account_errors = payload.get("account_errors")
    position_count_total = payload.get("position_count_total")
    position_count_by_account = payload.get("position_count_by_account")
    consistency = payload.get("consistency")

    if not isinstance(positions, list):
        _error(errors, "positions must be a list")
    if not isinstance(accounts, list):
        _error(errors, "accounts must be a list")
    if not isinstance(account_errors, list):
        _error(errors, "account_errors must be a list")
    if not isinstance(position_count_by_account, dict):
        _error(errors, "position_count_by_account must be an object")
    if not isinstance(consistency, dict):
        _error(errors, "consistency must be an object")

    if isinstance(positions, list) and position_count_total != len(positions):
        _error(errors, f"position_count_total={position_count_total!r} does not match positions length={len(positions)}")

    if isinstance(position_count_by_account, dict):
        try:
            summed_counts = sum(int(value) for value in position_count_by_account.values())
        except Exception:
            summed_counts = None
            _error(errors, "position_count_by_account values must be integers")
        if summed_counts is not None and position_count_total != summed_counts:
            _error(
                errors,
                f"position_count_total={position_count_total!r} does not match sum(position_count_by_account)={summed_counts}",
            )

    if isinstance(accounts, list):
        for account in accounts:
            if not isinstance(account, dict):
                _error(errors, f"Account entry must be an object: {account!r}")
                continue
            label = account.get("label")
            if not label:
                _error(errors, f"Account entry missing label: {account!r}")
                continue
            if account.get("status") not in {"ok", "error"}:
                _error(errors, f"Account {label!r} has invalid status: {account.get('status')!r}")
            if "positions" not in account or not isinstance(account.get("positions"), list):
                _error(errors, f"Account {label!r} must include positions list")
            expected_position_count = position_count_by_account.get(label) if isinstance(position_count_by_account, dict) else None
            if expected_position_count is not None and account.get("position_count") != expected_position_count:
                _error(
                    errors,
                    f"Account {label!r} position_count={account.get('position_count')!r} does not match position_count_by_account={expected_position_count}",
                )

    if isinstance(consistency, dict):
        if consistency.get("account_count_requested") != payload.get("account_count"):
            _error(
                errors,
                "consistency.account_count_requested does not match account_count",
            )
        if "positions_match_total" in consistency and consistency.get("positions_match_total") is not True:
            _error(errors, "consistency.positions_match_total is not true")
        if "accounts_match_requested" in consistency and consistency.get("accounts_match_requested") is not True:
            _error(errors, "consistency.accounts_match_requested is not true")

    if errors:
        print(json.dumps({"status": "error", "errors": errors}, indent=2))
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "account_count": payload["account_count"],
                "position_count_total": payload["position_count_total"],
                "portfolio_status": payload["status"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
