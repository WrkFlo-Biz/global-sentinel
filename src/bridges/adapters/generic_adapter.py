#!/usr/bin/env python3
"""Generic wrapper for existing bridge implementations."""
from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Sequence

from src.bridges.base_bridge import BaseBridge, utc_now_iso


@dataclass(frozen=True)
class ExistingBridgeSpec:
    name: str
    module_path: str
    class_name: str
    aliases: Sequence[str] = field(default_factory=tuple)
    preferred_methods: Sequence[str] = field(default_factory=lambda: ("fetch", "build_snapshot_section", "poll"))
    source_tier: str = ""
    trust_weight: float = 0.0
    freshness_ttl_minutes: int = 60


class ExistingBridgeAdapter(BaseBridge):
    """Wrap an existing class exposing fetch/poll/build_snapshot_section."""

    def __init__(self, spec: ExistingBridgeSpec, repo_root=None, config: Optional[dict] = None):
        super().__init__(repo_root=repo_root, config=config)
        self.spec = spec
        self.source = spec.name
        self.source_tier = spec.source_tier
        self.trust_weight = float(spec.trust_weight)
        self.freshness_ttl_minutes = int(spec.freshness_ttl_minutes)
        self._inner = self._instantiate_inner()

    def fetch(self) -> Dict[str, Any]:
        if self._inner is None:
            return self._mark_failure("inner_bridge_not_loaded")

        last_error: Optional[Exception] = None
        for method_name in self.spec.preferred_methods:
            method = getattr(self._inner, method_name, None)
            if not callable(method):
                continue
            try:
                raw = method()
                payload = self._normalize_payload(raw, method_name)
                return self._mark_success(payload)
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:  # pragma: no cover - exercised in integration
                last_error = exc
                break
        return self._mark_failure(last_error or "no_supported_fetch_method")

    def _instantiate_inner(self) -> Any:
        module = importlib.import_module(self.spec.module_path)
        cls = getattr(module, self.spec.class_name)
        sig = inspect.signature(cls)
        required = [
            p
            for p in sig.parameters.values()
            if p.name != "self"
            and p.default is inspect._empty
            and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]

        if not required:
            return cls()

        if len(required) == 1 and "root" in required[0].name.lower():
            param = required[0]
            if param.kind is inspect.Parameter.POSITIONAL_ONLY:
                return cls(self.repo_root)
            return cls(**{param.name: self.repo_root})

        kwargs: Dict[str, Any] = {}
        for param in sig.parameters.values():
            if param.name == "self":
                continue
            if param.name in {"repo_root", "root", "repo_path"}:
                kwargs[param.name] = self.repo_root
            elif param.name == "config":
                kwargs[param.name] = self.config
            elif param.default is inspect._empty:
                raise TypeError(f"Unsupported constructor for {self.spec.class_name}: requires {param.name}")
        return cls(**kwargs)

    def _normalize_payload(self, raw: Any, method_name: str) -> Dict[str, Any]:
        return {
            "source": self.source,
            "source_tier": self.source_tier,
            "trust_weight": self.trust_weight,
            "timestamp_utc": utc_now_iso(),
            "fresh": self._infer_fresh(raw),
            "data": raw,
            "method_used": method_name,
            "record_count": self._record_count(raw),
        }

    def _record_count(self, raw: Any) -> int:
        if isinstance(raw, list):
            return len(raw)
        if isinstance(raw, dict):
            for key in ("event_count", "packet_count", "symbol_count", "count", "filing_count"):
                value = raw.get(key)
                if isinstance(value, (int, float)):
                    return int(value)
            return len(raw)
        return 1 if raw is not None else 0

    def _infer_fresh(self, raw: Any) -> bool:
        if raw is None:
            return False
        if isinstance(raw, dict):
            fresh = raw.get("fresh")
            if isinstance(fresh, bool):
                return fresh
            numeric_keys = (
                "event_count",
                "packet_count",
                "symbol_count",
                "count",
                "filing_count",
                "disclosure_count",
                "active_alerts",
            )
            for key in numeric_keys:
                value = raw.get(key)
                if isinstance(value, (int, float)):
                    return value > 0
            if "data" in raw and raw["data"] is None:
                return False
            return bool(raw)
        if isinstance(raw, list):
            return len(raw) > 0
        return True

    @property
    def aliases(self) -> Iterable[str]:
        return self.spec.aliases
