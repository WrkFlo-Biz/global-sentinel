"""Tests for purged walk-forward validation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.research.walk_forward_validation import (
    build_purged_walk_forward_splits,
    validate_walk_forward_dataset,
)


def _make_rows(inverted: bool = False, major_event_index: int = 15) -> list[dict]:
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    rows: list[dict] = []
    for idx in range(60):
        score = idx / 59.0
        realized = (score * 200.0 - 20.0)
        if inverted:
            realized = -realized
        row = {
            "symbol": f"SYM{idx % 5}",
            "timestamp_utc": (base + timedelta(hours=idx)).isoformat(),
            "base_score": round(score, 4),
            "realized_return_bps": round(realized, 2),
        }
        if idx == major_event_index:
            row["event_novelty_score"] = 0.95
            row["event_id"] = "event-001"
        elif idx in {major_event_index - 1, major_event_index + 1}:
            row["event_id"] = "event-001"
        rows.append(row)
    return rows


def test_build_purged_walk_forward_splits_applies_major_event_embargo():
    splits = build_purged_walk_forward_splits(
        _make_rows(),
        n_folds=5,
        test_fraction=0.2,
        embargo_minutes=60,
        major_event_embargo_minutes=240,
    )

    assert splits["folds"]
    first_fold = splits["folds"][0]
    assert first_fold["major_event_count"] >= 1
    assert first_fold["embargo_minutes"] == 240
    assert first_fold["purged_count"] > 0
    assert len(first_fold["train_rows"]) < len(first_fold["raw_train_rows"])


def test_validate_walk_forward_dataset_passes_on_correlated_rows():
    result = validate_walk_forward_dataset(
        {"rows": _make_rows()},
        min_rows=30,
        min_folds=3,
    )

    assert result["passed"] is True
    assert result["summary"]["score_field"] == "base_score"
    assert result["summary"]["folds_evaluated"] >= 3
    assert result["summary"]["average_directional_accuracy"] >= 0.52


def test_validate_walk_forward_dataset_rejects_inverted_rows():
    result = validate_walk_forward_dataset(
        {"rows": _make_rows(inverted=True)},
        min_rows=30,
        min_folds=3,
    )

    assert result["passed"] is False
    assert result["summary"]["average_directional_accuracy"] < 0.52
