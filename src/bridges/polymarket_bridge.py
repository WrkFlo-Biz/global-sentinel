#!/usr/bin/env python3
"""Polymarket Geopolitical Prediction Market Bridge.

Polls Polymarket API for geopolitical event probabilities, calculates
probability velocity (rate of change), and feeds into regime scoring.

P0-1 enhancement — deployed 2026-03-25.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None

from src.bridges.base_bridge import BaseBridge, utc_now_iso

logger = logging.getLogger(__name__)

# Polymarket Gamma API endpoints
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"

# Keywords for Iran / war / peace markets
IRAN_KEYWORDS = [
    "iran", "iranian", "tehran", "irgc", "hormuz", "persian gulf",
    "khamenei", "rouhani", "nuclear deal", "jcpoa",
]
WAR_PEACE_KEYWORDS = [
    "war", "peace", "ceasefire", "escalation", "conflict", "military",
    "attack", "strike", "invasion", "armistice", "treaty", "sanctions",
    "missile", "nuclear", "bombing", "troops", "deploy",
]
GEOPOLITICS_KEYWORDS = IRAN_KEYWORDS + WAR_PEACE_KEYWORDS

# How many historical snapshots to keep for velocity calculation
MAX_HISTORY = 12  # 12 x 5min = 1 hour of history


class PolymarketBridge(BaseBridge):
    """Bridge polling Polymarket prediction markets for geopolitical probabilities."""

    source = "polymarket_bridge"
    source_tier = "tier_2_operational"
    trust_weight = 0.8
    freshness_ttl_minutes = 5

    def __init__(self, repo_root: Optional[Path] = None, config: Optional[dict] = None):
        super().__init__(repo_root=repo_root, config=config)
        # price history: {condition_id: deque of (timestamp, price)}
        self._price_history: Dict[str, deque] = {}
        self._output_path = self.repo_root / "data" / "quantum_feed" / "polymarket_geopolitical.json"

    def _search_markets(self, tag: str = "geopolitics", active: bool = True) -> List[Dict]:
        """Fetch events from Polymarket Gamma API by tag."""
        if requests is None:
            return []
        try:
            params = {"tag": tag, "active": str(active).lower()}
            resp = requests.get(GAMMA_EVENTS_URL, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json() if isinstance(resp.json(), list) else []
        except Exception as exc:
            logger.warning("Polymarket events fetch failed: %s", exc)
            return []

    def _search_keyword_markets(self, keywords: List[str]) -> List[Dict]:
        """Search markets by keyword to find Iran/war/peace specific markets."""
        if requests is None:
            return []
        results = []
        # Search in batches to avoid overwhelming the API
        search_terms = list(set(keywords[:10]))  # Dedupe and limit
        for term in search_terms:
            try:
                params = {"search": term, "active": "true", "limit": "20"}
                resp = requests.get(GAMMA_MARKETS_URL, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data)
                time.sleep(0.3)  # Rate limit courtesy
            except Exception as exc:
                logger.debug("Polymarket keyword search %s failed: %s", term, exc)
                continue
        # Deduplicate by condition_id
        seen = set()
        unique = []
        for m in results:
            cid = m.get("conditionId") or m.get("condition_id") or m.get("id", "")
            if cid and cid not in seen:
                seen.add(cid)
                unique.append(m)
        return unique

    def _extract_market_data(self, market: Dict) -> Optional[Dict]:
        """Extract structured data from a market/event entry."""
        # Handle both event-level and market-level responses
        title = market.get("title") or market.get("question") or ""
        condition_id = market.get("conditionId") or market.get("condition_id") or market.get("id", "")

        # Price = probability (Polymarket prices are $0.00-$1.00)
        price = None
        for key in ("outcomePrices", "outcome_prices", "bestBid", "best_bid", "lastTradePrice", "last_trade_price"):
            val = market.get(key)
            if val is not None:
                if isinstance(val, str):
                    try:
                        parsed = json.loads(val)
                        if isinstance(parsed, list) and len(parsed) > 0:
                            price = float(parsed[0])
                            break
                    except (json.JSONDecodeError, ValueError):
                        try:
                            price = float(val)
                            break
                        except ValueError:
                            continue
                elif isinstance(val, (int, float)):
                    price = float(val)
                    break
                elif isinstance(val, list) and len(val) > 0:
                    try:
                        price = float(val[0])
                        break
                    except (ValueError, TypeError):
                        continue

        if price is None:
            return None

        probability_pct = round(price * 100, 2) if price <= 1.0 else round(price, 2)

        # Volume
        volume = 0
        for vkey in ("volume", "volumeNum", "volume_num"):
            v = market.get(vkey)
            if v is not None:
                try:
                    volume = float(v)
                    break
                except (ValueError, TypeError):
                    pass

        # Calculate velocity
        velocity = self._calculate_velocity(condition_id, price)

        # Update history
        if condition_id not in self._price_history:
            self._price_history[condition_id] = deque(maxlen=MAX_HISTORY)
        self._price_history[condition_id].append((time.time(), price))

        # Classify relevance
        title_lower = title.lower()
        is_iran = any(kw in title_lower for kw in IRAN_KEYWORDS)
        is_war_peace = any(kw in title_lower for kw in WAR_PEACE_KEYWORDS)

        return {
            "event_name": title,
            "condition_id": str(condition_id),
            "probability_pct": probability_pct,
            "raw_price": round(price, 4),
            "velocity_pct_per_hour": velocity,
            "volume_usd": volume,
            "is_iran_related": is_iran,
            "is_war_peace": is_war_peace,
            "timestamp_utc": utc_now_iso(),
        }

    def _calculate_velocity(self, condition_id: str, current_price: float) -> Optional[float]:
        """Calculate probability velocity — change per hour based on history."""
        history = self._price_history.get(condition_id)
        if not history or len(history) < 2:
            return None
        # Use oldest available point vs current
        oldest_ts, oldest_price = history[0]
        elapsed_hours = (time.time() - oldest_ts) / 3600.0
        if elapsed_hours < 0.01:  # Less than 36 seconds
            return None
        delta = (current_price - oldest_price) * 100  # Convert to percentage points
        velocity = round(delta / elapsed_hours, 2)
        return velocity

    def fetch(self) -> Dict[str, Any]:
        """Fetch all geopolitical markets from Polymarket."""
        try:
            all_markets = []

            # 1. Fetch geopolitics-tagged events
            geo_events = self._search_markets(tag="geopolitics", active=True)
            for event in geo_events:
                # Events contain nested markets
                markets = event.get("markets", [])
                if markets:
                    all_markets.extend(markets)
                else:
                    all_markets.append(event)

            # 2. Search for Iran-specific markets
            iran_markets = self._search_keyword_markets(["iran war", "iran nuclear", "hormuz", "iran peace"])
            all_markets.extend(iran_markets)

            # 3. Search for war/peace markets
            war_peace_markets = self._search_keyword_markets(["ceasefire", "war escalation", "peace deal", "military strike"])
            all_markets.extend(war_peace_markets)

            # Deduplicate
            seen_ids = set()
            unique_markets = []
            for m in all_markets:
                mid = m.get("conditionId") or m.get("condition_id") or m.get("id", "")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    unique_markets.append(m)

            # Extract structured data
            structured = []
            for m in unique_markets:
                data = self._extract_market_data(m)
                if data:
                    structured.append(data)

            # Sort by volume descending
            structured.sort(key=lambda x: x.get("volume_usd", 0), reverse=True)

            # Identify key signals for regime scoring
            peace_signals = [s for s in structured if s.get("is_war_peace") and "peace" in s["event_name"].lower()]
            escalation_signals = [s for s in structured if s.get("is_war_peace") and any(
                kw in s["event_name"].lower() for kw in ["escalat", "war", "attack", "strike", "invasion"]
            )]
            iran_signals = [s for s in structured if s.get("is_iran_related")]

            # Compute aggregate scores for regime integration
            avg_peace_prob = (
                sum(s["probability_pct"] for s in peace_signals) / len(peace_signals)
                if peace_signals else None
            )
            avg_escalation_prob = (
                sum(s["probability_pct"] for s in escalation_signals) / len(escalation_signals)
                if escalation_signals else None
            )
            avg_iran_prob = (
                sum(s["probability_pct"] for s in iran_signals) / len(iran_signals)
                if iran_signals else None
            )

            # Determine velocity alerts
            velocity_alerts = []
            for s in structured:
                v = s.get("velocity_pct_per_hour")
                if v is not None and abs(v) > 5:  # >5% per hour is significant
                    velocity_alerts.append({
                        "event": s["event_name"],
                        "velocity": v,
                        "current_prob": s["probability_pct"],
                        "direction": "SURGING" if v > 0 else "COLLAPSING",
                    })

            payload = {
                "source": self.source,
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight,
                "timestamp_utc": utc_now_iso(),
                "fresh": True,
                "data": {
                    "total_markets_found": len(structured),
                    "markets": structured[:50],  # Top 50 by volume
                    "regime_evidence": {
                        "avg_peace_probability_pct": avg_peace_prob,
                        "avg_escalation_probability_pct": avg_escalation_prob,
                        "avg_iran_probability_pct": avg_iran_prob,
                        "peace_market_count": len(peace_signals),
                        "escalation_market_count": len(escalation_signals),
                        "iran_market_count": len(iran_signals),
                    },
                    "velocity_alerts": velocity_alerts,
                },
            }

            # Write to quantum_feed for other components
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            self._output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            return self._mark_success(payload)

        except Exception as exc:
            logger.exception("PolymarketBridge.fetch failed")
            return self._mark_failure(str(exc))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bridge = PolymarketBridge()
    result = bridge.fetch()
    print(json.dumps(result, indent=2, default=str))
