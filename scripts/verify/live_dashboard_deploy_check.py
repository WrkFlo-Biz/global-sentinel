#!/usr/bin/env python3
"""
Verify that the live dashboard/API matches the local lane-B build and schema.

Checks:
- live `/api/portfolio` includes the latest expected schema keys
- live `/api/execution/summary` includes the latest expected schema keys
- live root page serves the same app page chunk as the local built frontend
- live page chunk includes the portfolio and execution UI markers introduced in the current build

Usage:
  python3 scripts/verify/live_dashboard_deploy_check.py \
    --url http://20.124.180.8:8501 \
    --api-key "$GS_DASHBOARD_API_KEY"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

PAGE_CHUNK_RE = re.compile(r'/_next/static/chunks/app/page-[^"]+\.js')

PORTFOLIO_REQUIRED_KEYS = [
    "schema_version",
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

CONSISTENCY_REQUIRED_KEYS = [
    "account_count_requested",
    "account_count_success",
    "account_count_error",
    "position_count_total",
    "position_count_total_from_accounts",
    "position_count_by_account",
    "requested_accounts",
    "accounts_match_requested",
    "positions_match_total",
    "has_account_errors",
]

UI_MARKERS = [
    "Partial account failure",
    "Portfolio unavailable. All requested accounts failed.",
    "Routing Funnel",
    "True Fill Rate",
    "Skip / Block Categories",
    "Top Raw Reasons",
]

EXECUTION_SUMMARY_REQUIRED_KEYS = [
    "schema_version",
    "timestamp_utc",
    "routing",
    "live_orders",
]

ROUTING_REQUIRED_KEYS = [
    "event_count",
    "processed_candidate_count",
    "submit_attempt_count",
    "submit_success_count",
    "broker_rejected_count",
    "skipped_count",
    "error_count",
    "candidate_conversion_rate",
    "broker_accept_rate",
    "skip_or_block_rate",
    "block_reason_category_counts",
    "raw_block_reason_counts",
]

LIVE_ORDERS_REQUIRED_KEYS = [
    "status",
    "lookback_hours",
    "sample_window_start_utc",
    "account_count",
    "order_count_total",
    "filled_count",
    "partially_filled_count",
    "open_count",
    "rejected_count",
    "canceled_count",
    "expired_count",
    "other_count",
    "fill_rate_any",
    "fill_rate_full",
    "open_rate",
    "by_account",
    "raw_status_counts",
    "account_errors",
]


def _fetch_text(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, str]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        body = resp.read().decode("utf-8", errors="ignore")
        return int(status), body


def _fetch_json(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, Dict[str, Any]]:
    status, body = _fetch_text(url, headers=headers, timeout=timeout)
    return status, json.loads(body)


def _extract_page_chunk(html: str) -> str | None:
    match = PAGE_CHUNK_RE.search(html)
    return match.group(0) if match else None


def _validate_portfolio_payload(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    for key in PORTFOLIO_REQUIRED_KEYS:
        if key not in payload:
            errors.append(f"missing top-level key: {key}")

    if payload.get("status") not in {"ok", "partial", "error"}:
        errors.append(f"invalid portfolio status: {payload.get('status')!r}")

    positions = payload.get("positions")
    accounts = payload.get("accounts")
    account_errors = payload.get("account_errors")
    position_count_total = payload.get("position_count_total")
    position_count_by_account = payload.get("position_count_by_account")
    consistency = payload.get("consistency")

    if not isinstance(positions, list):
        errors.append("positions must be a list")
    if not isinstance(accounts, list):
        errors.append("accounts must be a list")
    if not isinstance(account_errors, list):
        errors.append("account_errors must be a list")
    if not isinstance(position_count_by_account, dict):
        errors.append("position_count_by_account must be an object")
    if not isinstance(consistency, dict):
        errors.append("consistency must be an object")

    if isinstance(positions, list) and position_count_total != len(positions):
        errors.append(
            f"position_count_total={position_count_total!r} does not match positions length={len(positions)}"
        )

    if isinstance(position_count_by_account, dict):
        try:
            by_account_total = sum(int(value) for value in position_count_by_account.values())
        except Exception:
            by_account_total = None
            errors.append("position_count_by_account values must be integers")
        if by_account_total is not None and position_count_total != by_account_total:
            errors.append(
                f"position_count_total={position_count_total!r} does not match sum(position_count_by_account)={by_account_total}"
            )

    if isinstance(consistency, dict):
        for key in CONSISTENCY_REQUIRED_KEYS:
            if key not in consistency:
                errors.append(f"missing consistency key: {key}")
        if "account_count_requested" in consistency and consistency.get("account_count_requested") != payload.get("account_count"):
            errors.append("consistency.account_count_requested does not match account_count")
        if "position_count_total" in consistency and consistency.get("position_count_total") != payload.get("position_count_total"):
            errors.append("consistency.position_count_total does not match position_count_total")
        if "position_count_by_account" in consistency and consistency.get("position_count_by_account") != payload.get("position_count_by_account"):
            errors.append("consistency.position_count_by_account does not match top-level position_count_by_account")
        if "position_count_total_from_accounts" in consistency and consistency.get("position_count_total_from_accounts") != payload.get("position_count_total"):
            errors.append("consistency.position_count_total_from_accounts does not match position_count_total")
        if "accounts_match_requested" in consistency and consistency.get("accounts_match_requested") is not True:
            errors.append("consistency.accounts_match_requested is not true")
        if "positions_match_total" in consistency and consistency.get("positions_match_total") is not True:
            errors.append("consistency.positions_match_total is not true")

    return errors


def _validate_execution_summary_payload(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    for key in EXECUTION_SUMMARY_REQUIRED_KEYS:
        if key not in payload:
            errors.append(f"missing execution-summary top-level key: {key}")

    if payload.get("schema_version") != "dashboard.execution_summary.v1":
        errors.append(f"invalid execution summary schema_version: {payload.get('schema_version')!r}")

    routing = payload.get("routing")
    live_orders = payload.get("live_orders")

    if not isinstance(routing, dict):
        errors.append("routing must be an object")
    if not isinstance(live_orders, dict):
        errors.append("live_orders must be an object")

    if isinstance(routing, dict):
        for key in ROUTING_REQUIRED_KEYS:
            if key not in routing:
                errors.append(f"missing routing key: {key}")
        if not isinstance(routing.get("block_reason_category_counts"), dict):
            errors.append("routing.block_reason_category_counts must be an object")
        if not isinstance(routing.get("raw_block_reason_counts"), dict):
            errors.append("routing.raw_block_reason_counts must be an object")

    if isinstance(live_orders, dict):
        for key in LIVE_ORDERS_REQUIRED_KEYS:
            if key not in live_orders:
                errors.append(f"missing live_orders key: {key}")
        if live_orders.get("status") not in {"ok", "partial", "error", "unavailable"}:
            errors.append(f"invalid live_orders status: {live_orders.get('status')!r}")
        if not isinstance(live_orders.get("by_account"), dict):
            errors.append("live_orders.by_account must be an object")
        if not isinstance(live_orders.get("raw_status_counts"), dict):
            errors.append("live_orders.raw_status_counts must be an object")
        if not isinstance(live_orders.get("account_errors"), list):
            errors.append("live_orders.account_errors must be a list")

    return errors


def _check_api(base_url: str, api_key: str, timeout: float) -> Dict[str, Any]:
    headers = {"X-API-Key": api_key} if api_key else {}
    portfolio_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", "api/portfolio?account=all")
    portfolio_status, portfolio_payload = _fetch_json(portfolio_url, headers=headers, timeout=timeout)
    execution_url = urllib.parse.urljoin(
        base_url.rstrip("/") + "/",
        "api/execution/summary?router_limit=20&broker_limit=50&lookback_hours=24",
    )
    execution_status, execution_payload = _fetch_json(execution_url, headers=headers, timeout=timeout)
    errors = []
    if portfolio_status != 200:
        errors.append(f"unexpected HTTP status from portfolio endpoint: {portfolio_status}")
    errors.extend(_validate_portfolio_payload(portfolio_payload))
    if execution_status != 200:
        errors.append(f"unexpected HTTP status from execution summary endpoint: {execution_status}")
    errors.extend(_validate_execution_summary_payload(execution_payload))
    return {
        "portfolio_url": portfolio_url,
        "portfolio_http_status": portfolio_status,
        "schema_version": portfolio_payload.get("schema_version"),
        "portfolio_status": portfolio_payload.get("status"),
        "account_count": portfolio_payload.get("account_count"),
        "position_count_total": portfolio_payload.get("position_count_total"),
        "execution_summary_url": execution_url,
        "execution_summary_http_status": execution_status,
        "execution_summary_schema_version": execution_payload.get("schema_version"),
        "execution_summary_live_order_status": (execution_payload.get("live_orders") or {}).get("status"),
        "errors": errors,
    }


def _read_local_page_chunk(local_index: Path) -> Tuple[str | None, str]:
    html = local_index.read_text(encoding="utf-8", errors="ignore")
    chunk = _extract_page_chunk(html)
    return chunk, html


def _chunk_file_from_local_index(local_index: Path, chunk_path: str) -> Path:
    return local_index.parent / chunk_path.lstrip("/")


def _check_frontend(base_url: str, local_index: Path, timeout: float) -> Dict[str, Any]:
    errors: List[str] = []
    if not local_index.exists():
        return {
            "errors": [f"local built index not found: {local_index}"],
        }

    local_chunk, _ = _read_local_page_chunk(local_index)
    if not local_chunk:
        return {
            "errors": [f"could not find local page chunk in {local_index}"],
        }

    live_root_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", "")
    live_status, live_html = _fetch_text(live_root_url, headers={}, timeout=timeout)
    live_chunk = _extract_page_chunk(live_html)

    if live_status != 200:
        errors.append(f"unexpected HTTP status from dashboard root: {live_status}")
    if not live_chunk:
        errors.append("could not find live page chunk in dashboard root HTML")
    if live_chunk != local_chunk:
        errors.append(f"live page chunk {live_chunk!r} does not match local built chunk {local_chunk!r}")

    local_chunk_file = _chunk_file_from_local_index(local_index, local_chunk)
    if not local_chunk_file.exists():
        errors.append(f"local built page chunk file not found: {local_chunk_file}")
        expected_markers: List[str] = []
    else:
        local_js = local_chunk_file.read_text(encoding="utf-8", errors="ignore")
        expected_markers = [marker for marker in UI_MARKERS if marker in local_js]

    live_chunk_url = urllib.parse.urljoin(base_url.rstrip("/") + "/", (live_chunk or local_chunk).lstrip("/"))
    live_chunk_status, live_chunk_body = _fetch_text(live_chunk_url, headers={}, timeout=timeout)
    if live_chunk_status != 200:
        errors.append(f"unexpected HTTP status from live page chunk: {live_chunk_status}")

    missing_markers = [marker for marker in expected_markers if marker not in live_chunk_body]
    for marker in missing_markers:
        errors.append(f"live page chunk missing UI marker: {marker}")

    return {
        "root_url": live_root_url,
        "http_status": live_status,
        "local_page_chunk": local_chunk,
        "live_page_chunk": live_chunk,
        "expected_ui_markers": expected_markers,
        "missing_ui_markers": missing_markers,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Dashboard base URL, e.g. http://20.124.180.8:8501")
    parser.add_argument("--api-key", default=os.getenv("GS_DASHBOARD_API_KEY", ""), help="Dashboard API key")
    parser.add_argument("--local-index", default="dashboard/frontend/out/index.html", help="Local built frontend index path")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()

    summary: Dict[str, Any] = {"status": "ok"}
    errors: List[str] = []

    if not args.skip_api:
        if not args.api_key:
            errors.append("api verification requested but no API key was provided")
        else:
            try:
                summary["api"] = _check_api(args.url, args.api_key, args.timeout)
                errors.extend(summary["api"]["errors"])
            except urllib.error.URLError as exc:
                errors.append(f"api check failed: {exc}")
            except Exception as exc:
                errors.append(f"api check failed: {exc}")

    if not args.skip_frontend:
        try:
            summary["frontend"] = _check_frontend(args.url, Path(args.local_index), args.timeout)
            errors.extend(summary["frontend"]["errors"])
        except urllib.error.URLError as exc:
            errors.append(f"frontend check failed: {exc}")
        except Exception as exc:
            errors.append(f"frontend check failed: {exc}")

    if errors:
        summary["status"] = "error"
        summary["errors"] = errors

    print(json.dumps(summary, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
