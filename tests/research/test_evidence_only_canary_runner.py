"""Tests for evidence-only canary runner."""
from __future__ import annotations

import json
from pathlib import Path

from src.research.evidence_only_canary_runner import EvidenceOnlyCanaryRunner


def _write_scorecard(path: Path, *, cycle: int, ts: str, fingerprint: str, eligible: bool = True) -> None:
    payload = {
        "schema_version": "scorecard.v6",
        "timestamp_utc": ts,
        "cycle": cycle,
        "mode": "NORMAL",
        "shadow_execution_eligible": eligible,
        "confidence": 0.72,
        "regime_shift_probability": 0.41,
        "freshness_penalty": 0.0,
        "degraded_mode": False,
        "config_fingerprint": fingerprint,
        "mode_decision_trace": {"blocked": False},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_runner_emits_evidence_only_artifact(tmp_path: Path):
    repo_root = tmp_path
    scorecards_dir = repo_root / "logs" / "scorecards"
    for i in range(1, 5):
        _write_scorecard(
            scorecards_dir / f"scorecard_{i:03d}.json",
            cycle=i,
            ts=f"2026-03-07T20:0{i}:00+00:00",
            fingerprint="fp-1",
        )

    artifact = EvidenceOnlyCanaryRunner(repo_root).run(limit=10, window_size=2)

    assert artifact["schema_version"] == "evidence_only_canary_artifact.v1"
    assert artifact["canary_evidence_only"] is True
    assert artifact["autonomous_promotion_forbidden"] is True
    assert "policy_decision" in artifact
    assert artifact["current_window"]["scorecard_count"] == 2
    assert (repo_root / "reports" / "research" / "canary" / "latest.json").exists()


def test_runner_skips_cleanly_without_scorecards(tmp_path: Path):
    artifact = EvidenceOnlyCanaryRunner(tmp_path).run(limit=10, window_size=2)

    assert artifact["status"] == "skipped"
    assert artifact["canary_evidence_only"] is True
    assert artifact["promotion_allowed_if_not_canary"] is False
