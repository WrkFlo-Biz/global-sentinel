"""Tests for freshness governance modules."""
from datetime import datetime, timezone, timedelta
from src.core.event_clock import EventClock
from src.core.packet_dedup_index import PacketDedupIndex
from src.core.late_packet_handler import LatePacketHandler


def test_event_clock_annotates():
    clock = EventClock(max_lag_minutes=60)
    pkt = {"packet_id": "test1", "timestamp_utc": datetime.now(timezone.utc).isoformat()}
    result = clock.annotate_packet(pkt)
    assert "_event_clock" in result
    assert result["_event_clock"]["lag_minutes"] is not None
    assert not result["_event_clock"]["stale"]


def test_event_clock_stale():
    clock = EventClock(max_lag_minutes=5)
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    pkt = {"packet_id": "test2", "timestamp_utc": old_time}
    result = clock.annotate_packet(pkt)
    assert result["_event_clock"]["stale"] is True
    assert clock.is_stale(result)


def test_event_clock_missing_time():
    clock = EventClock()
    pkt = {"packet_id": "test3"}
    result = clock.annotate_packet(pkt)
    assert result["_event_clock"]["missing_event_time"] is True


def test_dedup_index_new_packet():
    idx = PacketDedupIndex()
    assert idx.record("pkt_001") is True
    assert idx.size == 1


def test_dedup_index_duplicate():
    idx = PacketDedupIndex()
    idx.record("pkt_001")
    assert idx.record("pkt_001") is False
    assert idx.is_duplicate("pkt_001")


def test_dedup_index_max_entries():
    idx = PacketDedupIndex(max_entries=5)
    for i in range(10):
        idx.record(f"pkt_{i:03d}")
    assert idx.size == 5


def test_late_packet_annotate(tmp_path):
    import yaml
    policy = {
        "sources": {
            "gdelt": {"stale_action": "annotate_stale", "stale_trust_weight_override": 0.3}
        }
    }
    (tmp_path / "freshness_policy.yaml").write_text(yaml.dump(policy))
    handler = LatePacketHandler(config_dir=tmp_path)
    pkt = {"packet_id": "p1", "source": "gdelt", "trust_weight": 0.8}
    result = handler.handle(pkt)
    assert result["_late_packet"]["action"] == "annotated_stale"


def test_late_packet_degrade(tmp_path):
    import yaml
    policy = {
        "sources": {
            "fed": {"stale_action": "degrade_trust_weight", "stale_trust_weight_override": 0.5}
        }
    }
    (tmp_path / "freshness_policy.yaml").write_text(yaml.dump(policy))
    handler = LatePacketHandler(config_dir=tmp_path)
    pkt = {"packet_id": "p2", "source": "fed", "trust_weight": 1.0}
    result = handler.handle(pkt)
    assert result["trust_weight"] == 0.5
    assert result["_late_packet"]["action"] == "degraded"


def test_late_packet_discard(tmp_path):
    import yaml
    policy = {
        "sources": {
            "test": {"stale_action": "discard"}
        }
    }
    (tmp_path / "freshness_policy.yaml").write_text(yaml.dump(policy))
    handler = LatePacketHandler(config_dir=tmp_path)
    pkt = {"packet_id": "p3", "source": "test"}
    result = handler.handle(pkt)
    assert result["_late_packet"]["action"] == "discarded"
