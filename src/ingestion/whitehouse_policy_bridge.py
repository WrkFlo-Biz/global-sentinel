#!/usr/bin/env python3
"""White House Policy Bridge -- Executive orders, statements, briefings.

Emits MacroPolicyEvent packets from the White House WP-JSON API and
the Federal Register executive orders endpoint.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from typing import Any, Dict, List

from src.packets.macro_policy_event import make_macro_policy_event

WH_API = "https://www.whitehouse.gov/wp-json/wp/v2/posts?per_page=10"
FED_REG_EO = (
    "https://www.federalregister.gov/api/v1/documents.json"
    "?conditions[type][]=PRESDOCU"
    "&conditions[presidential_document_type][]=executive_order"
    "&per_page=10&order=newest"
)

HAWKISH_KEYWORDS = [
    "tariff", "sanction", "restrict", "ban", "enforce", "penalty",
    "security", "defense", "military", "crackdown", "tax",
]
DOVISH_KEYWORDS = [
    "invest", "stimulus", "relief", "aid", "subsidy", "grant",
    "infrastructure", "partnership", "cooperation", "trade deal",
]


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    clean = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(clean).strip()


def _score_hawkish_dovish(text: str) -> float:
    """Return score in [-1.0 (dovish), +1.0 (hawkish)]."""
    lower = text.lower()
    hawk = sum(1 for kw in HAWKISH_KEYWORDS if kw in lower)
    dove = sum(1 for kw in DOVISH_KEYWORDS if kw in lower)
    total = hawk + dove
    if total == 0:
        return 0.0
    return round((hawk - dove) / total, 3)


class WhiteHousePolicyBridge:
    source = "whitehouse"
    source_tier = "tier_1_official"
    trust_weight = 1.0

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch White House posts and Federal Register EOs, return packet dicts."""
        packets: List[Dict[str, Any]] = []
        packets.extend(self._fetch_wh_posts())
        packets.extend(self._fetch_executive_orders())
        return packets

    # ------------------------------------------------------------------
    def _fetch_wh_posts(self) -> List[Dict[str, Any]]:
        posts = self._http_get(WH_API, "wh_posts")
        if not isinstance(posts, list):
            return []

        out: List[Dict[str, Any]] = []
        for post in posts:
            title = _strip_html(post.get("title", {}).get("rendered", ""))
            excerpt = _strip_html(post.get("excerpt", {}).get("rendered", ""))
            combined = f"{title} {excerpt}"
            score = _score_hawkish_dovish(combined)

            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=title or "White House post",
                policy_domain="executive_policy",
                hawkish_dovish_score=score,
                growth_inflation_score=0.0,
                market_relevance_score=0.7,
                related_assets=["SPX", "DXY", "UST10Y", "XLE", "XLI"],
                summary=f"{title}: {excerpt[:200]}",
                confidence=0.85,
                provenance={
                    "url": post.get("link", ""),
                    "published": post.get("date", ""),
                    "api": "whitehouse_wp_json",
                },
            )
            out.append(pkt.to_dict())
        return out

    def _fetch_executive_orders(self) -> List[Dict[str, Any]]:
        data = self._http_get(FED_REG_EO, "exec_orders")
        results = data.get("results", []) if isinstance(data, dict) else []

        out: List[Dict[str, Any]] = []
        for eo in results:
            title = eo.get("title", "")
            abstract = eo.get("abstract", "") or ""
            combined = f"{title} {abstract}"
            score = _score_hawkish_dovish(combined)

            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=f"Executive Order: {title}",
                policy_domain="executive_policy",
                hawkish_dovish_score=score,
                growth_inflation_score=0.0,
                market_relevance_score=0.85,
                related_assets=["SPX", "DXY", "UST10Y", "XLE", "XLI"],
                summary=f"EO: {title}. {abstract[:200]}",
                confidence=0.85,
                provenance={
                    "url": eo.get("html_url", ""),
                    "document_number": eo.get("document_number", ""),
                    "publication_date": eo.get("publication_date", ""),
                    "api": "federal_register",
                },
            )
            out.append(pkt.to_dict())
        return out

    # ------------------------------------------------------------------
    def _http_get(self, url: str, cache_key: str) -> Any:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "GlobalSentinel/5.1"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._cache[cache_key] = data
                return data
        except Exception:
            return self._cache.get(cache_key, [])
