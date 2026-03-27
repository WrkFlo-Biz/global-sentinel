"""Knowledge graph fusion prototype for geopolitical-market signal research.

Fuses geopolitical event graphs with market signal graphs for research analysis.
Builds event nodes from geopolitical packets, signal nodes from market data,
and creates weighted edges based on temporal proximity and causal hypotheses.

RESEARCH ONLY — not for direct execution or production use.
"""
from __future__ import annotations

import logging
import math
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Category-signal affinity matrix: (event_category, signal_type) -> affinity score
_CATEGORY_SIGNAL_AFFINITY: Dict[tuple, float] = {
    ("sanctions", "volatility"): 0.9,
    ("sanctions", "price_move"): 0.8,
    ("sanctions", "correlation_break"): 0.7,
    ("conflict", "volatility"): 0.95,
    ("conflict", "price_move"): 0.85,
    ("conflict", "correlation_break"): 0.6,
    ("election", "volatility"): 0.7,
    ("election", "price_move"): 0.6,
    ("election", "correlation_break"): 0.5,
    ("policy_change", "price_move"): 0.8,
    ("policy_change", "volatility"): 0.65,
    ("policy_change", "correlation_break"): 0.75,
}

_DEFAULT_AFFINITY = 0.3


@dataclass
class GraphNode:
    node_id: str
    node_type: str  # "event" or "signal"
    label: str
    timestamp: datetime
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    edge_type: str
    weight: float
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class KnowledgeGraphFusionPrototype:
    """Research-only knowledge graph fusion engine.

    Connects geopolitical event nodes to market signal nodes via weighted edges
    based on temporal proximity and category-signal affinity.

    Attributes:
        research_only: Always True. This module is not for production.
        not_for_direct_execution: Always True.
    """

    research_only: bool = True
    not_for_direct_execution: bool = True

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config or {}
        self.temporal_window_hours: int = cfg.get("temporal_window_hours", 72)
        self.min_edge_weight: float = cfg.get("min_edge_weight", 0.1)
        self.max_nodes: int = cfg.get("max_nodes", 500)

        self._nodes: Dict[str, GraphNode] = {}
        self._edges: List[GraphEdge] = []
        # Adjacency list: node_id -> list of (neighbor_id, edge_index)
        self._adjacency: Dict[str, List[tuple]] = {}

    # ------------------------------------------------------------------
    # Node ingestion
    # ------------------------------------------------------------------

    def add_event_nodes(self, events: List[Dict[str, Any]]) -> int:
        """Add event nodes from geopolitical packets.

        Each event dict should contain: source, timestamp, severity, region, category.
        Returns count of nodes actually added.
        """
        added = 0
        for evt in events:
            if len(self._nodes) >= self.max_nodes:
                logger.warning("max_nodes (%d) reached, skipping remaining events", self.max_nodes)
                break

            node_id = evt.get("node_id", f"evt-{uuid.uuid4().hex[:12]}")
            ts = self._parse_timestamp(evt.get("timestamp"))
            if ts is None:
                logger.warning("Skipping event with unparseable timestamp: %s", evt)
                continue

            node = GraphNode(
                node_id=node_id,
                node_type="event",
                label=evt.get("source", "unknown_event"),
                timestamp=ts,
                attributes={
                    "severity": evt.get("severity", 0.0),
                    "region": evt.get("region", "unknown"),
                    "category": evt.get("category", "unknown"),
                    "source": evt.get("source", "unknown"),
                },
            )
            self._nodes[node_id] = node
            self._adjacency.setdefault(node_id, [])
            added += 1

        logger.info("Added %d event nodes (total nodes: %d)", added, len(self._nodes))
        return added

    def add_signal_nodes(self, signals: List[Dict[str, Any]]) -> int:
        """Add signal nodes from market data.

        Each signal dict should contain: symbol, timestamp, signal_type, magnitude, direction.
        Returns count of nodes actually added.
        """
        added = 0
        for sig in signals:
            if len(self._nodes) >= self.max_nodes:
                logger.warning("max_nodes (%d) reached, skipping remaining signals", self.max_nodes)
                break

            node_id = sig.get("node_id", f"sig-{uuid.uuid4().hex[:12]}")
            ts = self._parse_timestamp(sig.get("timestamp"))
            if ts is None:
                logger.warning("Skipping signal with unparseable timestamp: %s", sig)
                continue

            node = GraphNode(
                node_id=node_id,
                node_type="signal",
                label=sig.get("symbol", "unknown_signal"),
                timestamp=ts,
                attributes={
                    "symbol": sig.get("symbol", "unknown"),
                    "signal_type": sig.get("signal_type", "unknown"),
                    "magnitude": sig.get("magnitude", 0.0),
                    "direction": sig.get("direction", "neutral"),
                },
            )
            self._nodes[node_id] = node
            self._adjacency.setdefault(node_id, [])
            added += 1

        logger.info("Added %d signal nodes (total nodes: %d)", added, len(self._nodes))
        return added

    # ------------------------------------------------------------------
    # Edge construction
    # ------------------------------------------------------------------

    def build_edges(self, temporal_window_hours: Optional[int] = None) -> int:
        """Build edges between event nodes and signal nodes.

        Connects events to signals within the temporal window. Edge weight is
        computed as the product of temporal decay and category-signal affinity.

        Returns count of edges created.
        """
        window = temporal_window_hours if temporal_window_hours is not None else self.temporal_window_hours
        window_td = timedelta(hours=window)
        # Half-life for exponential decay: half the window
        half_life_hours = max(window / 2.0, 1.0)

        event_nodes = [n for n in self._nodes.values() if n.node_type == "event"]
        signal_nodes = [n for n in self._nodes.values() if n.node_type == "signal"]

        created = 0
        for evt in event_nodes:
            for sig in signal_nodes:
                delta = abs((sig.timestamp - evt.timestamp).total_seconds()) / 3600.0
                if delta > window:
                    continue

                # Temporal decay: exponential with configurable half-life
                temporal_weight = math.exp(-math.log(2) * delta / half_life_hours)

                # Category-signal affinity
                category = evt.attributes.get("category", "unknown")
                signal_type = sig.attributes.get("signal_type", "unknown")
                affinity = _CATEGORY_SIGNAL_AFFINITY.get(
                    (category, signal_type), _DEFAULT_AFFINITY
                )

                weight = temporal_weight * affinity

                if weight < self.min_edge_weight:
                    continue

                edge = GraphEdge(
                    source_id=evt.node_id,
                    target_id=sig.node_id,
                    edge_type="event_signal_link",
                    weight=round(weight, 6),
                    attributes={
                        "temporal_delta_hours": round(delta, 2),
                        "temporal_weight": round(temporal_weight, 6),
                        "affinity": affinity,
                    },
                )
                edge_idx = len(self._edges)
                self._edges.append(edge)
                self._adjacency[evt.node_id].append((sig.node_id, edge_idx))
                self._adjacency[sig.node_id].append((evt.node_id, edge_idx))
                created += 1

        logger.info("Built %d edges between %d events and %d signals",
                     created, len(event_nodes), len(signal_nodes))
        return created

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query_connected(self, node_id: str, max_depth: int = 2) -> List[Dict[str, Any]]:
        """BFS traversal returning connected subgraph up to max_depth.

        Returns list of dicts with node info and traversal depth.
        """
        if node_id not in self._nodes:
            return []

        visited: Dict[str, int] = {node_id: 0}
        queue: deque = deque([(node_id, 0)])
        results: List[Dict[str, Any]] = []

        while queue:
            current, depth = queue.popleft()
            node = self._nodes[current]
            results.append({
                "node": node.to_dict(),
                "depth": depth,
                "edge_count": len(self._adjacency.get(current, [])),
            })

            if depth < max_depth:
                for neighbor_id, edge_idx in self._adjacency.get(current, []):
                    if neighbor_id not in visited:
                        visited[neighbor_id] = depth + 1
                        queue.append((neighbor_id, depth + 1))

        return results

    def score_signal_strength(self, signal_node_id: str) -> Dict[str, Any]:
        """Score a signal node based on its connected event graph.

        Scoring factors:
        - connected_event_count: number of events linked to this signal
        - total_edge_weight: sum of all edge weights to connected events
        - event_diversity: number of unique event categories connected
        - composite_score: weighted combination of above
        """
        if signal_node_id not in self._nodes:
            return {"error": "node_not_found", "signal_node_id": signal_node_id}

        node = self._nodes[signal_node_id]
        if node.node_type != "signal":
            return {"error": "not_a_signal_node", "signal_node_id": signal_node_id}

        neighbors = self._adjacency.get(signal_node_id, [])
        connected_events: List[GraphNode] = []
        total_weight = 0.0

        for neighbor_id, edge_idx in neighbors:
            neighbor = self._nodes.get(neighbor_id)
            if neighbor and neighbor.node_type == "event":
                connected_events.append(neighbor)
                total_weight += self._edges[edge_idx].weight

        categories = set(
            e.attributes.get("category", "unknown") for e in connected_events
        )

        event_count = len(connected_events)
        diversity = len(categories)

        # Composite: weighted sum normalized to [0, 1] range
        # More events, higher weights, more diverse categories = stronger signal
        count_score = min(event_count / 10.0, 1.0)
        weight_score = min(total_weight / 5.0, 1.0)
        diversity_score = min(diversity / 4.0, 1.0)
        composite = 0.4 * count_score + 0.4 * weight_score + 0.2 * diversity_score

        return {
            "signal_node_id": signal_node_id,
            "connected_event_count": event_count,
            "total_edge_weight": round(total_weight, 6),
            "event_diversity": diversity,
            "categories": sorted(categories),
            "composite_score": round(composite, 6),
            "research_only": True,
            "not_for_direct_execution": True,
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summarize(self) -> Dict[str, Any]:
        """Return a summary of the current graph state."""
        event_count = sum(1 for n in self._nodes.values() if n.node_type == "event")
        signal_count = sum(1 for n in self._nodes.values() if n.node_type == "signal")
        edge_count = len(self._edges)

        # Graph density: edges / max possible edges (bipartite: events * signals)
        max_edges = event_count * signal_count
        density = edge_count / max_edges if max_edges > 0 else 0.0

        # Top connected signals by edge count
        signal_edge_counts: List[tuple] = []
        for nid, node in self._nodes.items():
            if node.node_type == "signal":
                n_edges = sum(
                    1 for _, eidx in self._adjacency.get(nid, [])
                    if self._nodes.get(self._edges[eidx].source_id, GraphNode("", "", "", datetime.now(timezone.utc))).node_type == "event"
                    or self._nodes.get(self._edges[eidx].target_id, GraphNode("", "", "", datetime.now(timezone.utc))).node_type == "event"
                )
                signal_edge_counts.append((nid, node.label, n_edges))

        signal_edge_counts.sort(key=lambda x: x[2], reverse=True)
        top_signals = [
            {"node_id": s[0], "label": s[1], "edge_count": s[2]}
            for s in signal_edge_counts[:5]
        ]

        return {
            "schema_version": "knowledge_graph_fusion.v1",
            "total_nodes": len(self._nodes),
            "event_nodes": event_count,
            "signal_nodes": signal_count,
            "total_edges": edge_count,
            "graph_density": round(density, 6),
            "top_connected_signals": top_signals,
            "config": {
                "temporal_window_hours": self.temporal_window_hours,
                "min_edge_weight": self.min_edge_weight,
                "max_nodes": self.max_nodes,
            },
            "research_only": True,
            "not_for_direct_execution": True,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_timestamp(ts: Any) -> Optional[datetime]:
        """Parse a timestamp from string or datetime."""
        if ts is None:
            return None
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                return None
        return None
