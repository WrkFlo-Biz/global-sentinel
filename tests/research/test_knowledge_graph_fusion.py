#!/usr/bin/env python3
"""Tests for KnowledgeGraphFusionPrototype."""
from __future__ import annotations

import sys
import os
import pytest
from datetime import datetime, timezone, timedelta

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from research.knowledge_graph_fusion_prototype import (
    KnowledgeGraphFusionPrototype,
    GraphNode,
    GraphEdge,
)


def _make_events(n: int = 3, base_time: datetime | None = None) -> list[dict]:
    """Create n sample geopolitical event dicts."""
    base = base_time or datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    categories = ["sanctions", "conflict", "election", "policy_change"]
    return [
        {
            "node_id": f"evt-{i:03d}",
            "source": f"event_source_{i}",
            "timestamp": (base + timedelta(hours=i)).isoformat(),
            "severity": 0.5 + i * 0.1,
            "region": "europe" if i % 2 == 0 else "asia",
            "category": categories[i % len(categories)],
        }
        for i in range(n)
    ]


def _make_signals(n: int = 3, base_time: datetime | None = None) -> list[dict]:
    """Create n sample market signal dicts."""
    base = base_time or datetime(2026, 3, 1, 14, 0, 0, tzinfo=timezone.utc)
    signal_types = ["volatility", "price_move", "correlation_break"]
    return [
        {
            "node_id": f"sig-{i:03d}",
            "symbol": f"ASSET_{i}",
            "timestamp": (base + timedelta(hours=i)).isoformat(),
            "signal_type": signal_types[i % len(signal_types)],
            "magnitude": 0.3 + i * 0.2,
            "direction": "up" if i % 2 == 0 else "down",
        }
        for i in range(n)
    ]


class TestAddEventNodes:
    def test_add_event_nodes(self):
        kg = KnowledgeGraphFusionPrototype()
        events = _make_events(5)
        count = kg.add_event_nodes(events)
        assert count == 5
        assert len(kg._nodes) == 5
        for nid, node in kg._nodes.items():
            assert node.node_type == "event"
            assert node.timestamp.tzinfo is not None

    def test_skips_bad_timestamp(self):
        kg = KnowledgeGraphFusionPrototype()
        events = [{"node_id": "bad", "timestamp": "not-a-date"}]
        count = kg.add_event_nodes(events)
        assert count == 0


class TestAddSignalNodes:
    def test_add_signal_nodes(self):
        kg = KnowledgeGraphFusionPrototype()
        signals = _make_signals(4)
        count = kg.add_signal_nodes(signals)
        assert count == 4
        assert len(kg._nodes) == 4
        for nid, node in kg._nodes.items():
            assert node.node_type == "signal"
            assert "symbol" in node.attributes

    def test_attributes_captured(self):
        kg = KnowledgeGraphFusionPrototype()
        signals = _make_signals(1)
        kg.add_signal_nodes(signals)
        node = list(kg._nodes.values())[0]
        assert node.attributes["signal_type"] == "volatility"
        assert node.attributes["direction"] == "up"


class TestBuildEdgesTemporalWindow:
    def test_build_edges_temporal_window(self):
        kg = KnowledgeGraphFusionPrototype({"temporal_window_hours": 72})
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        kg.add_event_nodes(_make_events(3, base))
        kg.add_signal_nodes(_make_signals(3, base + timedelta(hours=2)))
        edge_count = kg.build_edges()
        assert edge_count > 0
        for edge in kg._edges:
            assert edge.weight >= kg.min_edge_weight
            assert edge.edge_type == "event_signal_link"

    def test_closer_events_have_higher_weight(self):
        kg = KnowledgeGraphFusionPrototype({"temporal_window_hours": 72})
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        kg.add_event_nodes([
            {"node_id": "evt-close", "source": "close", "timestamp": base.isoformat(),
             "severity": 0.8, "region": "europe", "category": "sanctions"},
            {"node_id": "evt-far", "source": "far",
             "timestamp": (base - timedelta(hours=48)).isoformat(),
             "severity": 0.8, "region": "europe", "category": "sanctions"},
        ])
        kg.add_signal_nodes([
            {"node_id": "sig-target", "symbol": "OIL",
             "timestamp": (base + timedelta(hours=1)).isoformat(),
             "signal_type": "volatility", "magnitude": 0.9, "direction": "up"},
        ])
        kg.build_edges()

        weights = {e.source_id: e.weight for e in kg._edges}
        assert weights["evt-close"] > weights["evt-far"]


class TestNoEdgesDistantEvents:
    def test_no_edges_distant_events(self):
        kg = KnowledgeGraphFusionPrototype({"temporal_window_hours": 24})
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        kg.add_event_nodes([
            {"node_id": "evt-old", "source": "old_event",
             "timestamp": (base - timedelta(hours=100)).isoformat(),
             "severity": 0.9, "region": "europe", "category": "conflict"},
        ])
        kg.add_signal_nodes([
            {"node_id": "sig-new", "symbol": "SPY",
             "timestamp": base.isoformat(),
             "signal_type": "price_move", "magnitude": 0.5, "direction": "down"},
        ])
        edge_count = kg.build_edges()
        assert edge_count == 0


class TestQueryConnectedDepth:
    def test_query_connected_depth(self):
        kg = KnowledgeGraphFusionPrototype({"temporal_window_hours": 100})
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Create chain: evt-0 -- sig-0 -- evt-1 -- sig-1
        kg.add_event_nodes([
            {"node_id": "evt-0", "source": "e0", "timestamp": base.isoformat(),
             "severity": 0.5, "region": "europe", "category": "sanctions"},
            {"node_id": "evt-1", "source": "e1",
             "timestamp": (base + timedelta(hours=5)).isoformat(),
             "severity": 0.5, "region": "asia", "category": "conflict"},
        ])
        kg.add_signal_nodes([
            {"node_id": "sig-0", "symbol": "A",
             "timestamp": (base + timedelta(hours=2)).isoformat(),
             "signal_type": "volatility", "magnitude": 0.5, "direction": "up"},
            {"node_id": "sig-1", "symbol": "B",
             "timestamp": (base + timedelta(hours=7)).isoformat(),
             "signal_type": "price_move", "magnitude": 0.3, "direction": "down"},
        ])
        kg.build_edges()

        # Depth 1 from evt-0: should find signals connected to evt-0
        result_d1 = kg.query_connected("evt-0", max_depth=1)
        ids_d1 = {r["node"]["node_id"] for r in result_d1}
        assert "evt-0" in ids_d1

        # Depth 2: should reach further
        result_d2 = kg.query_connected("evt-0", max_depth=2)
        ids_d2 = {r["node"]["node_id"] for r in result_d2}
        assert len(ids_d2) >= len(ids_d1)

    def test_query_nonexistent_node(self):
        kg = KnowledgeGraphFusionPrototype()
        result = kg.query_connected("does-not-exist")
        assert result == []


class TestSignalStrengthScoring:
    def test_signal_strength_scoring(self):
        kg = KnowledgeGraphFusionPrototype({"temporal_window_hours": 72})
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        kg.add_event_nodes(_make_events(5, base))
        kg.add_signal_nodes(_make_signals(2, base + timedelta(hours=1)))
        kg.build_edges()

        score = kg.score_signal_strength("sig-000")
        assert "composite_score" in score
        assert score["connected_event_count"] > 0
        assert score["total_edge_weight"] > 0
        assert score["event_diversity"] > 0
        assert score["research_only"] is True
        assert score["not_for_direct_execution"] is True

    def test_score_nonexistent_signal(self):
        kg = KnowledgeGraphFusionPrototype()
        score = kg.score_signal_strength("nope")
        assert score["error"] == "node_not_found"

    def test_score_event_node_rejected(self):
        kg = KnowledgeGraphFusionPrototype()
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        kg.add_event_nodes(_make_events(1, base))
        score = kg.score_signal_strength("evt-000")
        assert score["error"] == "not_a_signal_node"


class TestSummarizeStructure:
    def test_summarize_structure(self):
        kg = KnowledgeGraphFusionPrototype()
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        kg.add_event_nodes(_make_events(4, base))
        kg.add_signal_nodes(_make_signals(3, base + timedelta(hours=1)))
        kg.build_edges()

        summary = kg.summarize()
        assert summary["schema_version"] == "knowledge_graph_fusion.v1"
        assert summary["total_nodes"] == 7
        assert summary["event_nodes"] == 4
        assert summary["signal_nodes"] == 3
        assert summary["total_edges"] > 0
        assert "graph_density" in summary
        assert isinstance(summary["top_connected_signals"], list)
        assert "config" in summary

    def test_empty_graph_summary(self):
        kg = KnowledgeGraphFusionPrototype()
        summary = kg.summarize()
        assert summary["total_nodes"] == 0
        assert summary["total_edges"] == 0
        assert summary["graph_density"] == 0.0


class TestResearchOnlyFlags:
    def test_research_only_flags(self):
        kg = KnowledgeGraphFusionPrototype()
        assert kg.research_only is True
        assert kg.not_for_direct_execution is True

        summary = kg.summarize()
        assert summary["research_only"] is True
        assert summary["not_for_direct_execution"] is True

    def test_score_output_has_flags(self):
        kg = KnowledgeGraphFusionPrototype()
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        kg.add_event_nodes(_make_events(1, base))
        kg.add_signal_nodes(_make_signals(1, base))
        kg.build_edges()
        score = kg.score_signal_strength("sig-000")
        assert score["research_only"] is True
        assert score["not_for_direct_execution"] is True


class TestMaxNodesEnforced:
    def test_max_nodes_enforced(self):
        kg = KnowledgeGraphFusionPrototype({"max_nodes": 5})
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        events = _make_events(10, base)
        count = kg.add_event_nodes(events)
        assert count == 5
        assert len(kg._nodes) == 5

        # No more room for signals either
        signals = _make_signals(5, base)
        sig_count = kg.add_signal_nodes(signals)
        assert sig_count == 0
        assert len(kg._nodes) == 5

    def test_mixed_max_nodes(self):
        kg = KnowledgeGraphFusionPrototype({"max_nodes": 8})
        base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        kg.add_event_nodes(_make_events(5, base))
        sig_count = kg.add_signal_nodes(_make_signals(10, base))
        assert sig_count == 3
        assert len(kg._nodes) == 8
