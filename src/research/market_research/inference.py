#!/usr/bin/env python3
"""
Inference interface for the market research analog engine (Stream 4).

Designed for consumption by dashboard and Telegram layers.
This module is intentionally dependency-light and returns JSON-serializable dicts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .analog_engine import DEFAULT_FEATURE_COLUMNS, AnalogMatch, build_analog_model, load_daily_features


def _utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def get_regime_analogs(
    *,
    as_of: str = None,
    k: int = 20,
    horizons: Sequence[int] = (1, 5, 20),
    feature_columns: Sequence[str] = DEFAULT_FEATURE_COLUMNS,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Returns the most similar historical regimes to `as_of` based on feature vectors.
    """
    features = load_daily_features(repo_root=repo_root)
    if not features:
        return {
            "as_of": as_of or _utc_today_iso(),
            "status": "error",
            "error": "no_daily_features_found",
            "matches": [],
            "stats": {},
        }

    model = build_analog_model(features, columns=feature_columns)
    query_date = as_of or model.dates[-1]

    try:
        matches: List[AnalogMatch] = model.similar_dates(query_date, k=int(k), min_separation_days=3)
    except KeyError:
        # If the requested as_of isn't present, fall back to latest.
        query_date = model.dates[-1]
        matches = model.similar_dates(query_date, k=int(k), min_separation_days=3)

    stats = model.conditional_forward_stats(matches, horizons=horizons)
    return {
        "as_of": query_date,
        "status": "ok",
        "feature_columns": list(model.columns),
        "matches": [{"date": m.date, "similarity": m.similarity} for m in matches],
        "stats": stats,
    }


def format_analogs_brief(payload: Dict[str, Any], *, max_matches: int = 8) -> str:
    """
    Human-readable summary for Telegram-style channels.
    """
    if payload.get("status") != "ok":
        return f"Analogs: error ({payload.get('error')})"

    as_of = payload.get("as_of") or ""
    matches = payload.get("matches") or []
    stats = payload.get("stats") or {}
    h = stats.get("horizons") or {}

    parts: List[str] = [f"Analogs (as_of={as_of})"]

    # Show a couple horizon stats
    for horizon in ("1", "5", "20"):
        if horizon not in h:
            continue
        st = h[horizon]
        if st.get("mean") is None:
            continue
        parts.append(
            f"fwd_{horizon}d mean={st['mean']:+.2%} min={st['min']:+.2%} max={st['max']:+.2%} n={st['count']}"
        )

    # Top matches
    top = matches[: max(0, int(max_matches))]
    if top:
        parts.append("top_matches=" + ", ".join(f"{m['date']}({m['similarity']:.2f})" for m in top))
    return " | ".join(parts)

