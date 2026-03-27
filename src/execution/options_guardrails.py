#!/usr/bin/env python3
"""
Global Sentinel - Options Guardrails

Phased gatekeeper for options rollout. Equity flow remains unaffected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except Exception:  # pragma: no cover - optional at import-time
    yaml = None


class OptionsGuardrails:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        path = self.repo_root / "config" / "options_rollout.yaml"
        default = {
            "options": {
                "enabled": False,
                "kill_switch": True,
                "phase": "disabled",
                "allowed_strategies": [],
                "max_contracts_per_order": 1,
                "min_open_interest": 500,
                "min_daily_volume": 250,
                "max_bid_ask_spread_pct": 2.5,
                "max_days_to_expiry": 45,
                "min_days_to_expiry": 7,
                "allow_naked_short": False,
            }
        }
        if not path.exists() or yaml is None:
            return default
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            merged = dict(default)
            merged.update(loaded)
            return merged
        except Exception:
            return default

    def evaluate_candidate(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        instrument_types = [str(x).lower() for x in (candidate.get("instrument_types") or [])]
        if not any("option" in x for x in instrument_types):
            return {"pass": True, "reason_code": None, "details": {}}

        cfg = (self.config or {}).get("options") or {}
        if not bool(cfg.get("enabled", False)):
            return {"pass": False, "reason_code": "options_disabled", "details": {"phase": cfg.get("phase")}}
        if bool(cfg.get("kill_switch", False)):
            return {"pass": False, "reason_code": "options_kill_switch_active", "details": {}}

        side = str(candidate.get("side", "")).lower()
        if side in {"sell", "short"} and not bool(cfg.get("allow_naked_short", False)):
            return {"pass": False, "reason_code": "options_naked_short_not_allowed", "details": {"side": side}}

        metadata = candidate.get("metadata") or {}
        oi = _to_float(metadata.get("open_interest"))
        vol = _to_float(metadata.get("daily_volume"))
        spread = _to_float(metadata.get("bid_ask_spread_pct"))
        dte = _to_float(metadata.get("days_to_expiry"))

        if oi is not None and oi < _to_float(cfg.get("min_open_interest"), 500):
            return {"pass": False, "reason_code": "options_open_interest_too_low", "details": {"open_interest": oi}}
        if vol is not None and vol < _to_float(cfg.get("min_daily_volume"), 250):
            return {"pass": False, "reason_code": "options_volume_too_low", "details": {"daily_volume": vol}}
        if spread is not None and spread > _to_float(cfg.get("max_bid_ask_spread_pct"), 2.5):
            return {"pass": False, "reason_code": "options_spread_too_wide", "details": {"bid_ask_spread_pct": spread}}
        if dte is not None:
            min_dte = _to_float(cfg.get("min_days_to_expiry"), 7)
            max_dte = _to_float(cfg.get("max_days_to_expiry"), 45)
            if dte < min_dte or dte > max_dte:
                return {
                    "pass": False,
                    "reason_code": "options_dte_out_of_bounds",
                    "details": {"days_to_expiry": dte, "min": min_dte, "max": max_dte},
                }

        return {"pass": True, "reason_code": None, "details": {"phase": cfg.get("phase")}}


def _to_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default
