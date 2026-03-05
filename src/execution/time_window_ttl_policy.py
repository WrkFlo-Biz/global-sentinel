#!/usr/bin/env python3
"""
Global Sentinel V5.0 - Time Window TTL Policy Engine

Purpose:
- Resolve stale-intent TTL (minutes) using:
  - time window hint/runtime flags
  - strategy style
  - symbol
  - policy defaults
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

import yaml


class TimeWindowTTLPolicyEngine:
    def __init__(self, policy: Dict[str, Any]):
        self.policy = policy or {}

    @classmethod
    def from_yaml_file(cls, path: Path) -> "TimeWindowTTLPolicyEngine":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        return cls(data or {})

    def resolve_ttl_minutes(self, intent_row: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        """
        Returns (ttl_minutes, explanation_dict)
        """
        defaults = (self.policy.get("defaults") or {})
        ttl = float(defaults.get("stale_after_minutes", 30))

        cand = intent_row.get("candidate_context") or {}
        runtime_flags = (intent_row.get("extra_context") or {}).get("runtime_flags") or {}
        pkg_ctx = intent_row.get("package_context") or {}
        order_req = intent_row.get("order_request") or {}
        order_strategy_ctx = (order_req.get("strategy_context") or {})
        window_context = (intent_row.get("window_context") or {})

        symbol = str(cand.get("symbol") or "").upper() if cand.get("symbol") else None
        strategy = str(cand.get("strategy_style") or "").strip() if cand.get("strategy_style") else None

        time_window_name = (
            runtime_flags.get("time_window_hint")
            or window_context.get("time_window_name")
            or pkg_ctx.get("time_window_name")
            or order_strategy_ctx.get("time_window_name")
            or "unknown"
        )
        time_window_name = str(time_window_name)

        reasons = [{"layer": "default", "ttl_minutes": ttl}]

        # Time window override
        tw_cfg = ((self.policy.get("time_windows") or {}).get(time_window_name))
        if tw_cfg and tw_cfg.get("stale_after_minutes") is not None:
            ttl = float(tw_cfg["stale_after_minutes"])
            reasons.append({"layer": "time_window", "time_window_name": time_window_name, "ttl_minutes": ttl})

        # Strategy override
        if strategy:
            s_cfg = ((self.policy.get("strategy_overrides") or {}).get(strategy))
            if s_cfg and s_cfg.get("stale_after_minutes") is not None:
                ttl = float(s_cfg["stale_after_minutes"])
                reasons.append({"layer": "strategy", "strategy_style": strategy, "ttl_minutes": ttl})

        # Symbol override (highest precedence)
        if symbol:
            sym_cfg = ((self.policy.get("symbol_overrides") or {}).get(symbol))
            if sym_cfg and sym_cfg.get("stale_after_minutes") is not None:
                ttl = float(sym_cfg["stale_after_minutes"])
                reasons.append({"layer": "symbol", "symbol": symbol, "ttl_minutes": ttl})

        explanation = {
            "resolved_ttl_minutes": ttl,
            "time_window_name": time_window_name,
            "strategy_style": strategy,
            "symbol": symbol,
            "reasons": reasons,
        }
        return ttl, explanation
