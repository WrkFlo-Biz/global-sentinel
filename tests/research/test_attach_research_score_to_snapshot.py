"""Tests for attaching research score to snapshot."""

from src.research.attach_research_score_to_snapshot import attach_research_score


def test_attach_research_score_to_snapshot():
    snapshot = {
        "snapshot_id": "snap-001",
        "runtime_flags": {"shadow_mode_only": True},
        "candidate_universe": [{"symbol": "XOM"}],
    }
    research_score = {
        "request_id": "req-001",
        "package_id": "pkg-001",
        "research_score": 0.73,
        "recommended_influence": "research_positive",
        "guardrails": {
            "not_for_direct_execution": True,
            "bounded_secondary_signal_only": True,
        },
    }

    out = attach_research_score(snapshot, research_score)

    assert out["research_overlays"]["quantum_research_score"]["research_score"] == 0.73
    assert out["runtime_flags"]["quantum_research_attached"] is True
    assert out["runtime_flags"]["quantum_direct_execution_forbidden"] is True
    assert out["runtime_flags"]["shadow_mode_only"] is True  # original preserved
    assert out["candidate_universe"] == [{"symbol": "XOM"}]  # original preserved
