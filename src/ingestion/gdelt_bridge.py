from __future__ import annotations

import os
import urllib.request
import json
from typing import Any, Dict, List

from src.packets.geopolitical_event import make_geopolitical_event


GDELT_DOC_API = os.environ.get("GDELT_DOC_API", "https://api.gdeltproject.org/api/v2/doc/doc")


class GDELTBridge:
    source = "gdelt"
    source_tier = "tier_2_operational"
    trust_weight = 0.8

    def fetch(self, query: str = "Iran OR Strait of Hormuz OR sanctions", max_records: int = 10) -> List[Dict[str, Any]]:
        params = urllib.request.quote(query, safe="")
        url = f"{GDELT_DOC_API}?query={params}&mode=artlist&maxrecords={max_records}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/5.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        articles = data.get("articles", [])

        out: List[Dict[str, Any]] = []
        for row in articles:
            title = row.get("title", "")
            article_url = row.get("url", "")
            summary = row.get("seendate", "") + " " + title

            pkt = make_geopolitical_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                region=self._infer_region(title),
                severity=self._severity_score(title),
                event_category=self._category(title),
                energy_relevance=self._energy_relevance(title),
                supply_chain_relevance=self._supply_chain_relevance(title),
                asset_channels=["CL", "XLE", "TLT", "GLD", "EEM"],
                summary=summary,
                confidence=0.75,
                provenance={"url": article_url, "title": title},
            )
            out.append(pkt.to_dict())
        return out

    def _infer_region(self, text: str) -> str:
        t = text.lower()
        if "iran" in t or "hormuz" in t:
            return "Middle East"
        if "china" in t or "taiwan" in t:
            return "East Asia"
        if "russia" in t or "ukraine" in t:
            return "Eastern Europe"
        return "Global"

    def _severity_score(self, text: str) -> float:
        t = text.lower()
        sev = 0.3
        if any(w in t for w in ["attack", "strike", "explosion", "missile", "blockade"]):
            sev += 0.5
        return min(sev, 1.0)

    def _category(self, text: str) -> str:
        t = text.lower()
        if any(w in t for w in ["sanction", "tariff"]):
            return "policy"
        if any(w in t for w in ["attack", "strike", "missile", "blockade"]):
            return "kinetic"
        return "narrative"

    def _energy_relevance(self, text: str) -> float:
        t = text.lower()
        return 1.0 if any(w in t for w in ["oil", "hormuz", "refinery", "crude"]) else 0.3

    def _supply_chain_relevance(self, text: str) -> float:
        t = text.lower()
        return 1.0 if any(w in t for w in ["shipping", "port", "vessel", "logistics"]) else 0.2
