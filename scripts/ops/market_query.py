#!/usr/bin/env python3
"""
Global Sentinel — Conversational Market Query (LLM-powered)

Takes a natural language question about the market, gathers context
from ALL quantum_feed JSON files, and calls Azure OpenAI (gpt-5-mini)
to generate an informed answer.

Usage:
  python3 market_query.py "What is the best trade setup right now?"
  python3 market_query.py "Is NVDA overvalued?"
  python3 market_query.py "Which sectors are rotating?"
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))

# Load .env
_env = {}
_env_path = REPO_ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            _env[k.strip()] = v.strip()

AZURE_ENDPOINT = _env.get("AZURE_OPENAI_ENDPOINT", "https://moses-8586-resource.services.ai.azure.com/")
AZURE_KEY = _env.get("AZURE_OPENAI_API_KEY", _env.get("AZURE_CLAUDE_API_KEY", ""))
AZURE_DEPLOYMENT = _env.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
API_VERSION = _env.get("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")


def gather_all_context() -> Dict[str, Any]:
    """Load ALL quantum_feed JSON files as context."""
    feed_dir = REPO_ROOT / "data" / "quantum_feed"
    context = {}
    if not feed_dir.exists():
        return context
    for f in sorted(feed_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            # Truncate large arrays to save token budget
            context[f.stem] = _truncate(data)
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
        return {k: _truncate(v, max_list, max_depth, depth + 1) for k, v in list(obj.items())[:30]}
    if isinstance(obj, list):
        truncated = [_truncate(item, max_list, max_depth, depth + 1) for item in obj[:max_list]]
        if len(obj) > max_list:
            truncated.append(f"... and {len(obj) - max_list} more items")
        return truncated
    return obj


def call_llm(question: str, context: Dict[str, Any]) -> str:
    """Call Azure OpenAI with the question and market context."""
    if not AZURE_KEY:
        return "Error: AZURE_OPENAI_API_KEY not set in .env"

    context_str = json.dumps(context, indent=1, default=str)
    # Cap context at ~50k chars to stay within token limits
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

    url = f"{AZURE_ENDPOINT}openai/deployments/{AZURE_DEPLOYMENT}/chat/completions?api-version={API_VERSION}"
    body = json.dumps({
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"MARKET DATA CONTEXT:\n{context_str}\n\nQUESTION: {question}"},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode()

    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "api-key": AZURE_KEY,
    })

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return f"LLM API error ({exc.code}): {error_body[:500]}"
    except Exception as exc:
        return f"LLM error: {exc}"


def query(question: str) -> Dict[str, Any]:
    """Run a market query and return structured result."""
    ts = datetime.now(timezone.utc).isoformat()
    context = gather_all_context()
    answer = call_llm(question, context)

    result = {
        "source": "market_query",
        "timestamp_utc": ts,
        "question": question,
        "answer": answer,
        "context_sources": list(context.keys()),
        "model": AZURE_DEPLOYMENT,
    }

    # Save last query result
    output_path = REPO_ROOT / "data" / "quantum_feed" / "last_market_query.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, default=str))

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
    sources_str = ', '.join(result['context_sources'])
    print(f"  {sources_str}")
    print("\n" + "="*60)
    print(result['answer'])
    print("="*60)


if __name__ == "__main__":
    main()
