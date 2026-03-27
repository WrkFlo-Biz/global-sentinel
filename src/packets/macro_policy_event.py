from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.packets.schemas import MacroPolicyEvent, utc_now_iso


@lru_cache(maxsize=1)
def _schema_versions() -> Dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "config" / "packet_schema_versions.yaml"
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def make_macro_policy_event(
    *,
    source: str,
    source_tier: str,
    trust_weight: float,
    topic: str,
    policy_domain: str,
    hawkish_dovish_score: float,
    growth_inflation_score: float,
    market_relevance_score: float,
    related_assets: List[str],
    summary: str,
    confidence: float,
    provenance: Dict[str, Any],
    schema_version: Optional[str] = None,
) -> MacroPolicyEvent:
    raw = f"{source}|{topic}|{policy_domain}|{summary}"
    packet_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    return MacroPolicyEvent(
        packet_type="macro_policy_event",
        packet_id=packet_id,
        source=source,
        source_tier=source_tier,
        timestamp_utc=utc_now_iso(),
        confidence=confidence,
        trust_weight=trust_weight,
        provenance=provenance,
        schema_version=schema_version or _schema_versions().get("macro_policy_event", "1.0.0"),
        topic=topic,
        policy_domain=policy_domain,
        hawkish_dovish_score=hawkish_dovish_score,
        growth_inflation_score=growth_inflation_score,
        market_relevance_score=market_relevance_score,
        related_assets=related_assets,
        summary=summary,
    )
