#!/usr/bin/env python3
"""Typed loader for ``config/feature_registry.yaml``.

Provides a validated, typed view over the canonical V4 feature registry so
runtime governance and dashboard surfaces do not need to parse raw YAML.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


@dataclass(frozen=True)
class FeatureDefinition:
    """Canonical metadata for a single feature."""

    name: str
    source: str
    version: str
    type: str
    range: Optional[Tuple[Any, Any]]
    freshness_ttl_minutes: int
    description: str
    categories: Tuple[str, ...] = ()
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": self.name,
            "source": self.source,
            "version": self.version,
            "type": self.type,
            "freshness_ttl_minutes": self.freshness_ttl_minutes,
            "description": self.description,
        }
        if self.range is not None:
            payload["range"] = list(self.range)
        if self.categories:
            payload["categories"] = list(self.categories)
        payload.update(self.extra)
        return payload


@dataclass
class FeatureRegistry:
    """Typed registry loaded from YAML."""

    schema_version: str = "feature_registry.v1"
    features: Dict[str, FeatureDefinition] = field(default_factory=dict)
    validation_errors: List[str] = field(default_factory=list)

    def get_feature(self, name: str) -> Optional[FeatureDefinition]:
        """Return a feature definition by name."""
        return self.features.get(name)

    def list_features(self) -> List[FeatureDefinition]:
        """Return all features sorted by name."""
        return [self.features[name] for name in sorted(self.features)]

    def get_features_by_source(self, source: str) -> List[FeatureDefinition]:
        """Return all features emitted by a source."""
        target = str(source)
        return [
            feature
            for feature in self.list_features()
            if feature.source == target
        ]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the registry for dashboard/API use."""
        return {
            "schema_version": self.schema_version,
            "feature_count": len(self.features),
            "validation_errors": list(self.validation_errors),
            "features": {
                name: feature.to_dict()
                for name, feature in sorted(self.features.items())
            },
        }


def _coerce_range(name: str, raw: Dict[str, Any], errors: List[str]) -> Optional[Tuple[Any, Any]]:
    value = raw.get("range")
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        errors.append(f"{name}: range must be a 2-item list")
        return None
    return (value[0], value[1])


def _coerce_categories(name: str, raw: Dict[str, Any], errors: List[str]) -> Tuple[str, ...]:
    value = raw.get("categories", [])
    if value is None:
        return ()
    if not isinstance(value, list):
        errors.append(f"{name}: categories must be a list")
        return ()
    return tuple(str(item) for item in value)


def _parse_feature(name: str, raw: Dict[str, Any], errors: List[str]) -> Optional[FeatureDefinition]:
    if not isinstance(raw, dict):
        errors.append(f"{name}: definition must be a mapping")
        return None

    source = str(raw.get("source", "")).strip()
    version = str(raw.get("version", "")).strip()
    feature_type = str(raw.get("type", "")).strip()
    description = str(raw.get("description", "")).strip()
    if not source:
        errors.append(f"{name}: missing source")
    if not version:
        errors.append(f"{name}: missing version")
    if not feature_type:
        errors.append(f"{name}: missing type")
    if not description:
        errors.append(f"{name}: missing description")

    ttl_raw = raw.get("freshness_ttl_minutes", 0)
    try:
        ttl = int(ttl_raw)
    except Exception:
        errors.append(f"{name}: freshness_ttl_minutes must be an integer")
        ttl = 0
    if ttl <= 0:
        errors.append(f"{name}: freshness_ttl_minutes must be > 0")

    feature_range = _coerce_range(name, raw, errors)
    categories = _coerce_categories(name, raw, errors)

    # Numeric features should declare a range. Categorical features should
    # declare either categories or a range.
    if feature_type == "numeric" and feature_range is None:
        errors.append(f"{name}: numeric features require a range")
    if feature_type == "categorical" and not categories and feature_range is None:
        errors.append(f"{name}: categorical features require categories or range")

    if source and version and feature_type and description and ttl > 0:
        extra = {
            key: value
            for key, value in raw.items()
            if key not in {
                "source",
                "version",
                "type",
                "range",
                "freshness_ttl_minutes",
                "description",
                "categories",
            }
        }
        return FeatureDefinition(
            name=name,
            source=source,
            version=version,
            type=feature_type,
            range=feature_range,
            freshness_ttl_minutes=ttl,
            description=description,
            categories=categories,
            extra=extra,
        )
    return None


def load_feature_registry(config_path: Optional[Path] = None) -> FeatureRegistry:
    """Load and validate the V4 feature registry from YAML."""
    if config_path is None:
        config_path = Path("config/feature_registry.yaml")

    if yaml is None:
        logger.warning("pyyaml not available, using empty feature registry")
        return FeatureRegistry(validation_errors=["pyyaml_unavailable"])

    if not config_path.exists():
        logger.warning("Feature registry not found at %s", config_path)
        return FeatureRegistry(validation_errors=[f"missing_file:{config_path}"])

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.error("Failed to parse feature registry: %s", exc)
        return FeatureRegistry(validation_errors=[f"parse_error:{exc}"])

    errors: List[str] = []
    features: Dict[str, FeatureDefinition] = {}
    for name, payload in (raw.get("features") or {}).items():
        parsed = _parse_feature(str(name), payload, errors)
        if parsed is not None:
            features[parsed.name] = parsed

    schema_version = str(raw.get("schema_version", "feature_registry.v1"))
    return FeatureRegistry(
        schema_version=schema_version,
        features=features,
        validation_errors=errors,
    )
