"""Tests for src/core/freshness_policy_loader.py"""
import pytest
from pathlib import Path
from src.core.freshness_policy_loader import load_freshness_policy


def test_loads_from_real_config():
    config = load_freshness_policy(Path("config/freshness_policy.yaml"))
    assert config.schema_version == "freshness_policy.v1"
    assert len(config.sources) > 10


def test_source_policy_fields():
    config = load_freshness_policy(Path("config/freshness_policy.yaml"))
    fed = config.get_source_policy("fed")
    assert fed is not None
    assert fed.ttl_minutes == 60
    assert fed.backoff_strategy == "exponential"
    assert fed.stale_action == "degrade_trust_weight"


def test_gdelt_short_ttl():
    config = load_freshness_policy(Path("config/freshness_policy.yaml"))
    gdelt = config.get_source_policy("gdelt")
    assert gdelt is not None
    assert gdelt.ttl_minutes == 30


def test_quorum_requirements():
    config = load_freshness_policy(Path("config/freshness_policy.yaml"))
    eq = config.get_quorum_requirements("execution_escalation")
    assert eq is not None
    assert eq.required_tier1_sources == 2
    assert eq.min_freshness_sources == 3


def test_list_sources():
    config = load_freshness_policy(Path("config/freshness_policy.yaml"))
    sources = config.list_sources()
    assert "fed" in sources
    assert "gdelt" in sources
    assert "maritime" in sources


def test_unknown_source_returns_none():
    config = load_freshness_policy(Path("config/freshness_policy.yaml"))
    assert config.get_source_policy("nonexistent") is None


def test_missing_file_returns_defaults():
    config = load_freshness_policy(Path("/tmp/nonexistent_freshness.yaml"))
    assert len(config.sources) == 0
