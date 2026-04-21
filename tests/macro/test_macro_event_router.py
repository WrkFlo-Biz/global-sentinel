"""Tests for macro router canonical ledger integration."""
from __future__ import annotations

from src.macro.macro_event_router import MacroEventRouter


def _macro_event(**overrides):
    event = {
        "schema_version": "macro_policy_event.v1",
        "event_type": "inflation_release",
        "headline": "US CPI surprise drives rates repricing",
        "source_domain": "bls.gov",
        "source_url": "https://www.bls.gov/news.release/cpi.nr0.htm",
        "source_tier": "tier_a_official",
        "source_type": "api",
        "source_confidence": 0.2,
        "policy_release_urgency_score": 0.7,
        "official_source": True,
        "official_source_confirmed": True,
    }
    event.update(overrides)
    return event


def test_route_attaches_canonical_fields_when_missing():
    router = MacroEventRouter()
    out = router.route([_macro_event()])

    top = out["macro_events_priority_top"]
    assert len(top) == 1
    winner = top[0]
    assert winner["canonical_event_id"].startswith("evt_")
    assert winner["canonical_dedupe_key"] == winner["event_ledger"]["dedupe_key"]
    assert winner["source_credibility"] is not None


def test_dedupe_prefers_higher_canonical_novelty_and_source_credibility():
    router = MacroEventRouter()
    low = _macro_event(source_credibility=0.20, source_url="https://www.bls.gov/news.release/cpi.nr0.htm?a=1")
    high = _macro_event(source_credibility=0.95, source_url="https://www.bls.gov/news.release/cpi.nr0.htm?a=2")

    out = router.route([low, high])
    top = out["macro_events_priority_top"]
    suppressed = out["macro_events_suppressed_duplicates"]

    assert len(top) == 1
    assert len(suppressed) == 1

    winner = top[0]
    loser = suppressed[0]
    assert float(winner["source_credibility"]) > float(loser["source_credibility"])
    assert winner["_router"]["canonical_novelty_score"] >= loser["_router"]["canonical_novelty_score"]
    assert loser["_router"]["suppressed_by_event_id"] == winner["canonical_event_id"]


def test_dedupe_uses_explicit_canonical_dedupe_key():
    router = MacroEventRouter()
    event_a = _macro_event(
        headline="Variant A headline",
        source_url="https://a.example.com",
        canonical_event_id="evt_variant_a",
        canonical_dedupe_key="dedupe-shared-key",
        source_credibility=0.10,
        canonical_novelty_score=0.15,
    )
    event_b = _macro_event(
        headline="Variant B headline",
        source_url="https://b.example.com",
        canonical_event_id="evt_variant_b",
        canonical_dedupe_key="dedupe-shared-key",
        source_credibility=0.90,
        canonical_novelty_score=0.90,
    )

    out = router.route([event_a, event_b])
    top = out["macro_events_priority_top"]
    suppressed = out["macro_events_suppressed_duplicates"]

    assert len(top) == 1
    assert len(suppressed) == 1
    assert top[0]["canonical_event_id"] == "evt_variant_b"
    assert suppressed[0]["_router"]["suppressed_by_event_id"] == "evt_variant_b"
