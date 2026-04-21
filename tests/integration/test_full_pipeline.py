"""Integration coverage for the Wave 5 governance pipeline."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.responses import JSONResponse

from src.core.feature_freshness_enforcer import FeatureFreshnessEnforcer
from src.core.feature_registry_loader import load_feature_registry
from src.core.promotion_policy_loader import load_promotion_policy
from src.core.source_quorum_engine import SourceQuorumEngine
from src.lineage.artifact_manifest_builder import ArtifactManifestBuilder, LineageResolver
from src.research.attach_research_score_to_snapshot import attach_research_score
from src.research.encoder_promotion_gate import EncoderPromotionGate


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
PASSING_METRICS = {
    "eval_days": 120,
    "trade_count": 300,
    "drawdown_delta_bps": 20,
    "slippage_adjusted_win_delta_bps": 25,
    "failure_rate": 0.01,
    "cumulative_drift_std": 0.5,
}


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 3, 7, 19, 0, tzinfo=timezone.utc)


def _load_trust_hierarchy() -> dict:
    return yaml.safe_load((CONFIG_DIR / "data_trust_hierarchy.yaml").read_text(encoding="utf-8")) or {}


def _build_manifest(artifact_type: str, parent_ids: list[str], content: dict) -> dict:
    manifest = (
        ArtifactManifestBuilder()
        .set_type(artifact_type)
        .set_parents(parent_ids)
        .set_source_packets(["packet-a", "packet-b"])
        .set_incident_mode("NORMAL")
        .set_code_version_from_git()
        .set_content_hash(content)
        .build()
    )
    return manifest.to_dict()


def test_feature_registry_loader_reads_real_config():
    registry = load_feature_registry(CONFIG_DIR / "feature_registry.yaml")

    assert registry.schema_version == "feature_registry.v1"
    assert registry.validation_errors == []
    assert registry.get_feature("base_score") is not None
    assert registry.get_feature("gamma_squeeze_risk").categories == ("low", "moderate", "elevated", "high")
    assert {feature.name for feature in registry.get_features_by_source("options_greeks_bridge")} == {
        "put_call_ratio",
        "implied_volatility",
        "gamma_squeeze_risk",
    }


def test_full_pipeline_happy_path(now: datetime):
    enforcer = FeatureFreshnessEnforcer(config_dir=CONFIG_DIR)
    freshness = enforcer.check_group(
        "core_scoring",
        {
            "base_score": now - timedelta(minutes=5),
            "event_score": now - timedelta(minutes=3),
            "quality_score": now - timedelta(minutes=8),
            "anomaly_score": now - timedelta(minutes=10),
        },
        now,
    )
    assert freshness.compliant is True
    assert freshness.confidence_penalty == 0.0

    snapshot_features = {
        "base_score": 0.74,
        "event_score": 0.62,
        "quality_score": 0.91,
        "anomaly_score": 0.18,
    }
    snapshot_manifest = _build_manifest("feature_store_snapshot", [], snapshot_features)
    snapshot = {
        "_artifact_id": snapshot_manifest["artifact_id"],
        "confidence": 0.82,
        "feature_store_snapshot": snapshot_features,
    }

    research_payload = {
        "research_score": 0.68,
        "recommended_influence": 0.1,
        "guardrails": {
            "not_for_direct_execution": True,
            "bounded_secondary_signal_only": True,
            "quantum_direct_execution_forbidden": True,
        },
        "request_id": "packet-a",
        "package_id": "packet-b",
    }
    research_manifest = _build_manifest("research_score", [snapshot_manifest["artifact_id"]], research_payload)
    research_score = {
        **research_payload,
        "_artifact_id": research_manifest["artifact_id"],
    }

    attached = attach_research_score(snapshot, research_score, incident_mode="NORMAL")
    assert attached["runtime_flags"]["quantum_research_attached"] is True
    assert attached["runtime_flags"]["quantum_direct_execution_forbidden"] is True

    resolver = LineageResolver()
    resolver.register_dict(snapshot_manifest)
    resolver.register_dict(research_manifest)
    resolver.register_dict(attached["_artifact_manifest"])
    lineage_validation = resolver.validate_lineage(attached["_artifact_id"])
    assert lineage_validation["valid"] is True

    gate = EncoderPromotionGate(config_path=CONFIG_DIR / "promotion_policy.yaml")
    promotion = gate.evaluate(
        PASSING_METRICS,
        guardrail_result={"passed": True},
        policy_decision={"allowed": True},
        signal_type="online_weighted_encoder",
        current_mode="NORMAL",
    )
    assert promotion.allowed is True

    quorum_engine = SourceQuorumEngine(config_dir=CONFIG_DIR)
    # Use real-time timestamps since quorum engine checks against wall clock
    real_now = datetime.now(timezone.utc).isoformat()
    quorum = quorum_engine.check_execution_quorum(
        {
            "fed": real_now,
            "fred": real_now,
            "gdelt": real_now,
        },
        _load_trust_hierarchy(),
    )
    assert quorum["quorum_met"] is True

    policy = load_promotion_policy(CONFIG_DIR / "promotion_policy.yaml")
    shadow_draft_eligible = (
        freshness.compliant
        and promotion.allowed
        and quorum["quorum_met"]
        and lineage_validation["valid"]
        and not policy.is_mode_frozen("NORMAL")
    )
    assert shadow_draft_eligible is True


def test_full_pipeline_stale_packet_applies_confidence_penalty(now: datetime):
    enforcer = FeatureFreshnessEnforcer(config_dir=CONFIG_DIR)
    freshness = enforcer.check_group(
        "market_microstructure",
        {
            "liquidity_score": now - timedelta(minutes=1),
            "volatility_penalty": now - timedelta(minutes=15),
        },
        now,
    )

    adjusted_confidence = 0.8 * (1.0 - freshness.confidence_penalty)
    assert freshness.compliant is False
    assert freshness.confidence_penalty > 0
    assert adjusted_confidence < 0.8
    assert any(result.status in {"stale", "expired"} for result in freshness.feature_results)


def test_full_pipeline_failed_quorum_blocks_escalation(now: datetime):
    quorum_engine = SourceQuorumEngine(config_dir=CONFIG_DIR)
    quorum = quorum_engine.check_execution_quorum(
        {
            "fed": now.isoformat(),
            "fred": (now - timedelta(days=3)).isoformat(),
            "gdelt": (now - timedelta(days=2)).isoformat(),
        },
        _load_trust_hierarchy(),
    )

    assert quorum["quorum_met"] is False
    assert quorum["fresh_total"] < quorum["required_total"]
    assert "fred" in quorum["stale_sources"]


def test_full_pipeline_crisis_mode_blocks_promotion():
    gate = EncoderPromotionGate(config_path=CONFIG_DIR / "promotion_policy.yaml")
    result = gate.evaluate(PASSING_METRICS, signal_type="default", current_mode="CRISIS")

    assert result.allowed is False
    assert "mode_frozen" in result.reason
    assert result.gate_results[0]["gate"] == "frozen_mode"


def test_full_pipeline_blocked_signal_rejects_with_reason():
    gate = EncoderPromotionGate(config_path=CONFIG_DIR / "promotion_policy.yaml")
    result = gate.evaluate(PASSING_METRICS, signal_type="politician_alpha", current_mode="NORMAL")

    assert result.allowed is False
    assert "promotion_blocked" in result.reason
    assert "political_disclosure_research_only" in result.reason


def test_full_pipeline_missing_manifest_fails_lineage_validation():
    snapshot = {"_artifact_id": "missing-snapshot-manifest"}
    research_score = {
        "_artifact_id": "missing-research-manifest",
        "research_score": 0.55,
        "recommended_influence": 0.05,
        "guardrails": {"not_for_direct_execution": True},
        "request_id": "packet-a",
        "package_id": "packet-b",
    }

    attached = attach_research_score(snapshot, research_score, incident_mode="NORMAL")

    resolver = LineageResolver()
    resolver.register_dict(attached["_artifact_manifest"])
    validation = resolver.validate_lineage(attached["_artifact_id"])

    assert validation["valid"] is False
    assert set(validation["missing_parents"]) == {
        "missing-snapshot-manifest",
        "missing-research-manifest",
    }


def test_dashboard_v4_governance_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, now: datetime):
    import dashboard.api.server as server

    temp_root = tmp_path / "repo"
    (temp_root / "config").mkdir(parents=True)
    (temp_root / "logs" / "lineage").mkdir(parents=True)

    for name in (
        "feature_registry.yaml",
        "feature_group_registry.yaml",
        "promotion_policy.yaml",
    ):
        shutil.copy2(CONFIG_DIR / name, temp_root / "config" / name)

    (temp_root / "logs" / "feature_timestamps.json").write_text(
        json.dumps(
            {
                "feature_timestamps": {
                    "liquidity_score": (now - timedelta(minutes=1)).isoformat(),
                    "volatility_penalty": (now - timedelta(minutes=20)).isoformat(),
                    "base_score": (now - timedelta(minutes=5)).isoformat(),
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest = (
        ArtifactManifestBuilder()
        .set_type("integration_artifact")
        .set_source_packets(["packet-a"])
        .set_content_hash({"hello": "world"})
        .build()
    )
    (temp_root / "logs" / "lineage" / "manifests.jsonl").write_text(
        json.dumps(manifest.to_dict()) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(server, "REPO_ROOT", temp_root)

    governance = server.v4_governance_status()
    assert governance["policy"]["blocked_signals"] == ["politician_alpha"]
    assert "blocked_examples" in governance["decision_trace"]

    features = server.v4_feature_registry()
    assert features["registry"]["feature_count"] >= 1
    assert "options_greeks_bridge" in features["features_by_source"]

    freshness = server.v4_feature_freshness()
    assert freshness["decision_trace"]["timestamp_trace"]["timestamp_source"].endswith("feature_timestamps.json")
    assert freshness["groups"]["market_microstructure"]["compliant"] is False
    assert any(
        trace["feature_name"] == "volatility_penalty" and trace["reason"]
        for trace in freshness["groups"]["market_microstructure"]["decision_trace"]
    )

    lineage = server.v4_lineage_lookup(manifest.artifact_id)
    assert lineage["artifact_id"] == manifest.artifact_id
    assert lineage["validation"]["valid"] is True

    missing = server.v4_lineage_lookup("not-found")
    assert isinstance(missing, JSONResponse)
    assert missing.status_code == 404
