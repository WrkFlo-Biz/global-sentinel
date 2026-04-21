from __future__ import annotations

from pathlib import Path

from src.bridges.base_bridge import BaseBridge
from src.bridges.bridge_registry import BridgeRegistry
from src.bridges.adapters.sec_filing_adapter import SECFilingAdapter


REPO_ROOT = Path(__file__).resolve().parents[2]


class DummyBridge(BaseBridge):
    source = "dummy"
    source_tier = "tier_2_operational"
    trust_weight = 0.8

    def fetch(self):
        return self._mark_success(
            {
                "source": self.source,
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight,
                "timestamp_utc": "2026-03-08T00:00:00+00:00",
                "fresh": True,
                "data": {"value": 1},
            }
        )


def test_registry_declares_all_expected_sources():
    registry = BridgeRegistry(repo_root=REPO_ROOT)
    names = list(registry.names())
    assert len(names) == 21
    assert "fed_bridge" in names
    assert "noaa_bridge" in names
    assert "sec_filing_event_scorer" in names


def test_registry_health_contract():
    registry = BridgeRegistry(repo_root=REPO_ROOT)
    health = registry.health_all()
    assert set(health.keys()) == set(registry.names())
    for name, item in health.items():
        assert item["source"] == name
        assert "source_tier" in item
        assert "trust_weight" in item
        assert "fresh" in item


def test_fetch_all_uses_bridge_contract():
    registry = BridgeRegistry(repo_root=REPO_ROOT)
    registry._bridges = {  # type: ignore[attr-defined]
        "dummy_bridge": DummyBridge(repo_root=REPO_ROOT),
    }
    results = registry.fetch_all(names=["dummy_bridge"])
    payload = results["dummy_bridge"]
    assert payload["source"] == "dummy"
    assert payload["fresh"] is True
    assert payload["data"] == {"value": 1}


def test_sec_filing_adapter_contract():
    adapter = SECFilingAdapter(repo_root=REPO_ROOT)
    adapter._edgar.fetch = lambda: [  # type: ignore[method-assign]
        {
            "topic": "8-K Filing",
            "summary": "Material event disclosed.",
            "packet_id": "pkt-1",
            "source": "sec",
            "provenance": {"url": "https://example.test"},
        }
    ]
    payload = adapter.fetch()
    assert payload["source"] == "sec_filing_event_scorer"
    assert payload["source_tier"] == "tier_3_research"
    assert payload["trust_weight"] == 0.5
    assert payload["fresh"] is True
    assert payload["data"]["filing_count"] == 1
    assert payload["data"]["scored_count"] == 1
