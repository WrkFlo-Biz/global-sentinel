"""Tests for canonical event-ledger behavior."""
from __future__ import annotations

from src.research.event_ledger import (
    attach_event_ledger,
    build_event_ledger,
    score_event_novelty,
)


def _base_packet(**overrides):
    packet = {
        "schema_version": "macro_policy_event.v1",
        "event_type": "inflation_release",
        "headline": "US CPI surprises to the upside",
        "source_domain": "bls.gov",
        "source_url": "https://www.bls.gov/news.release/cpi.nr0.htm",
        "source_tier": "tier_a_official",
        "source_type": "api",
        "official_source": True,
        "official_source_confirmed": True,
        "timestamp_utc": "2026-04-09T12:00:00Z",
    }
    packet.update(overrides)
    return packet


def test_attach_event_ledger_populates_canonical_ids_and_dedupe_keys():
    out = attach_event_ledger(_base_packet(), bridge_name="bls_bridge")

    assert out["canonical_event_id"].startswith("evt_")
    assert out["event_id"] == out["canonical_event_id"]
    assert out["canonical_dedupe_key"] == out["event_ledger"]["dedupe_key"]
    assert out["dedupe_key"] == out["canonical_dedupe_key"]
    assert 0.0 <= float(out["source_credibility"]) <= 1.0


def test_build_event_ledger_prefers_explicit_source_credibility():
    ledger = build_event_ledger(_base_packet(source_credibility=0.87, source_confidence=0.12))

    assert ledger["source_credibility"] == 0.87
    assert ledger["canonical_source"]["source_credibility"] == 0.87


def test_score_event_novelty_can_compute_without_prebuilt_ledger():
    hi_packet = _base_packet(source_credibility=0.95)
    lo_packet = _base_packet(source_credibility=0.15)

    hi_score, _, _ = score_event_novelty(hi_packet)
    lo_score, _, _ = score_event_novelty(lo_packet)

    assert hi_score > lo_score


def test_canonical_event_id_is_stable_when_raw_event_id_changes():
    ledger_a = build_event_ledger(_base_packet(event_id="raw-a"))
    ledger_b = build_event_ledger(_base_packet(event_id="raw-b"))

    assert ledger_a["canonical_event_id"] == ledger_b["canonical_event_id"]
    assert ledger_a["canonical_dedupe_key"] == ledger_b["canonical_dedupe_key"]
