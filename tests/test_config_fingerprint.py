"""Tests for src/core/config_fingerprint.py"""
import pytest
from pathlib import Path
from src.core.config_fingerprint import compute_config_fingerprint


def test_computes_from_real_config():
    result = compute_config_fingerprint(config_dir=Path("config"))
    assert result["config_count"] >= 5
    assert result["combined_fingerprint"]
    assert len(result["combined_fingerprint"]) == 64  # SHA-256 hex


def test_individual_config_hashes():
    result = compute_config_fingerprint(config_dir=Path("config"))
    assert "promotion_policy.yaml" in result["configs"]
    assert "feature_registry.yaml" in result["configs"]
    assert len(result["configs"]["promotion_policy.yaml"]) == 64


def test_deterministic():
    r1 = compute_config_fingerprint(config_dir=Path("config"))
    r2 = compute_config_fingerprint(config_dir=Path("config"))
    assert r1["combined_fingerprint"] == r2["combined_fingerprint"]


def test_missing_dir_returns_empty():
    result = compute_config_fingerprint(config_dir=Path("/tmp/nonexistent_dir_xyz"))
    assert result["config_count"] == 0
    assert result["combined_fingerprint"] == ""
    assert len(result["missing_configs"]) > 0


def test_custom_config_names():
    result = compute_config_fingerprint(
        config_dir=Path("config"),
        config_names=["promotion_policy.yaml"],
    )
    assert result["config_count"] == 1
    assert "promotion_policy.yaml" in result["configs"]
