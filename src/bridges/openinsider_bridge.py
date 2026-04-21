#!/usr/bin/env python3
"""
Global Sentinel — OpenInsider Scraper Bridge

Scrapes OpenInsider for recent insider trades (last 7 days).
Detects cluster buys (multiple insiders buying same stock) — one of the
strongest alpha signals in academic literature.

Output: data/quantum_feed/insider_clusters.json
Tier 2, trust 0.8, TTL 1440 min
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.openinsider_bridge")


def _fetch_text(url: str, timeout: int = 20) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Fetch failed %s: %s", url, exc)
        return None


class _OpenInsiderParser(HTMLParser):
    """Simple HTML parser to extract table rows from OpenInsider."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_tbody = False
        self.in_row = False
        self.in_cell = False
        self.current_row: List[str] = []
        self.rows: List[List[str]] = []
        self._cell_text = ""
        self._table_count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table_count += 1
            # The main data table is typically the largest one
            attrs_dict = dict(attrs)
            cls = attrs_dict.get("class", "")
            if "tinytable" in cls or self._table_count >= 2:
                self.in_table = True
        elif tag == "tbody" and self.in_table:
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self.in_row = True
            self.current_row = []
        elif tag == "td" and self.in_row:
            self.in_cell = True
            self._cell_text = ""
        elif tag == "a" and self.in_cell:
            pass  # Links contain ticker text

    def handle_endtag(self, tag):
        if tag == "td" and self.in_cell:
            self.in_cell = False
            self.current_row.append(self._cell_text.strip())
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "table":
            self.in_table = False

    def handle_data(self, data):
        if self.in_cell:
            self._cell_text += data


class OpenInsiderBridge:
    """Scrape OpenInsider for insider trading cluster signals."""

    DISPLAY_NAME = "openinsider"
    CATEGORY = "fundamental_intelligence"

    CLUSTER_MIN_BUYS = 2  # Minimum insiders buying same stock in 7 days

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "insider_clusters.json"

    def _scrape_openinsider(self) -> List[Dict[str, Any]]:
        """Scrape OpenInsider for recent insider purchases."""
        # fd=7 = last 7 days, ession=1 = buys only, num=100/den=100 = show all
        url = (
            "http://openinsider.com/screener?"
            "s=&o=&pl=&ph=&ll=&lh=&fd=7&fdr=&td=0&tdr="
            "&feession=&fessing=&cession=&cessing="
            "&si=0&sicl=&dx=0&num=100&den=100&type=&qty=0&val=0&ession=1"
        )
        html = _fetch_text(url)
        if not html:
            return []

        parser = _OpenInsiderParser()
        parser.feed(html)

        trades = []
        for row in parser.rows:
            if len(row) < 12:
                continue
            try:
                # OpenInsider columns (approximate):
                # 0: X (filing link), 1: Filing Date, 2: Trade Date, 3: Ticker,
                # 4: Insider Name, 5: Title, 6: Trade Type, 7: Price,
                # 8: Qty, 9: Owned, 10: Delta Own, 11: Value
                filing_date = row[1].strip() if len(row) > 1 else ""
                trade_date = row[2].strip() if len(row) > 2 else ""
                ticker = row[3].strip().upper() if len(row) > 3 else ""
                insider_name = row[4].strip() if len(row) > 4 else ""
                title = row[5].strip() if len(row) > 5 else ""
                trade_type = row[6].strip() if len(row) > 6 else ""
                price_str = row[7].strip().replace("$", "").replace(",", "") if len(row) > 7 else "0"
                qty_str = row[8].strip().replace(",", "").replace("+", "") if len(row) > 8 else "0"
                value_str = row[11].strip().replace("$", "").replace(",", "").replace("+", "") if len(row) > 11 else "0"

                if not ticker or len(ticker) > 5:
                    continue

                try:
                    price = float(price_str) if price_str else 0
                except ValueError:
                    price = 0
                try:
                    qty = int(float(qty_str)) if qty_str else 0
                except ValueError:
                    qty = 0
                try:
                    value = float(value_str) if value_str else 0
                except ValueError:
                    value = 0

                trades.append({
                    "ticker": ticker,
                    "insider_name": insider_name,
                    "title": title,
                    "trade_type": trade_type,
                    "trade_date": trade_date,
                    "filing_date": filing_date,
                    "price": price,
                    "quantity": qty,
                    "value": value,
                })
            except Exception as e:
                logger.debug(f"[OpenInsider] Row parse error: {e}")
                continue

        return trades

    def _detect_clusters(self, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect cluster buys — multiple insiders buying same stock."""
        by_ticker: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            ticker = trade.get("ticker", "")
            if ticker:
                by_ticker[ticker].append(trade)

        clusters = []
        for ticker, ticker_trades in by_ticker.items():
            # Count unique insiders
            unique_insiders = set()
            total_value = 0.0
            for t in ticker_trades:
                name = t.get("insider_name", "")
                if name:
                    unique_insiders.add(name)
                total_value += t.get("value", 0)

            if len(unique_insiders) >= self.CLUSTER_MIN_BUYS:
                clusters.append({
                    "ticker": ticker,
                    "unique_insiders": len(unique_insiders),
                    "total_trades": len(ticker_trades),
                    "total_value": total_value,
                    "insiders": list(unique_insiders),
                    "trades": ticker_trades,
                    "signal_strength": "strong" if len(unique_insiders) >= 3 else "moderate",
                })

        # Sort by number of unique insiders descending
        clusters.sort(key=lambda x: x.get("unique_insiders", 0), reverse=True)
        return clusters

    def poll(self) -> Dict[str, Any]:
        """Poll OpenInsider for insider trading clusters."""
        trades = self._scrape_openinsider()

        # Also scrape sells for context
        sells_url = (
            "http://openinsider.com/screener?"
            "s=&o=&pl=&ph=&ll=&lh=&fd=7&fdr=&td=0&tdr="
            "&feession=&fessing=&cession=&cessing="
            "&si=0&sicl=&dx=0&num=100&den=100&type=&qty=0&val=0&ession=2"
        )
        sells_html = _fetch_text(sells_url)
        sell_count = 0
        if sells_html:
            parser = _OpenInsiderParser()
            parser.feed(sells_html)
            sell_count = len(parser.rows)

        clusters = self._detect_clusters(trades)

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "openinsider",
            "data": {
                "total_buys_7d": len(trades),
                "total_sells_7d": sell_count,
                "buy_sell_ratio": round(len(trades) / max(sell_count, 1), 2),
                "clusters": clusters,
                "cluster_count": len(clusters),
                "top_individual_buys": sorted(trades, key=lambda x: x.get("value", 0), reverse=True)[:10],
                "summary": {
                    "strongest_cluster": clusters[0]["ticker"] if clusters else None,
                    "strongest_cluster_insiders": clusters[0]["unique_insiders"] if clusters else 0,
                    "total_cluster_value": sum(c.get("total_value", 0) for c in clusters),
                    "signal": "strong_bullish" if len(clusters) >= 3 else "bullish" if clusters else "neutral",
                },
            },
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, indent=2))
        logger.info(f"[OpenInsiderBridge] {len(trades)} buys, {len(clusters)} clusters detected")

        return result
def main():
    logging.basicConfig(level=logging.INFO)
    bridge = OpenInsiderBridge()
    result = bridge.poll()
    print(json.dumps({
        "cluster_count": result.get("data", {}).get("cluster_count"),
        "total_buys_7d": result.get("data", {}).get("total_buys_7d"),
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
