#!/usr/bin/env python3
"""
Global Sentinel — OpenClaw Research Bridge

Backup search/research tool that relays queries to OpenClaw bots (mo2darkbot,
mo2drkbot) running on Azure Container Apps.  The bots have research capabilities
including web search via memorySearch and LLM analysis.

Communication path:
    Global Sentinel  →  Telegram sendMessage  →  OpenClaw bot  →  research result
    Global Sentinel  ←  Telegram getUpdates   ←  bot reply

Because results arrive asynchronously, this bridge operates on a cache-forward
model: each fetch() cycle dispatches new queries and returns whatever results
were collected from the *previous* cycle.  This keeps the orchestrator
non-blocking.

Requires env vars:
    TELEGRAM_BOT_TOKEN_DARKBOT  — Bot token for the relay bot
    TELEGRAM_CHAT_ID            — Chat/group where research queries are sent
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.bridges.base_bridge import BaseBridge, utc_now_iso

logger = logging.getLogger("global_sentinel.bridges.openclaw_research")

# ---------------------------------------------------------------------------
# Research query templates — tuned for the categories Global Sentinel cares
# about most.  Each query is prefixed with "/gs_research " so the OpenClaw
# bot recognises it as a programmatic research request.
# ---------------------------------------------------------------------------
RESEARCH_QUERIES: Dict[str, Dict[str, Any]] = {
    "geopolitical_disruption": {
        "query": (
            "/gs_research Summarize the latest geopolitical crises, military "
            "conflicts, and sanctions developments that could move markets today."
        ),
        "weight": 1.0,
    },
    "oil_energy_supply": {
        "query": (
            "/gs_research Report on oil supply disruptions, OPEC decisions, "
            "pipeline incidents, LNG shortages, and energy infrastructure threats."
        ),
        "weight": 0.9,
    },
    "shipping_chokepoints": {
        "query": (
            "/gs_research Identify any shipping lane disruptions, port closures, "
            "Strait of Hormuz or Suez Canal incidents, and supply-chain bottlenecks."
        ),
        "weight": 0.85,
    },
    "defense_military": {
        "query": (
            "/gs_research Summarize defense sector developments: major weapons "
            "contracts, military deployments, NATO activity, and defense-stock catalysts."
        ),
        "weight": 0.8,
    },
    "macro_central_bank": {
        "query": (
            "/gs_research What are the latest central bank signals, rate "
            "decisions, and macro-economic surprises (CPI, jobs, GDP) globally?"
        ),
        "weight": 0.9,
    },
    "cyber_infrastructure": {
        "query": (
            "/gs_research Report on cyberattacks targeting critical infrastructure, "
            "financial systems, or government agencies in the last 24 hours."
        ),
        "weight": 0.75,
    },
}

MAX_QUERIES_PER_CYCLE = 10
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"
REQUEST_TIMEOUT = 15  # seconds


class OpenClawResearchBridge(BaseBridge):
    """Async-tolerant research bridge using OpenClaw bots via Telegram relay."""

    source = "openclaw_research"
    source_tier = "tier_3"
    trust_weight = 0.5
    freshness_ttl_minutes = 30  # results are async; shorter TTL than tier-1

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        config: Optional[dict] = None,
    ):
        super().__init__(repo_root=repo_root, config=config)

        self._bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN_DARKBOT", "")
        self._chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
        self._api_base: str = ""
        if self._bot_token:
            self._api_base = TELEGRAM_API_BASE.format(token=self._bot_token)

        # Cache: category → {text, timestamp, message_id}
        self._result_cache: Dict[str, Dict[str, Any]] = {}

        # Offset for Telegram getUpdates (ensures we don't re-read messages)
        self._update_offset: int = 0

        # Track queries dispatched this cycle to enforce rate limit
        self._queries_this_cycle: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(self) -> Dict[str, Any]:
        """Dispatch research queries and return cached results from prior cycles."""
        if not self._bot_token or not self._chat_id:
            return self._mark_failure(
                "TELEGRAM_BOT_TOKEN_DARKBOT or TELEGRAM_CHAT_ID not set"
            )

        try:
            # 1. Collect any results that arrived since last cycle
            self._check_research_results()

            # 2. Dispatch new queries (rate-limited)
            self._queries_this_cycle = 0
            dispatched: List[str] = []
            for category, meta in RESEARCH_QUERIES.items():
                if self._queries_this_cycle >= MAX_QUERIES_PER_CYCLE:
                    break
                ok = self._send_research_query(meta["query"], category)
                if ok:
                    dispatched.append(category)

            # 3. Build payload from cache
            events = self._build_events_from_cache()

            payload = {
                "source": self.source,
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight,
                "timestamp_utc": utc_now_iso(),
                "fresh": True,
                "queries_dispatched": dispatched,
                "cached_categories": list(self._result_cache.keys()),
                "events": events,
                "event_count": len(events),
            }
            return self._mark_success(payload)

        except Exception as exc:
            logger.exception("OpenClaw research bridge fetch failed")
            return self._mark_failure(str(exc))

    # ------------------------------------------------------------------
    # Telegram helpers
    # ------------------------------------------------------------------

    def _send_research_query(self, query: str, category: str) -> bool:
        """POST a research query to the Telegram chat. Returns True on success."""
        if self._queries_this_cycle >= MAX_QUERIES_PER_CYCLE:
            return False

        url = f"{self._api_base}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": query,
            "parse_mode": "Markdown",
        }

        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            self._queries_this_cycle += 1
            logger.debug("Dispatched research query [%s]", category)
            return True
        except requests.RequestException as exc:
            logger.warning(
                "Failed to send research query [%s]: %s", category, exc
            )
            return False

    def _check_research_results(self) -> None:
        """Poll Telegram getUpdates for bot replies and populate cache."""
        url = f"{self._api_base}/getUpdates"
        params: Dict[str, Any] = {
            "offset": self._update_offset,
            "timeout": 1,  # short poll — non-blocking
            "allowed_updates": ["message"],
        }

        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            logger.warning("getUpdates failed: %s", exc)
            return

        if not data.get("ok"):
            return

        for update in data.get("result", []):
            update_id = update.get("update_id", 0)
            if update_id >= self._update_offset:
                self._update_offset = update_id + 1

            msg = update.get("message", {})
            text = msg.get("text", "")
            from_user = msg.get("from", {})

            # Only accept messages from the bot itself (research replies)
            if not from_user.get("is_bot", False):
                continue

            # Classify the reply into a category based on keywords
            category = self._classify_reply(text)
            if category:
                self._result_cache[category] = {
                    "text": text,
                    "timestamp": utc_now_iso(),
                    "message_id": msg.get("message_id"),
                    "from_bot": from_user.get("username", "unknown"),
                }
                logger.info(
                    "Cached research result for [%s] from @%s",
                    category,
                    from_user.get("username"),
                )

    def _classify_reply(self, text: str) -> Optional[str]:
        """Best-effort classification of a bot reply into a research category."""
        if not text:
            return None

        text_lower = text.lower()

        keyword_map = {
            "geopolitical_disruption": [
                "geopolitical", "sanctions", "conflict", "military strike",
                "war", "diplomatic",
            ],
            "oil_energy_supply": [
                "oil", "opec", "crude", "energy", "lng", "pipeline",
                "brent", "wti", "refinery",
            ],
            "shipping_chokepoints": [
                "shipping", "hormuz", "suez", "port", "chokepoint",
                "tanker", "freight",
            ],
            "defense_military": [
                "defense", "defence", "weapons", "nato", "military contract",
                "pentagon", "arms",
            ],
            "macro_central_bank": [
                "central bank", "fed ", "ecb", "rate decision", "cpi",
                "inflation", "gdp", "jobs report", "fomc",
            ],
            "cyber_infrastructure": [
                "cyber", "ransomware", "hack", "breach", "infrastructure attack",
            ],
        }

        best_category: Optional[str] = None
        best_hits = 0
        for category, keywords in keyword_map.items():
            hits = sum(1 for kw in keywords if kw in text_lower)
            if hits > best_hits:
                best_hits = hits
                best_category = category

        return best_category if best_hits > 0 else None

    # ------------------------------------------------------------------
    # Payload construction
    # ------------------------------------------------------------------

    def _build_events_from_cache(self) -> List[Dict[str, Any]]:
        """Convert cached results into normalized search_event packets."""
        events: List[Dict[str, Any]] = []

        for category, cached in self._result_cache.items():
            text = cached.get("text", "")
            if not text:
                continue

            event_id = hashlib.sha256(
                f"{category}:{cached.get('timestamp', '')}:{text[:120]}".encode()
            ).hexdigest()[:16]

            weight = RESEARCH_QUERIES.get(category, {}).get("weight", 0.5)

            events.append({
                "event_id": f"ocr_{event_id}",
                "source": self.source,
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight * weight,
                "category": category,
                "headline": text[:200],
                "body": text,
                "timestamp_utc": cached.get("timestamp", utc_now_iso()),
                "from_bot": cached.get("from_bot", "unknown"),
                "async_result": True,
            })

        return events


# -----------------------------------------------------------------------
# Standalone smoke test
# -----------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    bridge = OpenClawResearchBridge()
    result = bridge.fetch()
    print(f"Health: {bridge.health()}")
    print(f"Events: {result.get('event_count', 0)}")
    print(f"Dispatched: {result.get('queries_dispatched', [])}")
    print(f"Cached categories: {result.get('cached_categories', [])}")
    if result.get("error"):
        print(f"Error: {result['error']}")
