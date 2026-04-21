"""Shared helpers for strategy learning and feedback application.

The repo has multiple suggestion surfaces:
- `StrategyEngine` for YAML-defined war strategies
- `TradeAnalysisEngine` for regime-based trade ideas
- `TradeIdeaPackager` for execution routing

This module keeps the feedback logic consistent across all three
surfaces so the learning loop actually changes future suggestions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


FEEDBACK_STATE_SCHEMA_VERSION = 2


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def feedback_state_path(repo_root: Path | str | None = None) -> Path:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    return root / "logs" / "execution" / "feedback_state.json"


def default_feedback_state() -> Dict[str, Any]:
    """Return a fresh feedback-state payload for the current schema."""
    return {
        "schema_version": FEEDBACK_STATE_SCHEMA_VERSION,
        "signal_adjustments": {},
        "signal_win_counts": {},
        "signal_loss_counts": {},
        "total_trades_analyzed": 0,
        "last_analysis_time": None,
        "cumulative_pnl": 0.0,
        "daily_pnl_history": [],
        "strategy_confidence_adjustments": {},
        "strategy_adjustments": {
            "day_trade": {"stop_loss_tightness": 1.0, "profit_target_mult": 1.0},
            "medium_long": {"stop_loss_tightness": 1.0, "profit_target_mult": 1.0},
        },
        "strategy_group_stats": {},
    }


def _coerce_dict(value: Any, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return dict(default or {})


def normalize_feedback_state(raw: Any) -> Dict[str, Any]:
    """Migrate legacy feedback-state payloads to the current schema."""
    defaults = default_feedback_state()
    if not isinstance(raw, dict):
        return defaults

    normalized = dict(raw)
    normalized["schema_version"] = FEEDBACK_STATE_SCHEMA_VERSION
    normalized["signal_adjustments"] = _coerce_dict(
        raw.get("signal_adjustments"), defaults["signal_adjustments"]
    )
    normalized["signal_win_counts"] = _coerce_dict(
        raw.get("signal_win_counts"), defaults["signal_win_counts"]
    )
    normalized["signal_loss_counts"] = _coerce_dict(
        raw.get("signal_loss_counts"), defaults["signal_loss_counts"]
    )
    normalized["strategy_confidence_adjustments"] = _coerce_dict(
        raw.get("strategy_confidence_adjustments"),
        defaults["strategy_confidence_adjustments"],
    )
    normalized["strategy_group_stats"] = _coerce_dict(
        raw.get("strategy_group_stats"), defaults["strategy_group_stats"]
    )

    raw_strategy_adjustments = _coerce_dict(
        raw.get("strategy_adjustments"), defaults["strategy_adjustments"]
    )
    merged_strategy_adjustments = {
        key: dict(value)
        for key, value in defaults["strategy_adjustments"].items()
    }
    for key, value in raw_strategy_adjustments.items():
        if isinstance(value, dict):
            base = dict(merged_strategy_adjustments.get(key) or {})
            base.update(value)
            merged_strategy_adjustments[key] = base
    normalized["strategy_adjustments"] = merged_strategy_adjustments

    normalized["total_trades_analyzed"] = int(raw.get("total_trades_analyzed") or 0)
    normalized["last_analysis_time"] = raw.get("last_analysis_time")
    normalized["cumulative_pnl"] = _safe_float(
        raw.get("cumulative_pnl"), defaults["cumulative_pnl"]
    )
    daily_pnl_history = raw.get("daily_pnl_history")
    normalized["daily_pnl_history"] = (
        list(daily_pnl_history) if isinstance(daily_pnl_history, list) else []
    )
    return normalized


def load_feedback_state(repo_root: Path | str | None = None) -> Dict[str, Any]:
    """Load the persisted adaptive feedback state if it exists."""
    path = feedback_state_path(repo_root)
    if not path.exists():
        return default_feedback_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return normalize_feedback_state(raw)
    except Exception:
        return default_feedback_state()


def infer_strategy_family(
    payload: Dict[str, Any] | None,
    default_family: Optional[str] = None,
) -> Optional[str]:
    """Infer the strategy family used for feedback bucketing.

    The return value is normalized to `day_trade` or `medium_long` when
    possible. If the payload does not contain enough information, the
    supplied default family is used as a fallback.
    """
    if not isinstance(payload, dict):
        payload = {}

    nested_payloads = []
    for key in ("metadata", "order_metadata"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            nested_payloads.append(nested)

    search_payloads = [payload, *nested_payloads]

    for source in search_payloads:
        candidates = (
            source.get("strategy_family"),
            source.get("strategy_name"),
            source.get("account"),
            source.get("family"),
            source.get("strategy_bucket"),
            source.get("underlying_strategy"),
        )
        for candidate in candidates:
            normalized = str(candidate or "").strip().lower()
            if normalized in {"day_trade", "medium_long"}:
                return normalized

    for source in search_payloads:
        text_fields = (
            source.get("strategy_style"),
            source.get("holding_period"),
            source.get("timeframe"),
            source.get("strategy"),
            source.get("playbook_transition"),
        )
        for candidate in text_fields:
            normalized = str(candidate or "").strip().lower()
            if not normalized:
                continue
            if any(
                token in normalized
                for token in (
                    "medium_long",
                    "swing",
                    "macro",
                    "position",
                    "long_hold",
                    "weekly",
                    "monthly",
                    "multi_day",
                    "overnight",
                )
            ):
                return "medium_long"
            if any(
                token in normalized
                for token in (
                    "day_trade",
                    "intraday",
                    "scalp",
                    "event_driven",
                    "opening",
                    "same_day",
                )
            ):
                return "day_trade"

    fallback = str(default_family or "").strip().lower()
    if fallback in {"day_trade", "medium_long"}:
        return fallback

    return None


def load_learning_context(
    repo_root: Path | str | None = None,
    bridge_signals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a normalized learning context from disk plus injected overrides."""
    state = load_feedback_state(repo_root)
    injected = bridge_signals or {}

    signal_adjustments = state.get("signal_adjustments", {}) or {}
    strategy_confidence_adjustments = state.get("strategy_confidence_adjustments", {}) or {}
    strategy_adjustments = state.get("strategy_adjustments", {}) or {}

    if isinstance(injected, dict):
        if isinstance(injected.get("_feedback_adjustments"), dict):
            signal_adjustments = injected["_feedback_adjustments"]
        if isinstance(injected.get("_feedback_strategy_confidence_adjustments"), dict):
            strategy_confidence_adjustments = injected["_feedback_strategy_confidence_adjustments"]
        if isinstance(injected.get("_feedback_strategy_adjustments"), dict):
            strategy_adjustments = injected["_feedback_strategy_adjustments"]

    return {
        "signal_adjustments": dict(signal_adjustments),
        "strategy_confidence_adjustments": dict(strategy_confidence_adjustments),
        "strategy_adjustments": dict(strategy_adjustments),
    }


def apply_learning_adjustments_to_idea(
    idea: Dict[str, Any],
    learning_context: Dict[str, Any],
    *,
    default_family: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply bounded feedback adjustments to a trade idea.

    This is intentionally conservative:
    - signal adjustments are small, global nudges
    - strategy confidence adjustments are bounded per exact strategy/family
    - strategy controls only adjust stop/target geometry
    """
    updated = dict(idea or {})
    if updated.get("learning_adjusted"):
        return updated

    signal_adjustments = dict(learning_context.get("signal_adjustments") or {})
    strategy_confidence_adjustments = dict(
        learning_context.get("strategy_confidence_adjustments") or {}
    )
    strategy_controls = dict(learning_context.get("strategy_adjustments") or {})

    strategy_name = str(updated.get("strategy") or updated.get("strategy_name") or "").strip()
    family = infer_strategy_family(updated, default_family=default_family)

    signal_delta = _clamp(
        sum(_safe_float(v) for v in signal_adjustments.values()),
        -0.12,
        0.12,
    )

    strategy_delta = 0.0
    if strategy_name and strategy_name in strategy_confidence_adjustments:
        strategy_delta += _safe_float(strategy_confidence_adjustments.get(strategy_name))
    if family and family in strategy_confidence_adjustments and family != strategy_name:
        strategy_delta += _safe_float(strategy_confidence_adjustments.get(family))
    strategy_delta = _clamp(strategy_delta, -0.12, 0.12)

    confidence_delta = _clamp(signal_delta * 0.5 + strategy_delta, -0.15, 0.15)

    controls = {}
    if strategy_name and strategy_name in strategy_controls:
        controls = dict(strategy_controls.get(strategy_name) or {})
    elif family and family in strategy_controls:
        controls = dict(strategy_controls.get(family) or {})

    stop_tightness = _clamp(_safe_float(controls.get("stop_loss_tightness"), 1.0), 0.5, 1.5)
    profit_target_mult = _clamp(_safe_float(controls.get("profit_target_mult"), 1.0), 0.5, 2.0)

    size_multiplier = _clamp(
        1.0
        + (confidence_delta * 0.6)
        + ((profit_target_mult - 1.0) * 0.25)
        - ((stop_tightness - 1.0) * 0.20),
        0.75,
        1.35,
    )

    def _adjust_pct(value: Any, *, widen: bool) -> Optional[float]:
        if value is None:
            return None
        pct = _safe_float(value, 0.0)
        if pct == 0.0:
            return round(pct, 3)
        sign = -1.0 if pct < 0 else 1.0
        base = abs(pct)
        if widen:
            base *= profit_target_mult
        else:
            base = base / max(stop_tightness, 0.5)
        return round(sign * base, 3)

    def _adjust_price_targets() -> None:
        entry = updated.get("entry")
        target = updated.get("target")
        stop = updated.get("stop")
        side = str(updated.get("side") or updated.get("direction") or "long").strip().lower()
        if entry is None or target is None or stop is None:
            return

        entry_f = _safe_float(entry, 0.0)
        target_f = _safe_float(target, 0.0)
        stop_f = _safe_float(stop, 0.0)
        if entry_f <= 0 or target_f <= 0 or stop_f <= 0:
            return

        if side == "short":
            target_dist = max(entry_f - target_f, 0.0)
            stop_dist = max(stop_f - entry_f, 0.0)
            if target_dist > 0:
                updated["target"] = round(entry_f - (target_dist * profit_target_mult), 2)
            if stop_dist > 0:
                updated["stop"] = round(entry_f + (stop_dist / max(stop_tightness, 0.5)), 2)
        else:
            target_dist = max(target_f - entry_f, 0.0)
            stop_dist = max(entry_f - stop_f, 0.0)
            if target_dist > 0:
                updated["target"] = round(entry_f + (target_dist * profit_target_mult), 2)
            if stop_dist > 0:
                updated["stop"] = round(entry_f - (stop_dist / max(stop_tightness, 0.5)), 2)

    confidence_field = None
    for candidate in ("confidence_adjusted_score", "confidence", "confidence_score"):
        if candidate in updated:
            confidence_field = candidate
            break

    if confidence_field is not None:
        base_conf = _safe_float(updated.get(confidence_field), 0.0)
        updated[confidence_field] = round(_clamp(base_conf + confidence_delta, 0.0, 0.99), 4)

    if "notional_usd" in updated:
        updated["notional_usd"] = round(_safe_float(updated.get("notional_usd"), 0.0) * size_multiplier, 2)
    if "size_usd" in updated:
        updated["size_usd"] = round(_safe_float(updated.get("size_usd"), 0.0) * size_multiplier, 2)
    if "take_profit_pct" in updated:
        updated["take_profit_pct"] = _adjust_pct(updated.get("take_profit_pct"), widen=True)
    if "stop_loss_pct" in updated:
        updated["stop_loss_pct"] = _adjust_pct(updated.get("stop_loss_pct"), widen=False)

    _adjust_price_targets()

    detail = dict(updated.get("learning_adjustment_detail") or {})
    detail.update(
        {
            "strategy": strategy_name,
            "strategy_family": family,
            "signal_delta": round(signal_delta, 4),
            "strategy_delta": round(strategy_delta, 4),
            "confidence_delta": round(confidence_delta, 4),
            "size_multiplier": round(size_multiplier, 4),
            "stop_loss_tightness": round(stop_tightness, 4),
            "profit_target_mult": round(profit_target_mult, 4),
        }
    )
    updated["learning_adjustment_detail"] = detail
    updated["learning_adjusted"] = True
    if family and "strategy_family" not in updated:
        updated["strategy_family"] = family

    return updated
