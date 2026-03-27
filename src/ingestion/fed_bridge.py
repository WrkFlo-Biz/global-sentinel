from __future__ import annotations

import os
import re
import urllib.request
from typing import Any, Dict, List

from src.packets.macro_policy_event import make_macro_policy_event


FED_FEED_URL = os.environ.get("FED_FEED_URL", "https://www.federalreserve.gov/feeds/press_all.xml")


class FedBridge:
    source = "fed"
    source_tier = "tier_1_official"
    trust_weight = 1.0

    def fetch(self) -> List[Dict[str, Any]]:
        req = urllib.request.Request(FED_FEED_URL, headers={"User-Agent": "GlobalSentinel/5.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8")

        items: List[Dict[str, Any]] = []
        for m in re.finditer(r"<title>(.*?)</title>.*?<description>(.*?)</description>", text, re.DOTALL):
            title = re.sub(r"<.*?>", "", m.group(1)).strip()
            desc = re.sub(r"<.*?>", "", m.group(2)).strip()

            hawkish = self._score_hawkish_dovish(f"{title} {desc}")
            gi = self._score_growth_inflation(f"{title} {desc}")
            mr = self._score_market_relevance(f"{title} {desc}")

            pkt = make_macro_policy_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                topic=title,
                policy_domain="monetary_policy",
                hawkish_dovish_score=hawkish,
                growth_inflation_score=gi,
                market_relevance_score=mr,
                related_assets=["UST10Y", "DXY", "SPX", "XLF", "GLD"],
                summary=desc[:500],
                confidence=0.95,
                provenance={"feed_url": FED_FEED_URL},
            )
            items.append(pkt.to_dict())
        return items

    def _score_hawkish_dovish(self, text: str) -> float:
        t = text.lower()
        hawk = sum(1 for w in ["inflation", "higher for longer", "restrictive", "tightening"] if w in t)
        dove = sum(1 for w in ["cut", "easing", "downside risks", "support growth"] if w in t)
        return float(hawk - dove)

    def _score_growth_inflation(self, text: str) -> float:
        t = text.lower()
        inflation = sum(1 for w in ["inflation", "price stability", "wages"] if w in t)
        growth = sum(1 for w in ["employment", "growth", "labor market"] if w in t)
        return float(inflation - growth)

    def _score_market_relevance(self, text: str) -> float:
        t = text.lower()
        score = 0.5
        if "fomc" in t or "statement" in t:
            score += 0.3
        if "rates" in t or "inflation" in t:
            score += 0.2
        return min(score, 1.0)
