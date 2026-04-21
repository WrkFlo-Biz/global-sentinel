"""Tests for ArtifactManifestBuilder and LineageResolver."""
from __future__ import annotations

import pytest
from src.lineage.artifact_manifest_builder import (
    ArtifactManifest,
    ArtifactManifestBuilder,
    LineageResolver,
)


def test_manifest_has_required_fields():
    m = ArtifactManifest(artifact_type="research_score")
    d = m.to_dict()
    assert d["schema_version"] == "artifact_manifest.v1"
    assert d["artifact_type"] == "research_score"
    assert d["created_at"]
    assert d["artifact_id"]
    assert d["not_for_direct_execution"] is True


def test_manifest_auto_generates_id():
    m = ArtifactManifest(artifact_type="feature_set")
    assert len(m.artifact_id) == 16


def test_manifest_deterministic_id():
    m1 = ArtifactManifest(artifact_type="test", created_at="2026-03-07T00:00:00Z",
                           parent_artifact_ids=["p1"])
    m2 = ArtifactManifest(artifact_type="test", created_at="2026-03-07T00:00:00Z",
                           parent_artifact_ids=["p1"])
    assert m1.artifact_id == m2.artifact_id


def test_builder_chain():
    builder = ArtifactManifestBuilder()
    manifest = (builder
        .set_type("learning_state")
        .set_parents(["parent_abc"])
        .set_source_packets(["pkt_1", "pkt_2"])
        .set_feature_version("encoder_v3")
        .set_model_version("weights_v2")
        .set_code_version("abc1234")
        .set_incident_mode("NORMAL")
        .set_time_window("2026-03-07T00:00Z/2026-03-07T12:00Z")
        .set_content_hash({"test": "data"})
        .build())

    assert manifest.artifact_type == "learning_state"
    assert manifest.parent_artifact_ids == ["parent_abc"]
    assert manifest.source_packet_hashes == ["pkt_1", "pkt_2"]
    assert manifest.feature_version == "encoder_v3"
    assert manifest.code_version == "abc1234"
    assert manifest.content_hash


def test_builder_resets_after_build():
    builder = ArtifactManifestBuilder()
    builder.set_type("first").build()
    m2 = builder.set_type("second").build()
    assert m2.artifact_type == "second"
    assert m2.parent_artifact_ids == []


def test_builder_add_parent():
    builder = ArtifactManifestBuilder()
    manifest = (builder
        .set_type("test")
        .add_parent("p1")
        .add_parent("p2")
        .build())
    assert manifest.parent_artifact_ids == ["p1", "p2"]


def test_manifest_to_json():
    m = ArtifactManifest(artifact_type="test")
    j = m.to_json()
    assert '"artifact_type": "test"' in j


def test_lineage_resolver_register_and_get():
    resolver = LineageResolver()
    m = ArtifactManifest(artifact_type="test")
    resolver.register(m)
    assert resolver.get(m.artifact_id) is m
    assert resolver.manifest_count == 1


def test_lineage_resolver_get_parents():
    resolver = LineageResolver()
    parent = ArtifactManifest(artifact_type="parent", artifact_id="p1")
    child = ArtifactManifest(artifact_type="child", artifact_id="c1",
                             parent_artifact_ids=["p1"])
    resolver.register(parent)
    resolver.register(child)
    parents = resolver.get_parents("c1")
    assert len(parents) == 1
    assert parents[0].artifact_id == "p1"


def test_lineage_resolver_get_children():
    resolver = LineageResolver()
    parent = ArtifactManifest(artifact_type="parent", artifact_id="p1")
    child = ArtifactManifest(artifact_type="child", artifact_id="c1",
                             parent_artifact_ids=["p1"])
    resolver.register(parent)
    resolver.register(child)
    children = resolver.get_children("p1")
    assert len(children) == 1
    assert children[0].artifact_id == "c1"


def test_lineage_ancestry():
    resolver = LineageResolver()
    gp = ArtifactManifest(artifact_type="gp", artifact_id="gp1")
    parent = ArtifactManifest(artifact_type="parent", artifact_id="p1",
                              parent_artifact_ids=["gp1"])
    child = ArtifactManifest(artifact_type="child", artifact_id="c1",
                             parent_artifact_ids=["p1"])
    resolver.register(gp)
    resolver.register(parent)
    resolver.register(child)
    ancestry = resolver.get_ancestry("c1")
    ids = [a.artifact_id for a in ancestry]
    assert "c1" in ids
    assert "p1" in ids
    assert "gp1" in ids


def test_lineage_validate_complete():
    resolver = LineageResolver()
    parent = ArtifactManifest(artifact_type="parent", artifact_id="p1")
    child = ArtifactManifest(artifact_type="child", artifact_id="c1",
                             parent_artifact_ids=["p1"])
    resolver.register(parent)
    resolver.register(child)
    result = resolver.validate_lineage("c1")
    assert result["valid"] is True
    assert result["missing_parents"] == []


def test_lineage_validate_broken():
    resolver = LineageResolver()
    child = ArtifactManifest(artifact_type="child", artifact_id="c1",
                             parent_artifact_ids=["missing_parent"])
    resolver.register(child)
    result = resolver.validate_lineage("c1")
    assert result["valid"] is False
    assert "missing_parent" in result["missing_parents"]


def test_lineage_validate_not_found():
    resolver = LineageResolver()
    result = resolver.validate_lineage("nonexistent")
    assert result["valid"] is False


def test_lineage_summary():
    resolver = LineageResolver()
    resolver.register(ArtifactManifest(artifact_type="score", artifact_id="s1"))
    resolver.register(ArtifactManifest(artifact_type="score", artifact_id="s2"))
    resolver.register(ArtifactManifest(artifact_type="state", artifact_id="st1"))
    s = resolver.summary()
    assert s["total_manifests"] == 3
    assert s["by_type"]["score"] == 2
    assert s["by_type"]["state"] == 1


def test_register_dict():
    resolver = LineageResolver()
    resolver.register_dict({
        "artifact_id": "d1",
        "artifact_type": "test",
        "parent_artifact_ids": [],
        "created_at": "2026-03-07T00:00:00Z",
    })
    assert resolver.get("d1") is not None
