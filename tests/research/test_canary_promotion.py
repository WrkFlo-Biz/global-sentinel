#!/usr/bin/env python3
"""Tests for canary encoder comparator and encoder version manager."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.research.canary_encoder_comparator import CanaryEncoderComparator
from src.research.rollback_encoder_version import EncoderVersionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_outputs(scores, wins=None, returns=None):
    """Build a list of output dicts from parallel lists."""
    out = []
    for i, s in enumerate(scores):
        d = {"score": s}
        if wins is not None:
            d["win"] = wins[i]
        if returns is not None:
            d["return"] = returns[i]
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# CanaryEncoderComparator tests
# ---------------------------------------------------------------------------

class TestCanaryEncoderComparator:

    def test_promote_recommendation(self):
        """Candidate clearly better on all metrics -> promote."""
        comp = CanaryEncoderComparator(thresholds={"min_sample_count": 5})
        n = 40
        current = _make_outputs(
            scores=[0.50] * n,
            wins=[True, False] * (n // 2),
            returns=[0.01, -0.005] * (n // 2),
        )
        candidate = _make_outputs(
            scores=[0.52] * n,
            wins=[True, True, True, False] * (n // 4),
            returns=[0.015, -0.003] * (n // 2),
        )
        result = comp.compare(current, candidate)

        assert result["schema_version"] == "canary_comparison.v1"
        assert result["sample_count"] == n
        assert result["promotion_recommendation"] == "promote"
        assert comp.is_safe_to_promote(result) is True

    def test_reject_recommendation_regression(self):
        """Candidate significantly worse -> reject."""
        comp = CanaryEncoderComparator(thresholds={"min_sample_count": 5})
        n = 40
        current = _make_outputs(
            scores=[0.60] * n,
            wins=[True] * n,
            returns=[0.02] * n,
        )
        candidate = _make_outputs(
            scores=[0.50] * n,
            wins=[False] * n,
            returns=[-0.01] * n,
        )
        result = comp.compare(current, candidate)

        assert result["promotion_recommendation"] == "reject"
        assert comp.is_safe_to_promote(result) is False

    def test_reject_low_correlation(self):
        """Candidate with very different behavior -> reject on correlation."""
        comp = CanaryEncoderComparator(thresholds={"min_sample_count": 5})
        import random
        random.seed(42)
        n = 50
        current_scores = [random.uniform(0.4, 0.6) for _ in range(n)]
        # Anti-correlated candidate
        candidate_scores = [1.0 - s + random.uniform(-0.05, 0.05) for s in current_scores]

        current = _make_outputs(current_scores)
        candidate = _make_outputs(candidate_scores)
        result = comp.compare(current, candidate)

        assert result["promotion_recommendation"] == "reject"
        assert "correlation" in result["reason"]

    def test_hold_recommendation_insufficient_samples(self):
        """Too few samples -> hold."""
        comp = CanaryEncoderComparator()  # default min_sample_count=30
        n = 10
        current = _make_outputs([0.50] * n)
        candidate = _make_outputs([0.52] * n)
        result = comp.compare(current, candidate)

        assert result["promotion_recommendation"] == "hold"
        assert "insufficient" in result["reason"]

    def test_hold_recommendation_mixed_results(self):
        """Mixed improvements -> hold (score up slightly, win rate down slightly)."""
        comp = CanaryEncoderComparator(thresholds={"min_sample_count": 5})
        n = 40
        # Score slightly better but below promote threshold, win rate slightly negative
        current = _make_outputs(
            scores=[0.50] * n,
            wins=([True] * 21 + [False] * 19),  # 52.5% win rate
            returns=[0.01] * n,
        )
        candidate = _make_outputs(
            scores=[0.503] * n,  # +0.003, below min_improvement_delta of 0.005
            wins=([True] * 20 + [False] * 20),  # 50% win rate, delta = -0.025 > -0.02 threshold... need smaller
            returns=[0.01] * n,
        )
        # win_rate_delta = -0.025 which is < -0.02 => reject. Make it smaller.
        # Use wins that give delta = -0.01
        current_wins = [True] * 21 + [False] * 19  # 52.5%
        candidate_wins = [True] * 21 + [False] * 19  # same 52.5%, delta=0
        # score delta = 0.003 < 0.005 min_improvement => hold (not promote)
        # win_rate_delta = 0 >= 0 but score not enough => hold
        candidate = _make_outputs(
            scores=[0.503] * n,
            wins=candidate_wins,
            returns=[0.01] * n,
        )
        result = comp.compare(current, candidate)

        assert result["promotion_recommendation"] == "hold"

    def test_mismatched_lengths_raises(self):
        """Different length lists -> ValueError."""
        comp = CanaryEncoderComparator()
        with pytest.raises(ValueError, match="Mismatched"):
            comp.compare(
                _make_outputs([0.5, 0.6]),
                _make_outputs([0.5]),
            )

    def test_empty_lists_raises(self):
        """Empty lists -> ValueError."""
        comp = CanaryEncoderComparator()
        with pytest.raises(ValueError, match="empty"):
            comp.compare([], [])

    def test_report_schema_fields(self):
        """Report contains all required fields."""
        comp = CanaryEncoderComparator(thresholds={"min_sample_count": 2})
        current = _make_outputs([0.5, 0.6, 0.55])
        candidate = _make_outputs([0.52, 0.61, 0.56])
        result = comp.compare(current, candidate)

        assert "schema_version" in result
        assert "timestamp_utc" in result
        assert "sample_count" in result
        assert "metrics" in result
        assert "promotion_recommendation" in result
        assert "reason" in result

        m = result["metrics"]
        for key in ("mean_score_delta", "max_score_delta", "correlation",
                     "win_rate_delta", "sharpe_delta"):
            assert key in m


# ---------------------------------------------------------------------------
# EncoderVersionManager tests
# ---------------------------------------------------------------------------

class TestEncoderVersionManager:

    def test_save_and_load_roundtrip(self, tmp_path):
        """Save a version, then rollback to it and get same state."""
        mgr = EncoderVersionManager(storage_dir=tmp_path)
        state = {"weights": [1.0, 2.0, 3.0], "bias": 0.5}
        mgr.save_version(state, "v1.0", metadata={"author": "test"})

        loaded = mgr.rollback_to("v1.0")
        assert loaded == state

    def test_list_versions(self, tmp_path):
        """List returns all saved versions."""
        mgr = EncoderVersionManager(storage_dir=tmp_path)
        mgr.save_version({"w": 1}, "v1.0")
        mgr.save_version({"w": 2}, "v2.0")
        mgr.save_version({"w": 3}, "v3.0")

        versions = mgr.list_versions()
        tags = [v["version_tag"] for v in versions]
        assert "v1.0" in tags
        assert "v2.0" in tags
        assert "v3.0" in tags
        assert len(versions) == 3

    def test_current_version_tracking(self, tmp_path):
        """Current version updates on save and rollback."""
        mgr = EncoderVersionManager(storage_dir=tmp_path)
        assert mgr.current_version() is None

        mgr.save_version({"w": 1}, "v1.0")
        assert mgr.current_version() == "v1.0"

        mgr.save_version({"w": 2}, "v2.0")
        assert mgr.current_version() == "v2.0"

        mgr.rollback_to("v1.0")
        assert mgr.current_version() == "v1.0"

    def test_rollback_nonexistent_raises(self, tmp_path):
        """Rollback to missing version raises FileNotFoundError."""
        mgr = EncoderVersionManager(storage_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            mgr.rollback_to("v999")

    def test_version_metadata_preserved(self, tmp_path):
        """Metadata is stored and returned in list_versions."""
        mgr = EncoderVersionManager(storage_dir=tmp_path)
        meta = {"source": "canary", "experiment_id": "exp-42"}
        mgr.save_version({"w": 1}, "v1.0", metadata=meta)

        versions = mgr.list_versions()
        assert len(versions) == 1
        assert versions[0]["metadata"] == meta
