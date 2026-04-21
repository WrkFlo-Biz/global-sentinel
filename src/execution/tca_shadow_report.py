#!/usr/bin/env python3
"""
Global Sentinel V4.5 - TCA Shadow Report

Purpose:
- Generate transaction-cost-analysis-style reports for SHADOW candidates
- Compare package/fill-sim estimates across time windows and strategy types
- Prepare the ground for broker-paper reconciliation later

Inputs:
- One or more package JSON files (from idiosyncratic_package_builder)
- Optional paper trade logs (future extension)
- Optional replay result mappings (future extension)

Outputs:
- summary JSON report
- markdown summary (optional helper)
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.execution.strategy_learning import infer_strategy_family


# -----------------------------
# Helpers
# -----------------------------
def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def mean(xs: List[float]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def pct(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return n / d


# -----------------------------
# TCA Shadow Report
# -----------------------------
class TCAShadowReport:
    def __init__(self):
        pass

    def build_report(
        self,
        packages: List[Dict[str, Any]],
        paper_trade_logs: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        paper_trade_logs = paper_trade_logs or []

        all_candidates = []
        all_blocked = []
        package_meta = []

        for pkg in packages:
            all_candidates.extend(pkg.get("candidates", []) or [])
            all_blocked.extend(pkg.get("blocked_candidates", []) or [])
            package_meta.append(self._extract_pkg_meta(pkg))

        # Candidate-level execution stats
        exec_stats = self._execution_quality_stats(all_candidates)
        blocked_stats = self._blocked_stats(all_blocked)
        window_stats = self._window_breakdown(all_candidates, all_blocked)
        strategy_stats = self._strategy_breakdown(all_candidates, all_blocked)
        symbol_stats = self._symbol_breakdown(all_candidates, all_blocked, top_n=15)
        no_trade_stats = self._no_trade_summary(packages)

        # Placeholder broker-paper reconciliation stats (future ready)
        broker_recon = self._broker_reconciliation_stub(paper_trade_logs)

        report = {
            "schema_version": "tca_shadow_report.v1",
            "timestamp_utc": iso_now(),
            "package_count": len(packages),
            "candidate_count": len(all_candidates),
            "blocked_candidate_count": len(all_blocked),
            "package_meta_summary": self._package_meta_summary(package_meta),

            "execution_quality_stats": exec_stats,
            "blocked_stats": blocked_stats,
            "window_breakdown": window_stats,
            "strategy_breakdown": strategy_stats,
            "symbol_breakdown_top": symbol_stats,
            "no_trade_stats": no_trade_stats,
            "broker_reconciliation": broker_recon,

            "operator_summary": self._operator_summary(
                package_count=len(packages),
                candidate_count=len(all_candidates),
                blocked_count=len(all_blocked),
                exec_stats=exec_stats,
                no_trade_stats=no_trade_stats
            )
        }
        return report

    # -------------------------
    # Internal summaries
    # -------------------------
    def _extract_pkg_meta(self, pkg: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "timestamp_utc": pkg.get("timestamp_utc"),
            "effective_mode": pkg.get("effective_mode"),
            "time_window_name": ((pkg.get("window_context") or {}).get("time_window_name")),
            "watchlist_only_window": ((pkg.get("window_context") or {}).get("watchlist_only_window")),
            "global_blocks": pkg.get("global_blocks") or [],
            "regime_shift_probability": pkg.get("regime_shift_probability"),
            "time_window_adjusted_confidence": pkg.get("time_window_adjusted_confidence"),
            "macro_quorum": ((pkg.get("macro_context") or {}).get("macro_event_quorum_pass")),
        }

    def _package_meta_summary(self, metas: List[Dict[str, Any]]) -> Dict[str, Any]:
        mode_counts = defaultdict(int)
        window_counts = defaultdict(int)
        watchlist_only_count = 0
        macro_quorum_fail_count = 0
        blocked_pkg_count = 0

        regime_ps = []
        confs = []

        for m in metas:
            mode_counts[str(m.get("effective_mode", "UNKNOWN"))] += 1
            window_counts[str(m.get("time_window_name", "unknown"))] += 1
            if m.get("watchlist_only_window") is True:
                watchlist_only_count += 1
            if m.get("macro_quorum") is False:
                macro_quorum_fail_count += 1
            if m.get("global_blocks"):
                blocked_pkg_count += 1

            if m.get("regime_shift_probability") is not None:
                regime_ps.append(safe_float(m.get("regime_shift_probability")))
            if m.get("time_window_adjusted_confidence") is not None:
                confs.append(safe_float(m.get("time_window_adjusted_confidence")))

        return {
            "mode_counts": dict(mode_counts),
            "window_counts": dict(window_counts),
            "watchlist_only_package_count": watchlist_only_count,
            "macro_quorum_fail_package_count": macro_quorum_fail_count,
            "packages_with_global_blocks": blocked_pkg_count,
            "avg_regime_shift_probability": mean(regime_ps),
            "avg_time_window_adjusted_confidence": mean(confs),
        }

    def _execution_quality_stats(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        slippage = []
        fill_feas = []
        partial_fill = []
        fill_completion = []
        reject_risk = []
        quality_counts = defaultdict(int)
        do_not_route_count = 0

        for c in candidates:
            fs = c.get("fill_sim_assessment") or {}
            quality = str(fs.get("execution_quality_class", "unknown"))
            quality_counts[quality] += 1

            if fs.get("do_not_route_even_in_shadow") is True:
                do_not_route_count += 1

            if fs.get("expected_slippage_bps") is not None:
                slippage.append(safe_float(fs.get("expected_slippage_bps")))
            if fs.get("fill_feasibility_score") is not None:
                fill_feas.append(safe_float(fs.get("fill_feasibility_score")))
            if fs.get("partial_fill_probability") is not None:
                partial_fill.append(safe_float(fs.get("partial_fill_probability")))
            if fs.get("fill_completion_probability") is not None:
                fill_completion.append(safe_float(fs.get("fill_completion_probability")))
            if fs.get("reject_risk_probability") is not None:
                reject_risk.append(safe_float(fs.get("reject_risk_probability")))

        n = len(candidates)
        return {
            "candidate_count": n,
            "avg_expected_slippage_bps": mean(slippage),
            "avg_fill_feasibility_score": mean(fill_feas),
            "avg_partial_fill_probability": mean(partial_fill),
            "avg_fill_completion_probability": mean(fill_completion),
            "avg_reject_risk_probability": mean(reject_risk),
            "execution_quality_counts": dict(quality_counts),
            "do_not_route_even_in_shadow_count": do_not_route_count,
            "do_not_route_even_in_shadow_rate": pct(do_not_route_count, n),
        }

    def _blocked_stats(self, blocked: List[Dict[str, Any]]) -> Dict[str, Any]:
        reason_counts = defaultdict(int)
        symbol_counts = defaultdict(int)

        for b in blocked:
            symbol_counts[str(b.get("symbol", "UNKNOWN"))] += 1
            for r in b.get("block_reasons", []) or [b.get("reason", "unknown")]:
                reason_counts[str(r)] += 1

        return {
            "blocked_candidate_count": len(blocked),
            "block_reason_counts": dict(sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)),
            "blocked_symbol_counts_top": dict(sorted(symbol_counts.items(), key=lambda kv: kv[1], reverse=True)[:15]),
        }

    def _window_breakdown(self, candidates: List[Dict[str, Any]], blocked: List[Dict[str, Any]]) -> Dict[str, Any]:
        stats = defaultdict(lambda: {
            "candidate_count": 0,
            "blocked_count": 0,
            "avg_confidence": [],
            "avg_size_mult": [],
            "avg_expected_slippage_bps": [],
            "quality_counts": defaultdict(int)
        })

        for c in candidates:
            w = str(c.get("window_name", "unknown"))
            stats[w]["candidate_count"] += 1
            stats[w]["avg_confidence"].append(safe_float(c.get("confidence_score")))
            stats[w]["avg_size_mult"].append(safe_float(c.get("size_multiplier_suggestion")))
            fs = c.get("fill_sim_assessment") or {}
            if fs.get("expected_slippage_bps") is not None:
                stats[w]["avg_expected_slippage_bps"].append(safe_float(fs.get("expected_slippage_bps")))
            q = str(fs.get("execution_quality_class", "unknown"))
            stats[w]["quality_counts"][q] += 1

        for b in blocked:
            w = str(b.get("window_name", "unknown"))
            stats[w]["blocked_count"] += 1

        out = {}
        for w, s in stats.items():
            out[w] = {
                "candidate_count": s["candidate_count"],
                "blocked_count": s["blocked_count"],
                "avg_confidence": mean(s["avg_confidence"]),
                "avg_size_multiplier_suggestion": mean(s["avg_size_mult"]),
                "avg_expected_slippage_bps": mean(s["avg_expected_slippage_bps"]),
                "execution_quality_counts": dict(s["quality_counts"]),
            }
        return out

    def _strategy_context(self, row: Dict[str, Any]) -> Dict[str, Any]:
        metadata = row.get("metadata") or {}
        strategy_style = row.get("strategy_style") or metadata.get("strategy_style")
        strategy = (
            row.get("strategy")
            or metadata.get("strategy")
            or row.get("template_key")
            or strategy_style
            or "unknown"
        )
        strategy_family = (
            row.get("strategy_family")
            or metadata.get("strategy_family")
            or infer_strategy_family(
                {
                    "strategy": strategy,
                    "strategy_style": strategy_style,
                    "holding_period": row.get("holding_period") or metadata.get("holding_period"),
                    "time_window_name": row.get("window_name") or metadata.get("time_window_name"),
                },
                default_family="unknown",
            )
        )
        learning_adjusted = row.get("learning_adjusted")
        if learning_adjusted is None:
            learning_adjusted = metadata.get("learning_adjusted", False)

        return {
            "strategy": str(strategy),
            "strategy_style": strategy_style,
            "strategy_family": strategy_family,
            "underlying_strategy": row.get("underlying_strategy") or metadata.get("underlying_strategy"),
            "learning_adjusted": bool(learning_adjusted),
            "learning_adjustment_detail": (
                row.get("learning_adjustment_detail")
                or metadata.get("learning_adjustment_detail")
            ),
        }

    def _strategy_breakdown(self, candidates: List[Dict[str, Any]], blocked: List[Dict[str, Any]]) -> Dict[str, Any]:
        stats = defaultdict(lambda: {
            "strategy": None,
            "candidate_count": 0,
            "blocked_count": 0,
            "avg_confidence": [],
            "avg_expected_slippage_bps": [],
            "avg_reject_risk_probability": [],
            "strategy_family_counts": defaultdict(int),
            "strategy_style_counts": defaultdict(int),
            "underlying_strategy_counts": defaultdict(int),
            "learning_adjusted_candidate_count": 0,
            "learning_adjusted_blocked_count": 0,
            "learning_adjustment_detail_counts": defaultdict(int),
        })

        for c in candidates:
            strategy_context = self._strategy_context(c)
            k = strategy_context["strategy"]
            stats[k]["strategy"] = k
            stats[k]["candidate_count"] += 1
            stats[k]["avg_confidence"].append(safe_float(c.get("confidence_score")))
            fs = c.get("fill_sim_assessment") or {}
            if fs.get("expected_slippage_bps") is not None:
                stats[k]["avg_expected_slippage_bps"].append(safe_float(fs.get("expected_slippage_bps")))
            if fs.get("reject_risk_probability") is not None:
                stats[k]["avg_reject_risk_probability"].append(safe_float(fs.get("reject_risk_probability")))
            if strategy_context.get("strategy_family"):
                stats[k]["strategy_family_counts"][str(strategy_context["strategy_family"])] += 1
            if strategy_context.get("strategy_style"):
                stats[k]["strategy_style_counts"][str(strategy_context["strategy_style"])] += 1
            if strategy_context.get("underlying_strategy"):
                stats[k]["underlying_strategy_counts"][str(strategy_context["underlying_strategy"])] += 1
            if strategy_context.get("learning_adjusted"):
                stats[k]["learning_adjusted_candidate_count"] += 1
            learning_detail = strategy_context.get("learning_adjustment_detail")
            if learning_detail:
                stats[k]["learning_adjustment_detail_counts"][json.dumps(learning_detail, sort_keys=True)] += 1

        for b in blocked:
            strategy_context = self._strategy_context(b)
            k = strategy_context["strategy"]
            stats[k]["strategy"] = k
            stats[k]["blocked_count"] += 1
            if strategy_context.get("strategy_family"):
                stats[k]["strategy_family_counts"][str(strategy_context["strategy_family"])] += 1
            if strategy_context.get("strategy_style"):
                stats[k]["strategy_style_counts"][str(strategy_context["strategy_style"])] += 1
            if strategy_context.get("underlying_strategy"):
                stats[k]["underlying_strategy_counts"][str(strategy_context["underlying_strategy"])] += 1
            if strategy_context.get("learning_adjusted"):
                stats[k]["learning_adjusted_blocked_count"] += 1
            learning_detail = strategy_context.get("learning_adjustment_detail")
            if learning_detail:
                stats[k]["learning_adjustment_detail_counts"][json.dumps(learning_detail, sort_keys=True)] += 1

        out = {}
        for k, s in stats.items():
            out[k] = {
                "strategy": s["strategy"] or k,
                "candidate_count": s["candidate_count"],
                "blocked_count": s["blocked_count"],
                "avg_confidence": mean(s["avg_confidence"]),
                "avg_expected_slippage_bps": mean(s["avg_expected_slippage_bps"]),
                "avg_reject_risk_probability": mean(s["avg_reject_risk_probability"]),
                "strategy_family_counts": dict(sorted(s["strategy_family_counts"].items(), key=lambda kv: kv[1], reverse=True)),
                "strategy_style_counts": dict(sorted(s["strategy_style_counts"].items(), key=lambda kv: kv[1], reverse=True)),
                "underlying_strategy_counts": dict(sorted(s["underlying_strategy_counts"].items(), key=lambda kv: kv[1], reverse=True)),
                "learning_adjusted_candidate_count": s["learning_adjusted_candidate_count"],
                "learning_adjusted_blocked_count": s["learning_adjusted_blocked_count"],
                "learning_adjustment_detail_counts": dict(sorted(s["learning_adjustment_detail_counts"].items(), key=lambda kv: kv[1], reverse=True)),
            }
        return dict(sorted(out.items(), key=lambda kv: kv[1]["candidate_count"], reverse=True))

    def _symbol_breakdown(self, candidates: List[Dict[str, Any]], blocked: List[Dict[str, Any]], top_n: int = 15) -> Dict[str, Any]:
        stats = defaultdict(lambda: {
            "candidate_count": 0,
            "blocked_count": 0,
            "avg_confidence": [],
            "avg_expected_slippage_bps": [],
            "avg_fill_feasibility": [],
        })

        for c in candidates:
            sym = str(c.get("symbol", "UNKNOWN"))
            stats[sym]["candidate_count"] += 1
            stats[sym]["avg_confidence"].append(safe_float(c.get("confidence_score")))
            fs = c.get("fill_sim_assessment") or {}
            if fs.get("expected_slippage_bps") is not None:
                stats[sym]["avg_expected_slippage_bps"].append(safe_float(fs.get("expected_slippage_bps")))
            if fs.get("fill_feasibility_score") is not None:
                stats[sym]["avg_fill_feasibility"].append(safe_float(fs.get("fill_feasibility_score")))

        for b in blocked:
            sym = str(b.get("symbol", "UNKNOWN"))
            stats[sym]["blocked_count"] += 1

        ranked = sorted(
            stats.items(),
            key=lambda kv: (kv[1]["candidate_count"] + kv[1]["blocked_count"]),
            reverse=True
        )[:top_n]

        out = {}
        for sym, s in ranked:
            out[sym] = {
                "candidate_count": s["candidate_count"],
                "blocked_count": s["blocked_count"],
                "avg_confidence": mean(s["avg_confidence"]),
                "avg_expected_slippage_bps": mean(s["avg_expected_slippage_bps"]),
                "avg_fill_feasibility": mean(s["avg_fill_feasibility"]),
            }
        return out

    def _no_trade_summary(self, packages: List[Dict[str, Any]]) -> Dict[str, Any]:
        no_trade_count = 0
        watchlist_only_count = 0
        reasons = defaultdict(int)

        for p in packages:
            candidates = p.get("candidates", []) or []
            blocked = p.get("blocked_candidates", []) or []
            global_blocks = p.get("global_blocks", []) or []
            window_ctx = p.get("window_context", {}) or {}

            if len(candidates) == 0:
                no_trade_count += 1

            if window_ctx.get("watchlist_only_window") is True:
                watchlist_only_count += 1

            for r in global_blocks:
                reasons[str(r)] += 1

            if len(candidates) == 0 and len(blocked) > 0:
                reasons["all_candidates_blocked"] += 1

        total = len(packages)
        return {
            "package_count": total,
            "no_trade_package_count": no_trade_count,
            "no_trade_package_rate": pct(no_trade_count, total),
            "watchlist_only_package_count": watchlist_only_count,
            "watchlist_only_package_rate": pct(watchlist_only_count, total),
            "no_trade_reason_counts": dict(sorted(reasons.items(), key=lambda kv: kv[1], reverse=True))
        }

    def _broker_reconciliation_stub(self, paper_trade_logs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Placeholder until you wire actual Alpaca/Tradier paper logs.
        """
        return {
            "paper_log_count": len(paper_trade_logs),
            "status": "not_wired" if not paper_trade_logs else "basic_stub_only",
            "planned_metrics": [
                "expected_vs_actual_fill_slippage_bps",
                "partial_fill_rate",
                "cancel_replace_rate",
                "reject_rate_by_reason",
                "broker_vs_intended_reconciliation_delta"
            ]
        }

    def _operator_summary(
        self,
        package_count: int,
        candidate_count: int,
        blocked_count: int,
        exec_stats: Dict[str, Any],
        no_trade_stats: Dict[str, Any]
    ) -> str:
        return (
            f"TCA shadow summary | packages={package_count} | candidates={candidate_count} | blocked={blocked_count} | "
            f"avg_slippage_bps={exec_stats.get('avg_expected_slippage_bps')} | "
            f"avg_fill_feasibility={exec_stats.get('avg_fill_feasibility_score')} | "
            f"no_trade_rate={no_trade_stats.get('no_trade_package_rate')}"
        )


# -----------------------------
# IO helpers
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


def render_markdown_summary(report: Dict[str, Any]) -> str:
    lines = []
    lines.append("# TCA Shadow Report")
    lines.append("")
    lines.append(f"- Generated: {report.get('timestamp_utc')}")
    lines.append(f"- Packages: {report.get('package_count')}")
    lines.append(f"- Candidates: {report.get('candidate_count')}")
    lines.append(f"- Blocked Candidates: {report.get('blocked_candidate_count')}")
    lines.append("")
    lines.append("## Execution Quality")
    eq = report.get("execution_quality_stats", {})
    lines.append(f"- Avg expected slippage (bps): {eq.get('avg_expected_slippage_bps')}")
    lines.append(f"- Avg fill feasibility: {eq.get('avg_fill_feasibility_score')}")
    lines.append(f"- Avg partial fill probability: {eq.get('avg_partial_fill_probability')}")
    lines.append(f"- Avg fill completion probability: {eq.get('avg_fill_completion_probability')}")
    lines.append(f"- Avg reject risk probability: {eq.get('avg_reject_risk_probability')}")
    lines.append(f"- Do-not-route (shadow) rate: {eq.get('do_not_route_even_in_shadow_rate')}")
    lines.append("")
    lines.append("## No-Trade Quality")
    nt = report.get("no_trade_stats", {})
    lines.append(f"- No-trade package rate: {nt.get('no_trade_package_rate')}")
    lines.append(f"- Watchlist-only package rate: {nt.get('watchlist_only_package_rate')}")
    lines.append("")
    lines.append("## Top Block Reasons")
    bs = report.get("blocked_stats", {}).get("block_reason_counts", {})
    for i, (k, v) in enumerate(bs.items()):
        if i >= 10:
            break
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Top Strategy Buckets")
    strategy_breakdown = report.get("strategy_breakdown", {})
    for i, (strategy, stats) in enumerate(strategy_breakdown.items()):
        if i >= 10:
            break
        family = ", ".join(
            f"{name}:{count}" for name, count in (stats.get("strategy_family_counts") or {}).items()
        ) or "n/a"
        underlying = ", ".join(
            f"{name}:{count}" for name, count in (stats.get("underlying_strategy_counts") or {}).items()
        ) or "n/a"
        learning_adjusted_total = (
            safe_int(stats.get("learning_adjusted_candidate_count"))
            + safe_int(stats.get("learning_adjusted_blocked_count"))
        )
        lines.append(
            f"- {strategy}: candidates={stats.get('candidate_count')} blocked={stats.get('blocked_count')} "
            f"families={family} underlying={underlying} learning_adjusted={learning_adjusted_total}"
        )
    if not strategy_breakdown:
        lines.append("- None")
    lines.append("")
    lines.append("## Operator Summary")
    lines.append(f"- {report.get('operator_summary')}")
    return "\n".join(lines)


# -----------------------------
# CLI
# -----------------------------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--inputs", nargs="+", required=True, help="Package JSON files or directories")
    p.add_argument("--output-json", required=False)
    p.add_argument("--output-md", required=False)
    return p.parse_args()


def main():
    args = parse_args()
    package_files = discover_package_files(args.inputs)
    packages = [load_json_file(p) for p in package_files]

    reporter = TCAShadowReport()
    report = reporter.build_report(packages)

    if args.output_json:
        outp = Path(args.output_json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, indent=2))

    if args.output_md:
        md = render_markdown_summary(report)
        outm = Path(args.output_md)
        outm.parent.mkdir(parents=True, exist_ok=True)
        outm.write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
