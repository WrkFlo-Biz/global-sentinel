#!/usr/bin/env python3
"""
Global Sentinel — Conversational Market Query (LLM-powered)

Takes a natural language question about the market, gathers context
from ALL quantum_feed JSON files, and routes the request through the
GS-side Foundry client boundary.

Usage:
  python3 market_query.py "What is the best trade setup right now?"
  python3 market_query.py "Is NVDA overvalued?"
  python3 market_query.py "Which sectors are rotating?"
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[2]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.control_state_snapshot import read_control_state_snapshot
from src.inference.foundry_client import FoundryResponse, send_request


def gather_all_context() -> Dict[str, Any]:
    """Load ALL quantum_feed JSON files as context."""
    feed_dir = REPO_ROOT / "data" / "quantum_feed"
    context = {}
    if not feed_dir.exists():
        return context
    for path in sorted(feed_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            context[path.stem] = _truncate(data)
        except Exception:
            pass
    return context


def _truncate(obj: Any, max_list: int = 10, max_depth: int = 3, depth: int = 0) -> Any:
    """Truncate deeply nested or large structures to fit token budget."""
    if depth >= max_depth:
        if isinstance(obj, (dict, list)):
            return f"[truncated at depth {depth}]"
        return obj
    if isinstance(obj, dict):
        return {key: _truncate(value, max_list, max_depth, depth + 1) for key, value in list(obj.items())[:30]}
    if isinstance(obj, list):
        truncated = [_truncate(item, max_list, max_depth, depth + 1) for item in obj[:max_list]]
        if len(obj) > max_list:
            truncated.append(f"... and {len(obj) - max_list} more items")
        return truncated
    return obj


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_operating_context(context: Dict[str, Any]) -> Dict[str, Any]:
    hmm_regime = context.get("hmm_regime") if isinstance(context.get("hmm_regime"), dict) else {}
    latest_signal = context.get("latest_signal") if isinstance(context.get("latest_signal"), dict) else {}
    control_snapshot = read_control_state_snapshot(REPO_ROOT)
    return {
        "mode": (
            hmm_regime.get("operating_mode")
            or hmm_regime.get("mode")
            or latest_signal.get("operating_mode")
            or latest_signal.get("mode")
            or "NORMAL"
        ),
        "regime_shift_probability": _coerce_float(
            hmm_regime.get("regime_shift_probability")
            or latest_signal.get("regime_shift_probability")
            or 0.0
        ),
        "manual_veto": control_snapshot["manual_veto"],
        "kill_switch": control_snapshot["kill_switch"],
        "execution_sensitivity": "research_only",
    }


def call_llm(question: str, context: Dict[str, Any], trace_context: Dict[str, str]) -> FoundryResponse:
    """Call Foundry/orchestrator with the question and market context."""
    context_str = json.dumps(context, indent=1, default=str)
    if len(context_str) > 50000:
        context_str = context_str[:50000] + "\n... [context truncated]"

    system_prompt = """You are Global Sentinel, an elite AI trading strategist with access to real-time market intelligence feeds.

You have access to the following data sources:
- technical_analysis: RSI, MACD, Bollinger Bands, VWAP, SMA crossovers, support/resistance, ATR, technical scores
- fundamental_scores: P/E, P/S, P/B, EV/EBITDA, FCF yield, DCF valuation, value scores
- social_trending: StockTwits and Reddit trending tickers, mention momentum
- news_impact: News headline impact scores per ticker
- sector_rotation: Sector relative performance vs SPY, rotation signals
- hmm_regime: Hidden Markov Model market regime detection
- latest_signal: Most recent quantum-optimized trading signal
- price_forecasts: ML price predictions
- strategy_recommendations: Current strategy recommendations
- optimal_portfolio: Optimized portfolio weights
- ensemble_signals: Ensemble of all signal sources

When answering:
1. Be specific with ticker symbols, price levels, and scores
2. Cite which data sources support your thesis
3. Always mention risk factors and conviction level (1-10)
4. Give actionable entry/exit levels when discussing trade setups
5. Be concise but thorough — prioritize signal over noise"""

    return send_request(
        intent_type="market_query",
        target_role="planner",
        operating_context=build_operating_context(context),
        latency_class="interactive",
        trace_context=trace_context,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"MARKET DATA CONTEXT:\n{context_str}\n\nQUESTION: {question}"},
        ],
    )


def query(question: str) -> Dict[str, Any]:
    """Run a market query and return structured result."""
    now = datetime.now(timezone.utc)
    ts = now.isoformat()
    query_id = now.strftime("%Y%m%d%H%M%S")
    context = gather_all_context()
    output_path = REPO_ROOT / "data" / "quantum_feed" / "last_market_query.json"
    trace_context = {
        "trace_id": f"market-query-{query_id}",
        "intent_id": f"market-query-{query_id}",
        "package_id": f"market-query-{query_id}",
        "report_path": str(output_path),
    }

    try:
        response = call_llm(question, context, trace_context)
        answer = response.output
        route = response.route
        trace_id = response.trace_id
        policy_annotations = response.policy_annotations
    except Exception as exc:
        answer = f"LLM error: {exc}"
        route = {}
        trace_id = trace_context["trace_id"]
        policy_annotations = {"error": str(exc)}

    result = {
        "source": "market_query",
        "timestamp_utc": ts,
        "question": question,
        "answer": answer,
        "context_sources": list(context.keys()),
        "model": route.get("model"),
        "route": route,
        "trace_id": trace_id,
        "policy_annotations": policy_annotations,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 market_query.py \"Your question about the market\"")
        print("\nExamples:")
        print("  python3 market_query.py \"What is the best trade setup right now?\"")
        print("  python3 market_query.py \"Is NVDA overvalued based on fundamentals?\"")
        print("  python3 market_query.py \"Which sectors show rotation signals?\"")
        print("  python3 market_query.py \"Summarize all current signals\"")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    print(f"Querying Global Sentinel: {question}\n")
    print("Gathering market context...")
    result = query(question)
    print(f"\nSources consulted: {len(result['context_sources'])}")
    sources_str = ", ".join(result["context_sources"])
    print(f"  {sources_str}")
    print("\n" + "=" * 60)
    print(result["answer"])
    print("=" * 60)


if __name__ == "__main__":
    main()
