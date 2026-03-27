#!/usr/bin/env python3
"""
Global Sentinel — News Impact Scorer

Reads headlines from Finnhub news API and Google News RSS,
extracts ticker mentions, and scores market impact using
keyword matching.

Output: data/quantum_feed/news_impact.json
Tier 2, trust 0.7, TTL 30 min
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("global_sentinel.news_impact_bridge")

# Impact keyword rules: (pattern, score, category)
IMPACT_RULES: List[Tuple[str, int, str]] = [
    (r"beats?\s+(?:earnings|estimates|expectations)", 3, "earnings_beat"),
    (r"(?:earnings|revenue)\s+(?:miss|misses|missed)", -3, "earnings_miss"),
    (r"(?:upgrade[ds]?|raised?\s+(?:target|price))", 2, "analyst_upgrade"),
    (r"(?:downgrade[ds]?|cut\s+(?:target|price|rating))", -2, "analyst_downgrade"),
    (r"FDA\s+approv", 4, "fda_approval"),
    (r"FDA\s+(?:reject|deny|refuse)", -4, "fda_rejection"),
    (r"layoff|(?:cutting|cut)\s+jobs|workforce\s+reduction", -2, "layoffs"),
    (r"CEO\s+(?:resign|step|leave|depart|fired|ousted)", -3, "ceo_departure"),
    (r"(?:acqui(?:re|sition)|buyout|merger|takeover)\s+(?:deal|bid|offer)?", 3, "acquisition"),
    (r"lawsuit|sued|litigation|legal\s+action", -2, "lawsuit"),
    (r"sanction[s]?|sanctioned", -3, "sanctions"),
    (r"tariff[s]?|trade\s+war|import\s+dut", -2, "tariff"),
    (r"(?:stock|share)\s+(?:buyback|repurchase)", 2, "buyback"),
    (r"dividend\s+(?:increase|raise|hike)", 2, "dividend_hike"),
    (r"dividend\s+(?:cut|slash|suspend)", -3, "dividend_cut"),
    (r"(?:beat|strong|blowout|stellar)\s+(?:quarter|earnings|results)", 3, "strong_results"),
    (r"(?:weak|disappointing|miss)\s+(?:quarter|earnings|results|guidance)", -3, "weak_results"),
    (r"guidance\s+(?:raise[ds]?|above|higher)", 2, "guidance_raised"),
    (r"guidance\s+(?:lower|below|cut|reduced)", -3, "guidance_lowered"),
    (r"(?:record|all.?time)\s+(?:high|revenue|profit)", 2, "record_high"),
    (r"(?:bankruptcy|chapter\s+11|insolvent)", -5, "bankruptcy"),
    (r"(?:IPO|going\s+public|direct\s+listing)", 1, "ipo"),
    (r"(?:data\s+breach|hack|cyber\s*attack)", -2, "cyber_breach"),
    (r"(?:partnership|collaboration|deal)\s+with", 1, "partnership"),
    (r"(?:recall|safety\s+issue|defect)", -2, "recall"),
    (r"(?:new\s+product|launch|unveiled|introduced)", 1, "product_launch"),
]

COMPILED_RULES = [(re.compile(pat, re.IGNORECASE), score, cat) for pat, score, cat in IMPACT_RULES]

# Ticker detection
TICKER_RE = re.compile(r"\b([A-Z]{2,5})\b")
TICKER_MAP = {
    "APPLE": "AAPL", "AMAZON": "AMZN", "GOOGLE": "GOOGL", "ALPHABET": "GOOGL",
    "MICROSOFT": "MSFT", "TESLA": "TSLA", "NVIDIA": "NVDA", "META": "META",
    "NETFLIX": "NFLX", "JPMORGAN": "JPM",
}
KNOWN_TICKERS = {
    "AAPL", "NVDA", "TSLA", "AMZN", "META", "MSFT", "AMD", "GOOGL",
    "NFLX", "JPM", "V", "UNH", "XOM", "LLY", "AVGO", "MA", "COST",
    "HD", "SPY", "QQQ", "DIS", "BA", "INTC", "PYPL", "CRM", "UBER",
}
NOISE_WORDS = {
    "CEO", "IPO", "ETF", "GDP", "CPI", "FBI", "SEC", "FDA", "FED",
    "NYSE", "USA", "THE", "FOR", "AND", "HAS", "NEW", "ALL", "NOW",
}


def _load_env(repo_root: Path) -> Dict[str, str]:
    env = {}
    env_path = repo_root / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _extract_tickers(text: str) -> List[str]:
    """Extract likely ticker symbols from text."""
    tickers = set()
    # Check company name mapping
    upper_text = text.upper()
    for company, ticker in TICKER_MAP.items():
        if company in upper_text:
            tickers.add(ticker)
    # Check direct ticker mentions
    for match in TICKER_RE.findall(text):
        if match in KNOWN_TICKERS:
            tickers.add(match)
    return list(tickers)


def _score_headline(headline: str) -> Tuple[int, List[str]]:
    """Score a headline for market impact. Returns (score, categories)."""
    total = 0
    cats = []
    for pattern, score, category in COMPILED_RULES:
        if pattern.search(headline):
            total += score
            cats.append(category)
    return max(-5, min(5, total)), cats


def fetch_finnhub_news(api_key: str, category: str = "general") -> List[Dict[str, Any]]:
    """Fetch market news from Finnhub."""
    url = f"https://finnhub.io/api/v1/news?category={category}&token={api_key}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Finnhub news fetch failed: %s", exc)
        return []


def fetch_google_news_rss(query: str = "stock market") -> List[Dict[str, str]]:
    """Fetch headlines from Google News RSS."""
    url = f"https://news.google.com/rss/search?q={urllib.request.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(xml_text)
        items = []
        for item in root.findall(".//item")[:30]:
            title = item.findtext("title", "")
            pub_date = item.findtext("pubDate", "")
            items.append({"headline": title, "published": pub_date, "source": "google_news"})
        return items
    except Exception as exc:
        logger.warning("Google News RSS fetch failed: %s", exc)
        return []


class NewsImpactBridge:
    """Bridge for news impact scoring."""

    DISPLAY_NAME = "news_impact"
    CATEGORY = "news_intelligence"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "news_impact.json"
        env = _load_env(self.repo_root)
        self.finnhub_key = env.get("FINNHUB_API_KEY", env.get("FINNHUB_KEY", ""))

    def poll(self) -> Dict[str, Any]:
        all_headlines = []

        # Finnhub news
        if self.finnhub_key:
            fh_news = fetch_finnhub_news(self.finnhub_key)
            for item in fh_news[:30]:
                all_headlines.append({
                    "headline": item.get("headline", ""),
                    "source": "finnhub",
                    "url": item.get("url", ""),
                    "published": item.get("datetime", ""),
                })

        # Google News RSS
        for query in ["stock market", "earnings report", "SEC filing"]:
            gn_items = fetch_google_news_rss(query)
            all_headlines.extend(gn_items)

        # Score each headline and extract tickers
        scored_headlines = []
        ticker_impact: Dict[str, List[int]] = {}

        for item in all_headlines:
            headline = item.get("headline", "")
            if not headline:
                continue
            score, categories = _score_headline(headline)
            tickers = _extract_tickers(headline)

            scored_item = {
                "headline": headline[:200],
                "source": item.get("source", "unknown"),
                "impact_score": score,
                "categories": categories,
                "tickers_mentioned": tickers,
            }
            scored_headlines.append(scored_item)

            for t in tickers:
                if t not in ticker_impact:
                    ticker_impact[t] = []
                ticker_impact[t].append(score)

        # Aggregate per-ticker impact
        ticker_scores = []
        for ticker, scores in ticker_impact.items():
            avg_score = sum(scores) / len(scores) if scores else 0
            ticker_scores.append({
                "ticker": ticker,
                "headline_count": len(scores),
                "avg_impact_score": round(avg_score, 2),
                "total_impact": sum(scores),
                "max_impact": max(scores) if scores else 0,
                "min_impact": min(scores) if scores else 0,
            })

        ticker_scores.sort(key=lambda x: abs(x["total_impact"]), reverse=True)
        bullish = [t for t in ticker_scores if t["total_impact"] > 0]
        bearish = [t for t in ticker_scores if t["total_impact"] < 0]

        output = {
            "source": "news_impact_bridge",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "headlines_analyzed": len(scored_headlines),
            "tickers_mentioned": len(ticker_scores),
            "bullish_tickers": bullish[:10],
            "bearish_tickers": bearish[:10],
            "ticker_impact_scores": ticker_scores[:30],
            "top_impact_headlines": sorted(scored_headlines, key=lambda x: abs(x["impact_score"]), reverse=True)[:15],
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(output, indent=2, default=str))
        logger.info("News impact: %d headlines, %d tickers, %d bullish, %d bearish",
                     len(scored_headlines), len(ticker_scores), len(bullish), len(bearish))

        return {
            "source": "news_impact_bridge",
            "source_tier": "tier_2_operational",
            "trust_weight": 0.7,
            "timestamp_utc": output["timestamp_utc"],
            "fresh": True,
            "data": output,
        }


def main():
    logging.basicConfig(level=logging.INFO)
    bridge = NewsImpactBridge()
    result = bridge.poll()
    d = result["data"]
    print(json.dumps({
        "headlines": d["headlines_analyzed"],
        "tickers": d["tickers_mentioned"],
        "top_bullish": d["bullish_tickers"][:3],
        "top_bearish": d["bearish_tickers"][:3],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
