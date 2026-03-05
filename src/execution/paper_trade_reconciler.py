#!/usr/bin/env python3
"""
Global Sentinel V4.8 - Paper Trade Reconciler

Purpose:
- Reconcile shadow package candidates against actual broker order state + fills
- Enrich candidates with intent_id / client_order_id / broker_order_id from
  OrderIntentRegistry + router_order_bindings before matching
- Produce comparison rows with match confidence + delta analysis
- Output JSON + Markdown for daily ops review

Inputs:
- Package JSON files (candidates)
- Broker orders (from adapter or manual export)
- Broker trades/fills
- logs/execution/order_intents.jsonl (enrichment)
- logs/execution/router_order_bindings.jsonl (enrichment)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


class PaperTradeReconciler:
    """
    Reconciles package candidates against broker orders/fills.
    Enriches candidates from OrderIntentRegistry + router binding logs
    before broker matching for higher accuracy.
    """

    def reconcile(
        self,
        packages: List[Dict[str, Any]],
        broker_orders: List[Dict[str, Any]],
        broker_trades: List[Dict[str, Any]],
        repo_root: Optional[Path] = None,
    ) -> Dict[str, Any]:
        # Enrich candidates with execution linkage (intent_id, client_order_id, broker_order_id)
        package_candidates = self._enrich_candidates_with_execution_linkage(packages, repo_root=repo_root)

        # Index broker orders for matching
        bo_by_id = {str(o.get("order_id")): o for o in broker_orders if o.get("order_id")}
        bo_by_client_id = {str(o.get("client_order_id")): o for o in broker_orders if o.get("client_order_id")}
        bo_by_symbol = defaultdict(list)
        for o in broker_orders:
            if o.get("symbol"):
                bo_by_symbol[str(o["symbol"])].append(o)

        # Index broker trades by order_id
        trades_by_order = defaultdict(list)
        for t in broker_trades:
            oid = t.get("order_id")
            if oid:
                trades_by_order[str(oid)].append(t)

        comparisons = []
        matched_broker_ids = set()

        for cand in package_candidates:
            comp = self._match_candidate(cand, bo_by_id, bo_by_client_id, bo_by_symbol, trades_by_order, matched_broker_ids)
            comparisons.append(comp)

        # Unmatched broker orders
        unmatched_broker = []
        for o in broker_orders:
            oid = str(o.get("order_id", ""))
            if oid and oid not in matched_broker_ids:
                unmatched_broker.append({
                    "broker_order_id": oid,
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "status": o.get("status"),
                    "client_order_id": o.get("client_order_id"),
                })

        # Summary
        match_conf_counts = defaultdict(int)
        for c in comparisons:
            match_conf_counts[str(c.get("match_confidence", "none"))] += 1

        total = len(comparisons)
        high_count = match_conf_counts.get("high", 0)
        medium_count = match_conf_counts.get("medium", 0)

        return {
            "schema_version": "paper_trade_reconciliation.v1",
            "timestamp_utc": iso_now(),
            "summary": {
                "candidate_count": total,
                "broker_order_count": len(broker_orders),
                "broker_trade_count": len(broker_trades),
                "match_rate": ((high_count + medium_count) / total) if total else None,
                "match_confidence_counts": dict(match_conf_counts),
                "unmatched_broker_order_count": len(unmatched_broker),
                "actual_rejected_count": sum(1 for c in comparisons if c.get("broker_status") == "rejected"),
                "sim_do_not_route_but_broker_found_count": sum(
                    1 for c in comparisons
                    if c.get("sim_do_not_route") and c.get("match_confidence") in {"high", "medium"}
                ),
            },
            "comparisons": comparisons,
            "unmatched_broker_orders": unmatched_broker,
        }

    def _match_candidate(
        self,
        cand: Dict[str, Any],
        bo_by_id: Dict[str, Dict[str, Any]],
        bo_by_client_id: Dict[str, Dict[str, Any]],
        bo_by_symbol: Dict[str, List[Dict[str, Any]]],
        trades_by_order: Dict[str, List[Dict[str, Any]]],
        matched_broker_ids: set,
    ) -> Dict[str, Any]:
        symbol = str(cand.get("symbol", ""))
        fs = cand.get("fill_sim_assessment") or {}
        sim_dnr = bool(fs.get("do_not_route_even_in_shadow"))

        comp = {
            "symbol": symbol,
            "candidate_id": cand.get("candidate_id"),
            "package_id": cand.get("package_id"),
            "intent_id": cand.get("intent_id"),
            "client_order_id": cand.get("client_order_id"),
            "strategy_style": cand.get("strategy_style"),
            "template_key": cand.get("template_key"),
            "direction": cand.get("direction"),
            "sim_confidence": cand.get("confidence_score"),
            "sim_slippage_bps": fs.get("expected_slippage_bps"),
            "sim_fill_feasibility": fs.get("fill_feasibility_score"),
            "sim_reject_risk": fs.get("reject_risk_probability"),
            "sim_quality_class": fs.get("execution_quality_class"),
            "sim_do_not_route": sim_dnr,
            "match_confidence": "none",
            "broker_order_id": None,
            "broker_status": None,
            "broker_filled_qty": None,
            "broker_avg_fill_price": None,
            "broker_trade_count": 0,
            "actual_slippage_bps": None,
            "delta_notes": [],
        }

        broker_order = None

        # Priority 1: broker_order_id from enrichment
        if cand.get("broker_order_id"):
            broker_order = bo_by_id.get(str(cand["broker_order_id"]))
            if broker_order:
                comp["match_confidence"] = "high"

        # Priority 2: client_order_id from enrichment
        if broker_order is None and cand.get("client_order_id"):
            broker_order = bo_by_client_id.get(str(cand["client_order_id"]))
            if broker_order:
                comp["match_confidence"] = "high"

        # Priority 3: symbol + side fallback
        if broker_order is None:
            direction = str(cand.get("direction", "")).lower()
            expected_side = "sell" if ("short" in direction or "bearish" in direction) else "buy"
            candidates_for_symbol = bo_by_symbol.get(symbol, [])
            for bo in candidates_for_symbol:
                bo_id = str(bo.get("order_id", ""))
                if bo_id in matched_broker_ids:
                    continue
                if str(bo.get("side", "")).lower() == expected_side:
                    broker_order = bo
                    comp["match_confidence"] = "medium"
                    break
            if broker_order is None and candidates_for_symbol:
                # any symbol match
                for bo in candidates_for_symbol:
                    bo_id = str(bo.get("order_id", ""))
                    if bo_id in matched_broker_ids:
                        continue
                    broker_order = bo
                    comp["match_confidence"] = "low"
                    break

        if broker_order:
            bo_id = str(broker_order.get("order_id", ""))
            matched_broker_ids.add(bo_id)
            comp["broker_order_id"] = bo_id
            comp["broker_status"] = broker_order.get("status")
            comp["broker_filled_qty"] = broker_order.get("filled_qty")
            comp["broker_avg_fill_price"] = broker_order.get("filled_avg_price")

            trades = trades_by_order.get(bo_id, [])
            comp["broker_trade_count"] = len(trades)

            # Compute actual slippage if decision price available
            decision_price = safe_float((cand.get("price_hints") or {}).get("decision_price"), 0.0)
            fill_price = safe_float(broker_order.get("filled_avg_price"), 0.0)
            if decision_price > 0 and fill_price > 0:
                actual_slip = abs(fill_price - decision_price) / decision_price * 10000.0
                comp["actual_slippage_bps"] = round(actual_slip, 2)

            # Delta notes
            if sim_dnr:
                comp["delta_notes"].append("sim_said_do_not_route_but_broker_found")
            if comp["broker_status"] == "rejected":
                comp["delta_notes"].append("broker_rejected")
            sim_slip = safe_float(fs.get("expected_slippage_bps"), 0.0)
            if comp["actual_slippage_bps"] is not None and sim_slip > 0:
                ratio = comp["actual_slippage_bps"] / sim_slip if sim_slip else 999
                if ratio > 2.0:
                    comp["delta_notes"].append(f"actual_slippage_2x_sim:actual={comp['actual_slippage_bps']}:sim={sim_slip}")
                elif ratio < 0.5:
                    comp["delta_notes"].append(f"actual_slippage_much_better_than_sim:actual={comp['actual_slippage_bps']}:sim={sim_slip}")
        else:
            if not sim_dnr:
                comp["delta_notes"].append("no_broker_match_found")

        return comp

    # -------------------------
    # Execution linkage enrichment
    # -------------------------
    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except Exception:
                continue
        return rows

    def _load_latest_order_intents(self, repo_root: Optional[Path]) -> Dict[str, Dict[str, Any]]:
        if repo_root is None:
            return {}
        path = repo_root / "logs" / "execution" / "order_intents.jsonl"
        rows = self._read_jsonl(path)
        latest = {}
        for r in rows:
            iid = r.get("intent_id")
            if iid:
                latest[str(iid)] = r
        return latest

    def _load_router_bindings(self, repo_root: Optional[Path]) -> List[Dict[str, Any]]:
        if repo_root is None:
            return []
        path = repo_root / "logs" / "execution" / "router_order_bindings.jsonl"
        return self._read_jsonl(path)

    def _index_router_bindings(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        idx = {
            "by_package_candidate": {},
            "by_intent_id": {},
            "by_client_order_id": {},
            "by_broker_order_id": {},
            "by_symbol": defaultdict(list),
        }
        for r in rows:
            package_id = r.get("package_id")
            candidate_id = r.get("candidate_id")
            intent_id = r.get("intent_id")
            client_order_id = r.get("client_order_id")
            broker_order_id = r.get("broker_order_id")
            symbol = r.get("symbol")

            if package_id and candidate_id:
                idx["by_package_candidate"][f"{package_id}|{candidate_id}"] = r
            if intent_id:
                idx["by_intent_id"][str(intent_id)] = r
            if client_order_id:
                idx["by_client_order_id"][str(client_order_id)] = r
            if broker_order_id:
                idx["by_broker_order_id"][str(broker_order_id)] = r
            if symbol:
                idx["by_symbol"][str(symbol)].append(r)
        return idx

    def _enrich_candidates_with_execution_linkage(
        self,
        packages: List[Dict[str, Any]],
        repo_root: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        latest_intents = self._load_latest_order_intents(repo_root)
        intent_rows = list(latest_intents.values())
        router_idx = self._index_router_bindings(self._load_router_bindings(repo_root))

        # build intent index by package+candidate
        intents_by_pkg_cand = {}
        for it in intent_rows:
            pkg = it.get("package_id")
            cand = it.get("candidate_id")
            if pkg and cand:
                intents_by_pkg_cand[f"{pkg}|{cand}"] = it

        rows = []
        for p in packages:
            package_id = p.get("package_id")
            for c in p.get("candidates", []) or []:
                row = dict(c)
                row["_package_timestamp_utc"] = p.get("timestamp_utc")
                row["_package_type"] = p.get("package_type")
                row["package_id"] = package_id or p.get("package_id")

                # Ensure candidate_id exists
                if not row.get("candidate_id") and row.get("symbol"):
                    seed = f"{row.get('package_id')}|{row.get('symbol')}|{row.get('template_key')}|{row.get('strategy_style')}|{row.get('window_name')}"
                    row["candidate_id"] = f"cand-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:14]}"

                has_linkage = any([
                    row.get("intent_id"),
                    row.get("client_order_id"),
                    row.get("broker_order_id"),
                ])

                if not has_linkage:
                    key = f"{row.get('package_id')}|{row.get('candidate_id')}" if row.get("package_id") and row.get("candidate_id") else None

                    binding = router_idx["by_package_candidate"].get(key) if key else None
                    if binding:
                        row["intent_id"] = row.get("intent_id") or binding.get("intent_id")
                        row["client_order_id"] = row.get("client_order_id") or binding.get("client_order_id")
                        row["broker_order_id"] = row.get("broker_order_id") or binding.get("broker_order_id")

                    # fallback to latest intent registry
                    if key and (not row.get("intent_id") or not row.get("client_order_id")):
                        it = intents_by_pkg_cand.get(key)
                        if it:
                            row["intent_id"] = row.get("intent_id") or it.get("intent_id")
                            row["client_order_id"] = row.get("client_order_id") or it.get("client_order_id")
                            row["broker_order_id"] = row.get("broker_order_id") or ((it.get("broker_binding") or {}).get("broker_order_id"))

                rows.append(row)
        return rows


def render_markdown(rep: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Paper Trade Reconciliation Report")
    lines.append("")
    lines.append(f"- Generated: {rep.get('timestamp_utc')}")
    lines.append("")

    s = rep.get("summary", {})
    lines.append("## Summary")
    for k, v in s.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    comps = rep.get("comparisons", [])
    if comps:
        lines.append("## Comparisons (Top 20)")
        lines.append("")
        lines.append("| Symbol | Match | Broker Status | Sim Quality | Actual Slip (bps) | Sim Slip (bps) | Delta Notes |")
        lines.append("|---|---|---|---|---:|---:|---|")
        for c in comps[:20]:
            delta = "; ".join(c.get("delta_notes", [])[:3])
            lines.append(
                f"| {c.get('symbol')} | {c.get('match_confidence')} | {c.get('broker_status')} | "
                f"{c.get('sim_quality_class')} | {c.get('actual_slippage_bps')} | {c.get('sim_slippage_bps')} | {delta} |"
            )
    lines.append("")

    unmatched = rep.get("unmatched_broker_orders", [])
    if unmatched:
        lines.append("## Unmatched Broker Orders")
        for u in unmatched[:10]:
            lines.append(f"- {u.get('symbol')} | {u.get('broker_order_id')} | {u.get('status')}")
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--packages-dir", default=None, help="Directory containing package JSON files")
    p.add_argument("--broker-orders-json", default=None, help="Broker orders JSON file")
    p.add_argument("--broker-trades-json", default=None, help="Broker trades JSON file")
    p.add_argument("--output-json", default=None)
    p.add_argument("--output-md", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()

    packages = []
    if args.packages_dir:
        pkg_dir = Path(args.packages_dir)
        for f in sorted(pkg_dir.glob("*.json")):
            try:
                packages.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue

    broker_orders = []
    if args.broker_orders_json:
        raw = json.loads(Path(args.broker_orders_json).read_text(encoding="utf-8"))
        broker_orders = raw if isinstance(raw, list) else raw.get("orders", [])

    broker_trades = []
    if args.broker_trades_json:
        raw = json.loads(Path(args.broker_trades_json).read_text(encoding="utf-8"))
        broker_trades = raw if isinstance(raw, list) else raw.get("trades", [])

    reconciler = PaperTradeReconciler()
    rep = reconciler.reconcile(packages, broker_orders, broker_trades, repo_root=repo_root)

    if args.output_json:
        p = Path(args.output_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    else:
        print(json.dumps(rep, indent=2))

    if args.output_md:
        p = Path(args.output_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(rep), encoding="utf-8")


if __name__ == "__main__":
    main()
