import json
from pathlib import Path

from src.core.openclaw_role_registry import load_openclaw_role_registry
from src.reports.openclaw_role_briefing import OpenClawRoleBriefingBuilder


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_registry(repo_root: Path) -> None:
    (repo_root / "config").mkdir(parents=True, exist_ok=True)
    (repo_root / "config" / "openclaw_role_registry.yaml").write_text(
        """
schema_version: openclaw_role_registry.v1
default_backend: openclaw
roles:
  cio:
    title: Chief Investment Officer
    bot: research
    backend: openclaw
    prompt_path: prompts/cowork-cio.md
    output_dir: reports/openclaw_research
    artifact_prefix: cio_brief
    every_n_seeds: 1
    enabled: true
    telegram_updates: false
    paper_only: true
  volatility_researcher:
    title: Volatility Researcher
    bot: research
    backend: openclaw
    prompt_path: prompts/cowork-volatility-researcher.md
    output_dir: reports/openclaw_research
    artifact_prefix: volatility_research_brief
    every_n_seeds: 1
    enabled: true
    telegram_updates: false
    paper_only: true
""".strip(),
        encoding="utf-8",
    )


def test_builds_cio_role_brief_from_runtime_artifacts(tmp_path: Path):
    repo_root = tmp_path
    _write_registry(repo_root)
    _write_json(
        repo_root / "logs" / "scorecards" / "scorecard_latest.json",
        {
            "timestamp_utc": "2026-03-07T20:00:00+00:00",
            "mode": "ELEVATED",
            "regime_shift_probability": 0.73,
            "confidence": 0.61,
            "component_scores": {"market_volatility": 0.8, "commodity_shock": 0.6},
        },
    )
    _write_json(repo_root / "reports" / "operational" / "canary_readiness_report.json", {"readiness_status": "GO"})
    _write_json(
        repo_root / "reports" / "operational" / "canary_observability_report.json",
        {"summary": {"rollback_recommended_count": 2, "total_canary_artifacts": 2}},
    )
    _write_json(
        repo_root / "reports" / "operational" / "canary_stabilization_report.json",
        {"summary": {"latest_session": "regular"}},
    )
    _write_json(
        repo_root / "reports" / "operational" / "canary_stabilization_checkpoint.json",
        {
            "checkpoint_status": "continue_stabilization_collect_maturity",
            "primary_blockers": ["evidence_maturity", "policy_gate_still_blocking"],
        },
    )
    _write_json(
        repo_root / "reports" / "operational" / "blob_persistence_health.json",
        {"status": "healthy", "persistence_mode": "blob_primary"},
    )

    role = load_openclaw_role_registry(repo_root / "config" / "openclaw_role_registry.yaml").roles["cio"]
    artifact = OpenClawRoleBriefingBuilder(repo_root).build_role_artifact(role)

    assert artifact["role_id"] == "cio"
    assert artifact["status"] == "yellow"
    assert artifact["safety"]["paper_only"] is True
    assert any("Mode: ELEVATED" in fact for fact in artifact["observed_facts"])
    assert artifact["metrics"]["rollback_recommended_count"] == 2
    assert artifact["metrics"]["checkpoint_status"] == "continue_stabilization_collect_maturity"
    assert any("Checkpoint status" in fact for fact in artifact["observed_facts"])


def test_builds_volatility_research_role_with_focus_areas(tmp_path: Path):
    repo_root = tmp_path
    _write_registry(repo_root)
    _write_json(
        repo_root / "logs" / "scorecards" / "scorecard_latest.json",
        {
            "timestamp_utc": "2026-03-07T20:00:00+00:00",
            "mode": "NORMAL",
            "regime_shift_probability": 0.44,
            "confidence": 0.58,
            "component_scores": {
                "commodity_shock": 0.92,
                "market_volatility": 0.84,
                "credit_spread": 0.77,
            },
        },
    )
    _write_json(
        repo_root / "reports" / "operational" / "canary_observability_report.json",
        {"summary": {"rollback_recommended_count": 1}},
    )
    _write_json(
        repo_root / "reports" / "operational" / "canary_stabilization_report.json",
        {"recommendations": ["Keep canary in stabilization."]},
    )
    _write_json(
        repo_root / "reports" / "operational" / "canary_stabilization_checkpoint.json",
        {"primary_blockers": ["evidence_maturity"]},
    )

    role = load_openclaw_role_registry(repo_root / "config" / "openclaw_role_registry.yaml").roles["volatility_researcher"]
    artifact = OpenClawRoleBriefingBuilder(repo_root).build_role_artifact(role)

    assert artifact["role_id"] == "volatility_researcher"
    assert artifact["safety"]["no_live_orders"] is True
    focus_areas = artifact["metrics"]["focus_areas"]
    assert "energy shock / transport stress paper basket" in focus_areas
    assert "index volatility and hedge paper basket" in focus_areas
    assert any("Checkpoint blockers" in fact for fact in artifact["observed_facts"])
    assert any("paper research" in action.lower() for action in artifact["actions"])


def test_load_context_uses_shared_control_snapshot_file_semantics(tmp_path: Path):
    repo_root = tmp_path
    _write_json(repo_root / "control" / "manual_veto.json", {"manual_veto": True})
    (repo_root / "control" / "kill_switch.json").write_text("{bad-json", encoding="utf-8")

    context = OpenClawRoleBriefingBuilder(repo_root)._load_context()

    assert context["control_flags"] == {
        "kill_switch": False,
        "manual_veto": True,
    }
