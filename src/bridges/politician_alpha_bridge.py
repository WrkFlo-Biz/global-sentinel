#!/usr/bin/env python3
"""
Global Sentinel V5.2 - Politician Alpha Bridge (Capitol Whale)

Fetches congressional trading disclosures from Financial Modeling Prep API
and computes a "Political Alpha Score" for tracked symbols.

Weights:
- Committee influence (Armed Services, Appropriations, Ways and Means, etc.)
- Transaction type (Purchase vs Sale)
- Transaction size ($100K+: 2.0x, $50K-100K: 1.5x, else 1.0x)
- Recency / disclosure lag decay (15d: 1.0x, 30d: 0.7x, 45d: 0.4x, 45+: 0.1x)

Output feeds into snapshot["politician_alpha"] for the crisis monitor.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("Missing dependency: pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def safe_get_json(url: str, timeout: int = 15) -> Any:
    """HTTP GET returning parsed JSON or None on any error."""
    if requests is None:
        return None
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "GlobalSentinel-PoliticianAlphaBridge/1.0"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Committee influence weights
# ---------------------------------------------------------------------------
COMMITTEE_WEIGHTS: Dict[str, float] = {
    "armed services": 1.5,
    "appropriations": 2.0,
    "ways and means": 1.8,
    "energy and commerce": 1.6,
    "commerce": 1.8,           # Senate Commerce — CHIPS Act, AI regulation, tech oversight
    "intelligence": 1.7,
    "finance": 1.5,
    "banking": 1.4,
    "homeland security": 1.3,
    "science": 1.5,            # Science & Technology — AI/semiconductor policy
    "judiciary": 1.2,
    "foreign relations": 1.4,
    "budget": 1.3,
}

# Default weight if committee is unknown
DEFAULT_COMMITTEE_WEIGHT = 1.0


def _committee_weight(committee: str) -> float:
    """Return committee influence weight based on committee name substring matching."""
    if not committee:
        return DEFAULT_COMMITTEE_WEIGHT
    lower = committee.lower()
    for key, weight in COMMITTEE_WEIGHTS.items():
        if key in lower:
            return weight
    return DEFAULT_COMMITTEE_WEIGHT


def _transaction_direction(tx_type: str) -> int:
    """Return +1 for purchase, -1 for sale."""
    if not tx_type:
        return 0
    lower = tx_type.lower()
    if "purchase" in lower or "buy" in lower:
        return 1
    if "sale" in lower or "sell" in lower:
        return -1
    return 0


def _size_weight(amount_str: str) -> float:
    """
    Parse FMP amount ranges like '$100,001 - $250,000' and return size weight.
    $100K+: 2.0x, $50K-100K: 1.5x, else 1.0x
    """
    if not amount_str:
        return 1.0
    # Extract numeric values from string
    import re
    nums = re.findall(r'[\d,]+', amount_str.replace(',', ''))
    if not nums:
        return 1.0
    try:
        # Use the upper bound if available, else the first number
        max_val = max(int(n) for n in nums if n)
    except (ValueError, TypeError):
        return 1.0
    if max_val >= 100_000:
        return 2.0
    elif max_val >= 50_000:
        return 1.5
    return 1.0


def _recency_decay(trade_date_str: str) -> float:
    """
    Disclosure lag decay:
    - Last 15 days: 1.0x
    - 15-30 days: 0.7x
    - 30-45 days: 0.4x
    - 45+ days: 0.1x
    """
    if not trade_date_str:
        return 0.1
    try:
        trade_date = datetime.strptime(trade_date_str[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return 0.1
    days_ago = (datetime.now(timezone.utc) - trade_date).days
    if days_ago <= 15:
        return 1.0
    elif days_ago <= 30:
        return 0.7
    elif days_ago <= 45:
        return 0.4
    return 0.1


# ---------------------------------------------------------------------------
# Politician Alpha Bridge
# ---------------------------------------------------------------------------

class PoliticianAlphaBridge:
    """
    Fetches congressional trading data from Financial Modeling Prep API
    and computes Political Alpha Scores for tracked symbols.

    Primary endpoints:
    - Senate trading by symbol
    - House disclosure RSS feed
    - Senate disclosure RSS feed
    """

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.watchlist = load_yaml(repo_root / "config" / "assets_watchlist.yaml")
        self.api_key = os.getenv("FMP_API_KEY", "")

        # Cache directory
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "politician_alpha"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _has_api_key(self) -> bool:
        return bool(self.api_key)

    # ------------------------------------------------------------------
    # Extract tracked symbols from watchlist
    # ------------------------------------------------------------------
    def _get_tracked_symbols(self) -> List[str]:
        """Extract equity symbols from the watchlist config."""
        symbols: List[str] = []
        # Equity indices
        for item in self.watchlist.get("equity_indices", []):
            sym = item.get("symbol", "")
            if sym and sym != "VIX":
                symbols.append(sym)
        # Aviation & travel
        for item in self.watchlist.get("aviation_travel", []):
            sym = item.get("symbol", "")
            if sym:
                symbols.append(sym)
        # Defense
        for item in self.watchlist.get("defense_military", []):
            sym = item.get("symbol", "")
            if sym:
                symbols.append(sym)
        # Gasoline & refining
        for item in self.watchlist.get("gasoline_refining", []):
            sym = item.get("symbol", "")
            if sym:
                symbols.append(sym)
        # Other sections with 'symbols' lists
        for section in ("cybersecurity", "shipping_maritime", "uranium_nuclear",
                        "agriculture_food", "leveraged_volatility", "oil_majors",
                        "ai_infrastructure", "ai_software", "ai_disrupted",
                        "robotics_autonomous"):
            data = self.watchlist.get(section, {})
            if isinstance(data, dict):
                for sym in data.get("symbols", []):
                    if sym:
                        symbols.append(str(sym))
        return list(set(symbols))

    # ------------------------------------------------------------------
    # API fetchers
    # ------------------------------------------------------------------
    def _fetch_quiver_trades(self) -> List[Dict[str, Any]]:
        """
        Fetch congressional trades from Quiver Quantitative (free, no key needed).
        Returns trades normalized to our internal format.
        """
        url = "https://api.quiverquant.com/beta/live/congresstrading"
        # Quiver requires Accept header for JSON response
        if requests is None:
            return []
        try:
            resp = requests.get(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            data = None
        if not isinstance(data, list):
            return []

        normalized: List[Dict[str, Any]] = []
        for trade in data:
            ticker = trade.get("Ticker", "")
            if not ticker:
                continue
            # Map Quiver fields to our internal format
            tx_type = trade.get("Transaction", "")
            normalized.append({
                "symbol": ticker,
                "transactionDate": trade.get("TransactionDate", ""),
                "disclosureDate": trade.get("ReportDate", ""),
                "type": tx_type,
                "transactionType": tx_type,
                "amount": trade.get("Range", ""),
                "firstName": trade.get("Representative", "").split()[0] if trade.get("Representative") else "",
                "lastName": " ".join(trade.get("Representative", "").split()[1:]) if trade.get("Representative") else "",
                "office": trade.get("House", ""),
                "committee": "",  # Quiver doesn't provide committee
                "representative": trade.get("Representative", ""),
                "party": trade.get("Party", ""),
                "source": "quiver",
            })
        return normalized

    def _fetch_fmp_senate_latest(self) -> List[Dict[str, Any]]:
        """Fetch latest senate financial disclosures (FMP stable API)."""
        if not self._has_api_key():
            return []
        url = f"https://financialmodelingprep.com/stable/senate-latest?apikey={self.api_key}"
        data = safe_get_json(url)
        return data if isinstance(data, list) else []

    def _fetch_fmp_house_latest(self) -> List[Dict[str, Any]]:
        """Fetch latest house financial disclosures (FMP stable API)."""
        if not self._has_api_key():
            return []
        url = f"https://financialmodelingprep.com/stable/house-latest?apikey={self.api_key}"
        data = safe_get_json(url)
        return data if isinstance(data, list) else []

    def _fetch_fmp_senate_by_symbol(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetch senate trading activity for a specific symbol (FMP stable API)."""
        if not self._has_api_key():
            return []
        url = f"https://financialmodelingprep.com/stable/senate-trading?symbol={symbol}&apikey={self.api_key}"
        data = safe_get_json(url)
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # Score computation
    # ------------------------------------------------------------------
    def _compute_trade_score(self, trade: Dict[str, Any]) -> float:
        """
        Compute weighted score for a single congressional trade.
        Score = direction * committee_weight * size_weight * recency_decay
        """
        direction = _transaction_direction(trade.get("type", trade.get("transactionType", "")))
        if direction == 0:
            return 0.0

        committee = trade.get("committee", trade.get("office", ""))
        c_weight = _committee_weight(committee)
        s_weight = _size_weight(trade.get("amount", trade.get("range", "")))
        recency = _recency_decay(
            trade.get("transactionDate", trade.get("disclosureDate", ""))
        )

        return direction * c_weight * s_weight * recency

    def _compute_political_alpha_scores(
        self, all_trades: List[Dict[str, Any]], tracked_symbols: List[str]
    ) -> Dict[str, float]:
        """Compute aggregate political alpha score per symbol."""
        scores: Dict[str, float] = {}
        for symbol in tracked_symbols:
            symbol_trades = [
                t for t in all_trades
                if (t.get("symbol", "") or t.get("ticker", "")).upper() == symbol.upper()
            ]
            if not symbol_trades:
                continue
            total = sum(self._compute_trade_score(t) for t in symbol_trades)
            scores[symbol] = round(total, 3)
        return scores

    def _extract_top_whale_trades(
        self, all_trades: List[Dict[str, Any]], limit: int = 15
    ) -> List[Dict[str, Any]]:
        """Extract the most significant recent trades by absolute score."""
        scored: List[tuple] = []
        for t in all_trades:
            score = self._compute_trade_score(t)
            if abs(score) > 0:
                scored.append((abs(score), score, t))
        scored.sort(key=lambda x: x[0], reverse=True)

        top_trades = []
        for abs_score, score, t in scored[:limit]:
            top_trades.append({
                "politician": t.get("firstName", t.get("representative", "")) + " "
                             + t.get("lastName", ""),
                "symbol": t.get("symbol", t.get("ticker", "")),
                "transaction_type": t.get("type", t.get("transactionType", "")),
                "amount": t.get("amount", t.get("range", "")),
                "transaction_date": t.get("transactionDate", t.get("disclosureDate", "")),
                "committee": t.get("committee", t.get("office", "")),
                "score": round(score, 3),
                "chamber": t.get("chamber", "senate" if t.get("senator") else "house"),
            })
        return top_trades

    def _compute_committee_signals(
        self, all_trades: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Identify committee-ticker correlations (clusters of trades)."""
        committee_tickers: Dict[str, Dict[str, int]] = {}
        for t in all_trades:
            committee = t.get("committee", t.get("office", "")) or "Unknown"
            symbol = (t.get("symbol", t.get("ticker", "")) or "").upper()
            if not symbol:
                continue
            if committee not in committee_tickers:
                committee_tickers[committee] = {}
            committee_tickers[committee][symbol] = committee_tickers[committee].get(symbol, 0) + 1

        signals = []
        for committee, tickers in committee_tickers.items():
            # Only report if there are at least 2 trades in a committee-ticker pair
            for symbol, count in tickers.items():
                if count >= 2:
                    signals.append({
                        "committee": committee,
                        "symbol": symbol,
                        "trade_count": count,
                        "influence_weight": _committee_weight(committee),
                    })
        signals.sort(key=lambda x: x["trade_count"], reverse=True)
        return signals[:20]

    def _compute_aggregate_sentiment(
        self, scores: Dict[str, float]
    ) -> str:
        """Determine overall congressional sentiment from aggregate scores."""
        if not scores:
            return "neutral"
        total = sum(scores.values())
        count = len(scores)
        avg = total / count if count else 0
        if avg > 1.0:
            return "bullish"
        elif avg < -1.0:
            return "bearish"
        return "neutral"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def poll(self) -> Dict[str, Any]:
        """
        Fetch congressional trading data and compute Political Alpha Scores.

        Primary source: Quiver Quantitative (free, no key needed).
        Fallback: FMP API (paid tier, requires FMP_API_KEY).

        Returns standardized bridge result dict.
        """
        tracked_symbols = self._get_tracked_symbols()
        all_trades: List[Dict[str, Any]] = []
        source = "quiver"

        # Primary: Quiver Quantitative (free)
        quiver_trades = self._fetch_quiver_trades()
        all_trades.extend(quiver_trades)

        # Also fetch from FMP stable API if key available (cross-reference)
        if self._has_api_key():
            fmp_senate = self._fetch_fmp_senate_latest()
            fmp_house = self._fetch_fmp_house_latest()
            existing_ids = {
                (t.get("transactionDate", ""), t.get("symbol", ""), t.get("firstName", ""))
                for t in all_trades
            }
            for t in fmp_senate + fmp_house:
                key = (t.get("transactionDate", ""), t.get("symbol", ""), t.get("firstName", ""))
                if key not in existing_ids:
                    all_trades.append(t)
                    existing_ids.add(key)
            if not all_trades and (fmp_senate or fmp_house):
                source = "financial_modeling_prep"
            elif fmp_senate or fmp_house:
                source = "quiver+fmp"

        if not all_trades:
            return self._empty_result("No congressional trading data from any source")

        # Compute scores
        scores = self._compute_political_alpha_scores(all_trades, tracked_symbols)
        top_trades = self._extract_top_whale_trades(all_trades)
        committee_signals = self._compute_committee_signals(all_trades)
        sentiment = self._compute_aggregate_sentiment(scores)

        result = {
            "timestamp_utc": iso_now(),
            "fresh": True,
            "source": source,
            "political_alpha_scores": scores,
            "top_whale_trades": top_trades,
            "committee_signals": committee_signals,
            "aggregate_sentiment": sentiment,
            "total_trades_analyzed": len(all_trades),
            "tracked_symbols_with_activity": len(scores),
        }

        # Cache
        self._cache_results(result)
        return result

    def _empty_result(self, reason: str) -> Dict[str, Any]:
        """Return empty/default result when data is unavailable."""
        return {
            "timestamp_utc": iso_now(),
            "fresh": False,
            "source": "unavailable",
            "reason": reason,
            "political_alpha_scores": {},
            "top_whale_trades": [],
            "committee_signals": [],
            "aggregate_sentiment": "neutral",
            "total_trades_analyzed": 0,
            "tracked_symbols_with_activity": 0,
        }

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def _cache_results(self, result: Dict[str, Any]):
        tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = self.cache_dir / f"politician_alpha_{tag}.json"
        try:
            cache_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception:
            pass  # Non-fatal: caching failure should not break the bridge


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Politician Alpha Bridge - congressional trading signals"
    )
    p.add_argument("--repo-root", default=".")
    p.add_argument("--output-json", default=None,
                   help="Write output to JSON file instead of stdout")
    args = p.parse_args()

    bridge = PoliticianAlphaBridge(Path(args.repo_root).resolve())
    result = bridge.poll()

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
