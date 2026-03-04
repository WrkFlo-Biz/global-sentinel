#!/usr/bin/env python3
"""
Global Sentinel V4.5 - No-Trade Quality Metrics

Purpose:
- Quantify decision quality through disciplined non-actions
- Track no-trade and watchlist-only outcomes as positive process signals
- Measure guardrail effectiveness and prevent overtrading

Inputs:
- package JSON files from idiosyncratic_package_builder
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return n / d


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


class NoTradeQualityMetrics:
    def build(self, packages: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(packages)
        no_trade_count = 0
        watchlist_only_count = 0
        blocked_all_count = 0
        high_urgency_no_trade_count = 0
        macro_quorum_fail_no_trade_count = 0

        reason_counts = defaultdict(int)
        global_block_counts = defaultdict(int)
        window_counts = defaultdict(int)
        window_no_trade_counts = defaultdict(int)
        window_watchlist_only_counts = defaultdict(int)
        prevented_risk_signals = defaultdict(int)

        for pkg in packages:
            candidates = pkg.get("candidates", []) or []
            blocked = pkg.get("blocked_candidates", []) or []
            global_blocks = pkg.get("global_blocks", []) or []
            win = (pkg.get("window_context") or {})
            macro = (pkg.get("macro_context") or {})

            window_name = str(win.get("time_window_name", "unknown"))
            window_counts[window_name] += 1

            is_no_trade = len(candidates) == 0
            is_watchlist_only = bool(win.get("watchlist_only_window", False))

            if is_no_trade:
                no_trade_count += 1
                window_no_trade_counts[window_name] += 1
            if is_watchlist_only:
                watchlist_only_count += 1
                window_watchlist_only_counts[window_name] += 1

            if is_no_trade and blocked:
                blocked_all_count += 1
                reason_counts["all_candidates_blocked"] += 1

            if is_no_trade and safe_float(macro.get("policy_release_urgency_score_max", 0.0)) >= 0.85:
                high_urgency_no_trade_count += 1
                prevented_risk_signals["high_urgency_no_trade"] += 1

            if is_no_trade and (macro.get("macro_event_quorum_pass") is False):
                macro_quorum_fail_no_trade_count += 1
                prevented_risk_signals["macro_quorum_fail_no_trade"] += 1

            for gb in global_blocks:
                global_block_counts[str(gb)] += 1
                reason_counts[f"global_block::{gb}"] += 1

            # Candidate/blocked reason mining
            for b in blocked:
                for br in b.get("block_reasons", []):
                    reason_counts[str(br)] += 1

                # Fill sim prevented bad routing
                fs = b.get("fill_sim_assessment") or {}
                if fs.get("do_not_route_even_in_shadow") is True:
                    prevented_risk_signals["fill_sim_do_not_route_blocked"] += 1
                    for rr in fs.get("do_not_route_reasons", []) or []:
                        prevented_risk_signals[f"do_not_route_reason::{rr}"] += 1

        report = {
            "schema_version": "no_trade_quality_metrics.v1",
            "timestamp_utc": iso_now(),
            "package_count": total,

            "summary": {
                "no_trade_package_count": no_trade_count,
                "no_trade_package_rate": pct(no_trade_count, total),
                "watchlist_only_package_count": watchlist_only_count,
                "watchlist_only_package_rate": pct(watchlist_only_count, total),
                "all_candidates_blocked_count": blocked_all_count,
                "all_candidates_blocked_rate": pct(blocked_all_count, total),
                "high_urgency_no_trade_count": high_urgency_no_trade_count,
                "macro_quorum_fail_no_trade_count": macro_quorum_fail_no_trade_count,
            },

            "window_stats": self._window_stats(
                window_counts=window_counts,
                window_no_trade_counts=window_no_trade_counts,
                window_watchlist_only_counts=window_watchlist_only_counts
            ),

            "global_block_counts": dict(sorted(global_block_counts.items(), key=lambda kv: kv[1], reverse=True)),
            "reason_counts_top": dict(sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)[:50]),
            "prevented_risk_signals": dict(sorted(prevented_risk_signals.items(), key=lambda kv: kv[1], reverse=True)),

            "operator_summary": (
                f"no_trade_rate={pct(no_trade_count, total)} | "
                f"watchlist_only_rate={pct(watchlist_only_count, total)} | "
                f"high_urgency_no_trade={high_urgency_no_trade_count} | "
                f"macro_quorum_fail_no_trade={macro_quorum_fail_no_trade_count}"
            )
        }
        return report

    def _window_stats(self, window_counts, window_no_trade_counts, window_watchlist_only_counts) -> Dict[str, Any]:
        out = {}
        for w, total in sorted(window_counts.items(), key=lambda kv: kv[0]):
            nt = window_no_trade_counts.get(w, 0)
            wl = window_watchlist_only_counts.get(w, 0)
            out[w] = {
                "count": total,
                "no_trade_count": nt,
                "no_trade_rate": pct(nt, total),
                "watchlist_only_count": wl,
                "watchlist_only_rate": pct(wl, total),
            }
        return out


# -----------------------------
# CLI helpers
# -----------------------------
def load_json_file(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_package_files(inputs: List[str]) -> List[Path]:
    files: List[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.glob("*.json")))
    return files


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--inputs", nargs="+", required=True, help="Package JSON files or directories")
    p.add_argument("--output-json", required=False)
    return p.parse_args()


def main():
    args = parse_args()
    package_files = discover_package_files(args.inputs)
    packages = [load_json_file(p) for p in package_files]

    m = NoTradeQualityMetrics()
    report = m.build(packages)

    if args.output_json:
        outp = Path(args.output_json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
