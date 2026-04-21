"""Tests for feature store lineage subsystem."""
from src.research.feature_store.feature_group_registry import FeatureGroupRegistry
from src.research.feature_store.point_in_time_joiner import PointInTimeJoiner
from src.research.feature_store.feature_lineage_tracker import FeatureLineageTracker
from src.research.feature_store.dataset_manifest import DatasetManifest


def test_feature_group_registration():
    reg = FeatureGroupRegistry()
    entry = reg.register_group("macro_features", "1.0.0", {"fields": ["hawkish_score"]}, ["fed", "fred"])
    assert entry["name"] == "macro_features"
    assert entry["version"] == "1.0.0"


def test_feature_group_latest():
    reg = FeatureGroupRegistry()
    reg.register_group("macro", "1.0.0", {}, ["fed"])
    reg.register_group("macro", "1.1.0", {}, ["fed", "fred"])
    latest = reg.get_group("macro")
    assert latest["version"] == "1.1.0"


def test_feature_group_deprecation():
    reg = FeatureGroupRegistry()
    reg.register_group("old", "1.0.0", {}, ["fed"])
    assert reg.deprecate_group("old", "1.0.0")
    group = reg.get_group("old")
    assert group["deprecated_at"] is not None


def test_feature_group_not_found():
    reg = FeatureGroupRegistry()
    assert reg.get_group("nonexistent") is None


def test_point_in_time_no_future_leakage():
    joiner = PointInTimeJoiner()
    events = [
        {"symbol": "XOM", "timestamp_utc": "2026-01-15T10:00:00Z", "price": 100},
    ]
    features = [
        {"symbol": "XOM", "timestamp_utc": "2026-01-14T10:00:00Z", "vol": 0.3},
        {"symbol": "XOM", "timestamp_utc": "2026-01-16T10:00:00Z", "vol": 0.5},  # FUTURE
    ]
    result = joiner.join(events, features)
    assert len(result) == 1
    assert result[0].get("feat_vol") == 0.3  # Should get Jan 14 (before event), NOT Jan 16


def test_point_in_time_no_match():
    joiner = PointInTimeJoiner()
    events = [{"symbol": "AAPL", "timestamp_utc": "2026-01-10T00:00:00Z"}]
    features = [{"symbol": "XOM", "timestamp_utc": "2026-01-09T00:00:00Z", "vol": 0.2}]
    result = joiner.join(events, features)
    assert result[0]["_pit_join"]["matched"] is False


def test_lineage_tracker_roundtrip():
    tracker = FeatureLineageTracker()
    h = tracker.record_computation(
        input_packet_ids=["pkt_abc", "pkt_def"],
        feature_group_name="macro_features",
        feature_group_version="1.0.0",
        output_features={"hawkish": 0.6, "growth": 0.3},
    )
    lineage = tracker.get_lineage(h)
    assert lineage is not None
    assert lineage["feature_group_name"] == "macro_features"
    assert "pkt_abc" in lineage["input_packet_ids"]


def test_lineage_chain():
    tracker = FeatureLineageTracker()
    h = tracker.record_computation(["p1"], "g1", "1.0", {"x": 1})
    chain = tracker.get_lineage_chain(h)
    assert len(chain) == 1


def test_dataset_manifest():
    manifest = DatasetManifest()
    rows = [
        {"symbol": "XOM", "alpha_label": "positive"},
        {"symbol": "AAPL", "alpha_label": "negative"},
        {"symbol": "NVDA", "alpha_label": "positive"},
    ]
    m = manifest.create(rows, {"macro": "1.0.0"}, environment="dev")
    assert m["row_count"] == 3
    assert m["label_distribution"]["positive"] == 2
    assert m["label_distribution"]["negative"] == 1
    assert m["manifest_id"]
    assert m["manifest_hash"]


def test_dataset_manifest_deterministic():
    manifest = DatasetManifest()
    rows = [{"symbol": "XOM"}]
    m1 = manifest.create(rows, {})
    m2 = manifest.create(rows, {})
    assert m1["manifest_id"] == m2["manifest_id"]


def test_list_groups():
    reg = FeatureGroupRegistry()
    reg.register_group("a", "1.0", {}, ["fed"])
    reg.register_group("b", "1.0", {}, ["fred"])
    groups = reg.list_groups()
    assert len(groups) == 2
