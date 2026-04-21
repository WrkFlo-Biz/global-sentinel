#!/usr/bin/env python3
"""Sentiment Bridge -- News sentiment via Finnhub API.

Emits MacroPolicyEvent packets from Finnhub company news.
Falls back to empty list if FINNHUB_API_KEY is not set.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from src.packets.macro_policy_event import make_macro_policy_event


FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"

DEFAULT_SYMBOLS = ["SPY", "QQQ", "XLE", "GLD", "TLT"]


class SentimentBridge:
    source = "sentiment"
    source_tier = "tier_2_operational"
    trust_weight = 0.8

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch news sentiment for default watchlist symbols."""
        if not FINNHUB_API_KEY:
            return []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        out: List[Dict[str, Any]] = []
        for symbol in DEFAULT_SYMBOLS:
            try:
                out.extend(self.fetch_company_news(symbol, yesterday, today))
            except Exception:
                continue
        return out

    def fetch_company_news(self, symbol: str, from_date: str, to_date: str) -> List[Dict[str, Any]]:
        """Fetch company news from Finnhub for a single symbol."""
        if not FINNHUB_API_KEY:
            return []
        url = (
            f"{FINNHUB_BASE}/company-news"
            f"?symbol={symbol}&from={from_date}&to={to_date}"
            f"&token={FINNHUB_API_KEY}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/5.1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        out: List[Dict[str, Any]] = []
        for row in rows[:20]:
            headline = row.get("headline", "")
            summary = row.get("summary", "")
            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=f"News sentiment: {symbol}",
                policy_domain="sentiment_news",
                hawkish_dovish_score=0.0,
                growth_inflation_score=0.0,
                market_relevance_score=0.65,
                related_assets=[symbol],
                summary=f"{headline} {summary}"[:500],
                confidence=0.75,
                provenance={
                    "symbol": symbol,
                    "url": row.get("url"),
                    "datetime": row.get("datetime"),
                    "source_name": row.get("source"),
                },
            )
            out.append(pkt.to_dict())
        return out
