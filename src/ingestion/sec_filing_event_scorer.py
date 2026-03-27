"""Score SEC filing significance for research and candidate ranking."""
from __future__ import annotations

from typing import Any, Dict


class SECFilingEventScorer:

    FORM_WEIGHTS = {
        "8-K": 1.00,
        "10-Q": 0.90,
        "10-K": 0.95,
        "6-K": 0.75,
        "20-F": 0.80,
        "13D": 0.85,
        "13G": 0.70,
        "S-1": 0.75,
        "424B2": 0.70,
    }

    def score(self, filing_packet: Dict[str, Any]) -> Dict[str, Any]:
        topic = str(filing_packet.get("topic", ""))
        summary = str(filing_packet.get("summary", ""))
        provenance = filing_packet.get("provenance") or {}

        form = self._extract_form(topic)
        form_weight = self.FORM_WEIGHTS.get(form, 0.50)

        event_keywords = 0.0
        text = f"{topic} {summary}".lower()
        if any(w in text for w in ["guidance", "material", "acquisition", "impairment", "restatement"]):
            event_keywords += 0.20
        if any(w in text for w in ["investigation", "bankruptcy", "liquidity", "covenant"]):
            event_keywords += 0.25

        significance = min(form_weight + event_keywords, 1.0)

        return {
            "filing_form": form,
            "filing_significance_score": significance,
            "packet_id": filing_packet.get("packet_id"),
            "source": filing_packet.get("source"),
            "provenance": provenance,
        }

    def _extract_form(self, topic: str) -> str:
        for form in self.FORM_WEIGHTS:
            if form in topic:
                return form
        return "UNKNOWN"
