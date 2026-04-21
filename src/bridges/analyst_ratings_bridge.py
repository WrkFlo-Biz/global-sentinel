#!/usr/bin/env python3
"""
Global Sentinel — Analyst Ratings Bridge

Combines Finnhub (price targets, recommendations, upgrades/downgrades)
with yfinance (analyst price targets, recommendations summary)
into a consensus score per symbol.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.analyst_ratings_bridge")

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

try:
    import yaml
except ImportError:
    yaml = None


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


class AnalystRatingsBridge:
    """Fetch analyst ratings and price targets from Finnhub + yfinance."""

    DISPLAY_NAME = "analyst_ratings"
    CATEGORY = "fundamental_intelligence"
    API_BASE = "https://finnhub.io/api/v1"

    # Rating string to numeric mapping
    RATING_MAP = {
        "strong buy": 5, "buy": 4, "overweight": 4,
        "outperform": 4, "hold": 3, "neutral": 3,
        "equal-weight": 3, "market perform": 3,
        "underweight": 2, "underperform": 2,
        "sell": 1, "strong sell": 0,
    }

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.api_key = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_KEY", "")
        wl = _load_yaml(self.repo_root / "config" / "assets_watchlist.yaml")
        self.symbols = self._extract_stock_symbols(wl)

    def _extract_stock_symbols(self, wl: Dict[str, Any]) -> List[str]:
        """Extract individual stock symbols (not ETFs) from watchlist."""
        symbols = []
        etf_patterns = ["SPY", "QQQ", "IWM", "DIA", "XL", "EEM", "EFA", "FXI",
                        "GLD", "TLT", "VTI", "VOO", "MDY", "VTWO", "VIX"]
        skip = ["USD", "XAU", "UST", "BRN", "BZ=F", "^", "GC=F", "USD/"]
        for section in ["equity_indices", "aviation_travel", "travel_hospitality",
                        "supply_chain", "insurance_risk"]:
            for item in wl.get(section, []):
                if not isinstance(item, dict):
                    continue
                sym = str(item.get("symbol", "")).strip()
                if not sym or any(p in sym for p in skip):
                    continue
                if sym in etf_patterns:
                    continue
                symbols.append(sym)
        seen = set()
        out = []
        for s in symbols:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out[:20]

    def _finnhub_get(self, endpoint: str, params: Dict[str, str]) -> Any:
        params["token"] = self.api_key
        url = f"{self.API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def poll(self) -> Dict[str, Any]:
        """Poll analyst ratings for all watchlist symbols."""
        if not self.api_key:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bridge": "analyst_ratings",
                "error": "no_finnhub_api_key",
                "symbols": {},
            }

        results = {}
        errors = []
        upgrades_list = []
        downgrades_list = []

        for symbol in self.symbols:
            try:
                data = self._analyze_symbol(symbol)
                if data:
                    results[symbol] = data
                    for action in data.get("recent_actions", []):
                        if action.get("action") in ("upgrade", "init-buy"):
                            upgrades_list.append({"symbol": symbol, **action})
                        elif action.get("action") in ("downgrade", "init-sell"):
                            downgrades_list.append({"symbol": symbol, **action})
                time.sleep(0.5)  # Finnhub rate limit
            except Exception as e:
                errors.append({"symbol": symbol, "error": str(e)})
                logger.warning(f"[AnalystRatingsBridge] {symbol} error: {e}")

        # Aggregate consensus
        scores = [r.get("consensus_score", 0) for r in results.values() if r.get("consensus_score")]
        avg_consensus = round(sum(scores) / max(len(scores), 1), 2)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "analyst_ratings",
            "source": "finnhub+yfinance",
            "symbols_analyzed": len(results),
            "average_consensus_score": avg_consensus,
            "market_sentiment": "bullish" if avg_consensus > 3.5 else "bearish" if avg_consensus < 2.5 else "neutral",
            "recent_upgrades": upgrades_list[:10],
            "recent_downgrades": downgrades_list[:10],
            "symbols": results,
            "errors": errors,
        }

    def _analyze_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get combined analyst data for a symbol."""
        result: Dict[str, Any] = {"symbol": symbol}

        # 1. Finnhub: Recommendation trends
        try:
            recs = self._finnhub_get("stock/recommendation", {"symbol": symbol})
            if isinstance(recs, list) and recs:
                latest = recs[0]
                result["finnhub_recommendation"] = {
                    "period": latest.get("period"),
                    "strong_buy": latest.get("strongBuy", 0),
                    "buy": latest.get("buy", 0),
                    "hold": latest.get("hold", 0),
                    "sell": latest.get("sell", 0),
                    "strong_sell": latest.get("strongSell", 0),
                }
                # Weighted score
                sb = latest.get("strongBuy", 0)
                b = latest.get("buy", 0)
                h = latest.get("hold", 0)
                s = latest.get("sell", 0)
                ss = latest.get("strongSell", 0)
                total = sb + b + h + s + ss
                if total > 0:
                    weighted = (sb * 5 + b * 4 + h * 3 + s * 2 + ss * 1) / total
                    result["finnhub_score"] = round(weighted, 2)
            time.sleep(0.2)
        except Exception as e:
            result["finnhub_recommendation_error"] = str(e)

        # 2. Finnhub: Price target
        try:
            pt = self._finnhub_get("stock/price-target", {"symbol": symbol})
            if pt and pt.get("targetMean"):
                result["price_target"] = {
                    "target_high": pt.get("targetHigh"),
                    "target_low": pt.get("targetLow"),
                    "target_mean": pt.get("targetMean"),
                    "target_median": pt.get("targetMedian"),
                    "last_updated": pt.get("lastUpdated"),
                }
            time.sleep(0.2)
        except Exception as e:
            result["price_target_error"] = str(e)

        # 3. Finnhub: Upgrades/downgrades
        try:
            ud = self._finnhub_get("stock/upgrade-downgrade", {"symbol": symbol})
            if isinstance(ud, list):
                recent = ud[:5]
                actions = []
                for item in recent:
                    action_type = "upgrade" if self.RATING_MAP.get(
                        (item.get("toGrade") or "").lower(), 3
                    ) > self.RATING_MAP.get(
                        (item.get("fromGrade") or "").lower(), 3
                    ) else "downgrade"
                    actions.append({
                        "date": item.get("gradeTime"),
                        "company": item.get("company"),
                        "from_grade": item.get("fromGrade"),
                        "to_grade": item.get("toGrade"),
                        "action": item.get("action", action_type),
                    })
                result["recent_actions"] = actions
            time.sleep(0.2)
        except Exception as e:
            result["upgrade_downgrade_error"] = str(e)

        # 4. yfinance: Additional analyst data (if available)
        if YF_AVAILABLE:
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info or {}
                if info.get("targetMeanPrice"):
                    result["yfinance_targets"] = {
                        "target_mean": info.get("targetMeanPrice"),
                        "target_high": info.get("targetHighPrice"),
                        "target_low": info.get("targetLowPrice"),
                        "recommendation": info.get("recommendationKey"),
                        "number_of_analysts": info.get("numberOfAnalystOpinions"),
                    }
            except Exception as e:
                result["yfinance_error"] = str(e)

        # 5. Calculate consensus score (1-5 scale)
        scores = []
        if result.get("finnhub_score"):
            scores.append(result["finnhub_score"])
        yf_rec = (result.get("yfinance_targets") or {}).get("recommendation", "")
        if yf_rec:
            mapped = self.RATING_MAP.get(yf_rec.lower())
            if mapped:
                scores.append(mapped)
        result["consensus_score"] = round(sum(scores) / max(len(scores), 1), 2) if scores else None
        result["consensus_signal"] = (
            "strong_buy" if (result["consensus_score"] or 0) >= 4.5 else
            "buy" if (result["consensus_score"] or 0) >= 3.5 else
            "hold" if (result["consensus_score"] or 0) >= 2.5 else
            "sell" if (result["consensus_score"] or 0) >= 1.5 else
            "strong_sell" if result["consensus_score"] else "unknown"
        )

        return result
