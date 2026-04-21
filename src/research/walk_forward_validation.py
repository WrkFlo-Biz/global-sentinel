"""Purged walk-forward validation for research datasets.

The goal is to make training-validation splits more realistic by:
- preserving chronological order
- purging training rows near test windows
- widening embargo windows around major events

The module is intentionally dependency-light so it can be used from
research checks, pipeline gates, and tests without pulling in ML libs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_VERSION = "walk_forward_validation.v1"
DEFAULT_SCORE_FIELDS = (
    "net_expected_value_bps",
    "expected_edge_bps",
    "research_score_used",
    "confidence_adjusted_score",
    "confidence_score",
    "preopt_feature_score",
    "base_score",
    "event_score",
    "quality_score",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_timestamp(row: Dict[str, Any]) -> Optional[datetime]:
    for key in (
        "timestamp_utc",
        "event_timestamp_utc",
        "decision_timestamp_utc",
        "exit_time",
        "entry_time",
        "timestamp",
    ):
        dt = _parse_timestamp(row.get(key))
        if dt is not None:
            return dt
    return None


def _row_event_id(row: Dict[str, Any]) -> str:
    for key in (
        "event_id",
        "canonical_event_id",
        "source_event_id",
        "macro_event_id",
        "event_key",
    ):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _row_event_novelty(row: Dict[str, Any]) -> Optional[float]:
    for key in ("event_novelty_score", "novelty_score", "canonical_novelty_score"):
        value = row.get(key)
        if value is not None:
            return _safe_float(value, 0.0)
    return None


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs)
    den_y = sum((y - mean_y) ** 2 for y in ys)
    den = (den_x * den_y) ** 0.5
    if den == 0:
        return None
    return num / den


def _select_score_field(rows: Sequence[Dict[str, Any]], candidates: Sequence[str] = DEFAULT_SCORE_FIELDS) -> Optional[str]:
    best_field: Optional[str] = None
    best_count = 0
    for field in candidates:
        count = 0
        for row in rows:
            if field in row and isinstance(row.get(field), (int, float)):
                count += 1
        if count > best_count:
            best_field = field
            best_count = count
    return best_field if best_count > 0 else None


def _top_bottom_quartile_means(pairs: Sequence[Tuple[float, float]]) -> Tuple[Optional[float], Optional[float]]:
    if len(pairs) < 4:
        return None, None
    ordered = sorted(pairs, key=lambda item: item[0])
    quartile = max(1, len(ordered) // 4)
    bottom = ordered[:quartile]
    top = ordered[-quartile:]
    bottom_mean = sum(v for _, v in bottom) / len(bottom) if bottom else None
    top_mean = sum(v for _, v in top) / len(top) if top else None
    return bottom_mean, top_mean


@dataclass
class WalkForwardFoldResult:
    fold_index: int
    raw_train_count: int
    train_count: int
    test_count: int
    purged_count: int
    train_start_utc: Optional[str]
    train_end_utc: Optional[str]
    test_start_utc: Optional[str]
    test_end_utc: Optional[str]
    embargo_minutes: int
    major_event_embargo_minutes: int
    major_event_count: int
    score_field: Optional[str]
    train_score_median: Optional[float]
    directional_accuracy: Optional[float]
    pearson_correlation: Optional[float]
    average_test_return_bps: Optional[float]
    top_quartile_return_bps: Optional[float]
    bottom_quartile_return_bps: Optional[float]
    passed: bool
    reason: str
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WalkForwardValidationResult:
    schema_version: str = SCHEMA_VERSION
    passed: bool = False
    folds: List[WalkForwardFoldResult] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "passed": self.passed,
            "folds": [fold.to_dict() for fold in self.folds],
            "summary": dict(self.summary),
            "warnings": list(self.warnings),
        }


def build_purged_walk_forward_splits(
    rows: Sequence[Dict[str, Any]],
    *,
    n_folds: int = 5,
    test_fraction: float = 0.2,
    embargo_minutes: int = 60,
    major_event_novelty_threshold: float = 0.8,
    major_event_embargo_minutes: int = 240,
) -> Dict[str, Any]:
    """Create chronological walk-forward folds with purge windows."""
    valid_rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _row_timestamp(row)
        if ts is None:
            warnings.append("row_missing_timestamp")
            continue
        item = dict(row)
        item["_wf_timestamp"] = ts
        item["_wf_event_id"] = _row_event_id(row)
        item["_wf_event_novelty"] = _row_event_novelty(row)
        valid_rows.append(item)

    valid_rows.sort(key=lambda row: row["_wf_timestamp"])
    total_rows = len(valid_rows)
    if total_rows == 0:
        return {
            "schema_version": SCHEMA_VERSION,
            "folds": [],
            "warnings": warnings or ["no_valid_rows"],
        }

    fold_size = max(1, int(round(total_rows * max(0.01, min(test_fraction, 0.9)))))
    initial_train = max(fold_size, min(total_rows - 1, max(5, fold_size)))
    if initial_train >= total_rows:
        initial_train = max(1, total_rows - fold_size)

    possible_folds = max(0, (total_rows - initial_train) // fold_size)
    fold_count = min(max(1, n_folds), possible_folds) if possible_folds > 0 else 0

    folds: List[Dict[str, Any]] = []
    for fold_index in range(fold_count):
        test_start_idx = initial_train + fold_index * fold_size
        test_end_idx = min(total_rows, test_start_idx + fold_size)
        if test_start_idx >= total_rows or test_end_idx <= test_start_idx:
            break

        test_rows = valid_rows[test_start_idx:test_end_idx]
        train_rows = valid_rows[:test_start_idx]
        if not test_rows or not train_rows:
            continue

        test_start_ts = test_rows[0]["_wf_timestamp"]
        test_end_ts = test_rows[-1]["_wf_timestamp"]
        test_event_ids = {
            row["_wf_event_id"]
            for row in test_rows
            if row["_wf_event_id"]
        }
        major_event_count = sum(
            1
            for row in test_rows
            if _safe_float(row.get("event_novelty_score", row.get("_wf_event_novelty")), 0.0)
            >= major_event_novelty_threshold
        )
        fold_embargo_minutes = embargo_minutes
        if major_event_count > 0:
            fold_embargo_minutes = max(fold_embargo_minutes, major_event_embargo_minutes)

        embargo_delta = timedelta(minutes=fold_embargo_minutes)
        purged_train_rows: List[Dict[str, Any]] = []
        purged_count = 0
        for row in train_rows:
            ts = row["_wf_timestamp"]
            within_embargo = (test_start_ts - embargo_delta) <= ts <= (test_end_ts + embargo_delta)
            same_event = bool(row["_wf_event_id"] and row["_wf_event_id"] in test_event_ids)
            if within_embargo or same_event:
                purged_count += 1
                continue
            purged_train_rows.append(row)

        folds.append(
            {
                "fold_index": fold_index,
                "raw_train_rows": train_rows,
                "train_rows": purged_train_rows,
                "test_rows": test_rows,
                "purged_count": purged_count,
                "major_event_count": major_event_count,
                "embargo_minutes": fold_embargo_minutes,
                "test_start_utc": test_start_ts.isoformat(),
                "test_end_utc": test_end_ts.isoformat(),
                "train_start_utc": purged_train_rows[0]["_wf_timestamp"].isoformat() if purged_train_rows else None,
                "train_end_utc": purged_train_rows[-1]["_wf_timestamp"].isoformat() if purged_train_rows else None,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "folds": folds,
        "warnings": warnings,
    }


def validate_walk_forward_dataset(
    dataset: Dict[str, Any],
    *,
    score_fields: Optional[Sequence[str]] = None,
    n_folds: int = 5,
    test_fraction: float = 0.2,
    embargo_minutes: int = 60,
    major_event_novelty_threshold: float = 0.8,
    major_event_embargo_minutes: int = 240,
    min_rows: int = 30,
    min_folds: int = 3,
    min_directional_accuracy: float = 0.52,
    min_average_correlation: float = 0.05,
) -> Dict[str, Any]:
    """Evaluate a dataset with purged walk-forward validation."""
    rows = dataset.get("rows") if isinstance(dataset, dict) else None
    if not isinstance(rows, list):
        rows = []

    warnings: List[str] = []
    if len(rows) < min_rows:
        return {
            "schema_version": SCHEMA_VERSION,
            "passed": False,
            "summary": {
                "reason": "insufficient_rows",
                "row_count": len(rows),
                "min_rows": min_rows,
            },
            "folds": [],
            "warnings": ["insufficient_rows"],
        }

    selected_score_field = None
    if score_fields:
        selected_score_field = _select_score_field(rows, score_fields)
    else:
        dataset_hint = dataset.get("walk_forward_validation") if isinstance(dataset, dict) else None
        if isinstance(dataset_hint, dict):
            hint_fields = dataset_hint.get("score_fields")
            if isinstance(hint_fields, list) and hint_fields:
                selected_score_field = _select_score_field(rows, hint_fields)
        if not selected_score_field:
            selected_score_field = _select_score_field(rows)

    if not selected_score_field:
        return {
            "schema_version": SCHEMA_VERSION,
            "passed": False,
            "summary": {
                "reason": "no_numeric_score_field",
                "row_count": len(rows),
            },
            "folds": [],
            "warnings": ["no_numeric_score_field"],
        }

    split_payload = build_purged_walk_forward_splits(
        rows,
        n_folds=n_folds,
        test_fraction=test_fraction,
        embargo_minutes=embargo_minutes,
        major_event_novelty_threshold=major_event_novelty_threshold,
        major_event_embargo_minutes=major_event_embargo_minutes,
    )
    split_folds = split_payload.get("folds") or []
    warnings.extend(split_payload.get("warnings") or [])

    fold_results: List[WalkForwardFoldResult] = []
    directional_accuracies: List[float] = []
    correlations: List[float] = []
    major_event_folds = 0

    for fold in split_folds:
        raw_train_rows = fold.get("raw_train_rows") or []
        train_rows = fold.get("train_rows") or []
        test_rows = fold.get("test_rows") or []
        fold_warnings: List[str] = []
        if not train_rows or not test_rows:
            fold_results.append(
                WalkForwardFoldResult(
                    fold_index=int(fold.get("fold_index", 0)),
                    raw_train_count=len(raw_train_rows),
                    train_count=len(train_rows),
                    test_count=len(test_rows),
                    purged_count=int(fold.get("purged_count", 0)),
                    train_start_utc=fold.get("train_start_utc"),
                    train_end_utc=fold.get("train_end_utc"),
                    test_start_utc=fold.get("test_start_utc"),
                    test_end_utc=fold.get("test_end_utc"),
                    embargo_minutes=int(fold.get("embargo_minutes", embargo_minutes)),
                    major_event_embargo_minutes=major_event_embargo_minutes,
                    major_event_count=int(fold.get("major_event_count", 0)),
                    score_field=selected_score_field,
                    train_score_median=None,
                    directional_accuracy=None,
                    pearson_correlation=None,
                    average_test_return_bps=None,
                    top_quartile_return_bps=None,
                    bottom_quartile_return_bps=None,
                    passed=False,
                    reason="insufficient_fold_rows",
                    warnings=["insufficient_fold_rows"],
                )
            )
            continue

        train_scores = [
            _safe_float(row.get(selected_score_field))
            for row in train_rows
            if row.get(selected_score_field) is not None
        ]
        paired_test = [
            (
                _safe_float(row.get(selected_score_field)),
                _safe_float(row.get("realized_return_bps"), 0.0),
            )
            for row in test_rows
            if row.get(selected_score_field) is not None
        ]
        test_scores = [score for score, _ in paired_test]
        test_returns = [ret for _, ret in paired_test]
        if len(train_scores) < 2 or len(paired_test) < 2:
            fold_warnings.append("insufficient_numeric_scores")
            fold_results.append(
                WalkForwardFoldResult(
                    fold_index=int(fold.get("fold_index", 0)),
                    raw_train_count=len(raw_train_rows),
                    train_count=len(train_rows),
                    test_count=len(test_rows),
                    purged_count=int(fold.get("purged_count", 0)),
                    train_start_utc=fold.get("train_start_utc"),
                    train_end_utc=fold.get("train_end_utc"),
                    test_start_utc=fold.get("test_start_utc"),
                    test_end_utc=fold.get("test_end_utc"),
                    embargo_minutes=int(fold.get("embargo_minutes", embargo_minutes)),
                    major_event_embargo_minutes=major_event_embargo_minutes,
                    major_event_count=int(fold.get("major_event_count", 0)),
                    score_field=selected_score_field,
                    train_score_median=None,
                    directional_accuracy=None,
                    pearson_correlation=None,
                    average_test_return_bps=sum(test_returns) / len(test_returns) if test_returns else None,
                    top_quartile_return_bps=None,
                    bottom_quartile_return_bps=None,
                    passed=False,
                    reason="insufficient_numeric_scores",
                    warnings=fold_warnings,
                )
            )
            continue

        threshold = median(train_scores)
        predicted_positive = [score >= threshold for score, _ in paired_test]
        actual_positive = [ret > 0 for _, ret in paired_test]
        directional_accuracy = sum(
            1 for pred, actual in zip(predicted_positive, actual_positive) if pred == actual
        ) / len(paired_test)
        correlation = _pearson(test_scores, test_returns)
        bottom_quartile_return, top_quartile_return = _top_bottom_quartile_means(paired_test)
        average_test_return = sum(test_returns) / len(test_returns) if test_returns else None

        if int(fold.get("major_event_count", 0)) > 0:
            major_event_folds += 1

        fold_passed = directional_accuracy >= min_directional_accuracy and (
            correlation is None or correlation >= min_average_correlation
        )
        if correlation is None:
            fold_warnings.append("correlation_unavailable")
            fold_passed = False
        if directional_accuracy < min_directional_accuracy:
            fold_warnings.append("directional_accuracy_below_threshold")
        if correlation is not None and correlation < min_average_correlation:
            fold_warnings.append("correlation_below_threshold")

        if fold_passed:
            directional_accuracies.append(directional_accuracy)
        if correlation is not None:
            correlations.append(correlation)

        fold_results.append(
            WalkForwardFoldResult(
                fold_index=int(fold.get("fold_index", 0)),
                raw_train_count=len(raw_train_rows),
                train_count=len(train_rows),
                test_count=len(test_rows),
                purged_count=int(fold.get("purged_count", 0)),
                train_start_utc=fold.get("train_start_utc"),
                train_end_utc=fold.get("train_end_utc"),
                test_start_utc=fold.get("test_start_utc"),
                test_end_utc=fold.get("test_end_utc"),
                embargo_minutes=int(fold.get("embargo_minutes", embargo_minutes)),
                major_event_embargo_minutes=major_event_embargo_minutes,
                major_event_count=int(fold.get("major_event_count", 0)),
                score_field=selected_score_field,
                train_score_median=float(threshold),
                directional_accuracy=round(directional_accuracy, 4),
                pearson_correlation=None if correlation is None else round(correlation, 4),
                average_test_return_bps=None if average_test_return is None else round(average_test_return, 4),
                top_quartile_return_bps=None if top_quartile_return is None else round(top_quartile_return, 4),
                bottom_quartile_return_bps=None if bottom_quartile_return is None else round(bottom_quartile_return, 4),
                passed=fold_passed,
                reason="ok" if fold_passed else "fold_metric_threshold_not_met",
                warnings=fold_warnings,
            )
        )

    evaluated_folds = [fold for fold in fold_results if fold.test_count > 0]
    average_directional_accuracy = (
        sum(directional_accuracies) / len(directional_accuracies)
        if directional_accuracies
        else 0.0
    )
    average_correlation = (
        sum(correlations) / len(correlations) if correlations else 0.0
    )

    passed = (
        len(evaluated_folds) >= min_folds
        and average_directional_accuracy >= min_directional_accuracy
        and average_correlation >= min_average_correlation
        and all(f.passed for f in evaluated_folds)
    )

    if len(evaluated_folds) < min_folds:
        warnings.append("insufficient_folds")
    if average_directional_accuracy < min_directional_accuracy:
        warnings.append("average_directional_accuracy_below_threshold")
    if average_correlation < min_average_correlation:
        warnings.append("average_correlation_below_threshold")

    return {
        "schema_version": SCHEMA_VERSION,
        "passed": passed,
        "summary": {
            "row_count": len(rows),
            "score_field": selected_score_field,
            "folds_evaluated": len(evaluated_folds),
            "major_event_folds": major_event_folds,
            "average_directional_accuracy": round(average_directional_accuracy, 4),
            "average_pearson_correlation": round(average_correlation, 4),
            "embargo_minutes": embargo_minutes,
            "major_event_embargo_minutes": major_event_embargo_minutes,
            "reason": "ok" if passed else "walk_forward_validation_failed",
        },
        "folds": [fold.to_dict() for fold in fold_results],
        "warnings": warnings,
    }
