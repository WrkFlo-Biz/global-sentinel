#!/usr/bin/env python3
"""SEC EDGAR Bridge -- Securities and Exchange Commission filings.

Emits MacroPolicyEvent packets from the SEC EDGAR ATOM feed of recent
filings and the full-text search index.
"""
from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.packets.macro_policy_event import make_macro_policy_event

EDGAR_ATOM = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=8-K&dateb=&owner=include"
    "&count=10&search_text=&action=getcurrent&output=atom"
)
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"

# SEC requires a descriptive User-Agent with contact info
USER_AGENT = "GlobalSentinel research@wrkflo.biz"

# Filing-type relevance weights (higher = more market-moving)
FILING_RELEVANCE: Dict[str, float] = {
    "8-K": 0.90,
    "8-K/A": 0.85,
    "6-K": 0.80,
    "4": 0.75,
    "SC 13D": 0.85,
    "SC 13D/A": 0.80,
    "13-F": 0.60,
    "10-K": 0.50,
    "10-Q": 0.45,
    "S-1": 0.70,
    "DEF 14A": 0.55,
}

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
}


def _detect_form_type(title: str) -> str:
    """Try to extract form type from an ATOM entry title."""
    m = re.match(r"^(\S+)", title.strip())
    return m.group(1) if m else "8-K"


class SECEdgarBridge:
    source = "sec"
    source_tier = "tier_1_official"
    trust_weight = 1.0

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch recent SEC filings and return packet dicts."""
        packets: List[Dict[str, Any]] = []
        packets.extend(self._fetch_atom_feed())
        return packets

    # ------------------------------------------------------------------
    def _fetch_atom_feed(self) -> List[Dict[str, Any]]:
        xml_bytes = self._http_get_raw(EDGAR_ATOM, "atom_8k")
        if not xml_bytes:
            return []

        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return []

        out: List[Dict[str, Any]] = []
        for entry in root.findall("atom:entry", ATOM_NS):
            title_el = entry.find("atom:title", ATOM_NS)
            summary_el = entry.find("atom:summary", ATOM_NS)
            link_el = entry.find("atom:link", ATOM_NS)
            updated_el = entry.find("atom:updated", ATOM_NS)

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            summary_text = summary_el.text.strip() if summary_el is not None and summary_el.text else ""
            link = link_el.get("href", "") if link_el is not None else ""
            updated = updated_el.text.strip() if updated_el is not None and updated_el.text else ""

            form_type = _detect_form_type(title)
            relevance = FILING_RELEVANCE.get(form_type, 0.50)

            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=f"SEC Filing: {title[:120]}",
                policy_domain="regulatory",
                hawkish_dovish_score=0.0,
                growth_inflation_score=0.0,
                market_relevance_score=relevance,
                related_assets=["SPX", "XLF", "IWM"],
                summary=f"{form_type} filing: {summary_text[:250]}",
                confidence=0.90,
                provenance={
                    "url": link,
                    "form_type": form_type,
                    "updated": updated,
                    "api": "edgar_atom",
                },
            )
            out.append(pkt.to_dict())
        return out

    def fetch_search(self, query: str = "material event", days_back: int = 1) -> List[Dict[str, Any]]:
        """Full-text search on EDGAR search index. Returns packet dicts."""
        now = datetime.now(timezone.utc)
        end_dt = now.strftime("%Y-%m-%d")
        from datetime import timedelta
        start_dt = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")

        url = (
            f"{EDGAR_SEARCH}"
            f"?q={urllib.request.quote(query)}"
            f"&dateRange=custom&startdt={start_dt}&enddt={end_dt}"
        )
        data = self._http_get_json(url, f"search:{query}")
        hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []

        out: List[Dict[str, Any]] = []
        for hit in hits:
            src = hit.get("_source", {})
            form_type = src.get("form_type", "")
            relevance = FILING_RELEVANCE.get(form_type, 0.50)
            file_desc = src.get("file_description", "") or src.get("display_names", [""])[0] if src.get("display_names") else ""

            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=f"SEC Search Hit: {file_desc[:120]}",
                policy_domain="regulatory",
                hawkish_dovish_score=0.0,
                growth_inflation_score=0.0,
                market_relevance_score=relevance,
                related_assets=["SPX", "XLF", "IWM"],
                summary=f"{form_type}: {file_desc[:250]}",
                confidence=0.90,
                provenance={
                    "file_num": src.get("file_num", ""),
                    "form_type": form_type,
                    "filed_at": src.get("file_date", ""),
                    "api": "edgar_search",
                },
            )
            out.append(pkt.to_dict())
        return out

    # ------------------------------------------------------------------
    def _http_get_raw(self, url: str, cache_key: str) -> bytes:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                self._cache[cache_key] = raw
                return raw
        except Exception:
            return self._cache.get(cache_key, b"")

    def _http_get_json(self, url: str, cache_key: str) -> Any:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._cache[cache_key] = data
                return data
        except Exception:
            return self._cache.get(cache_key, {})
