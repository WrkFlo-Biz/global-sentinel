from pathlib import Path

from src.core.openclaw_role_registry import load_openclaw_role_registry


def test_load_openclaw_role_registry_filters_roles_by_bot(tmp_path: Path):
    registry_path = tmp_path / "openclaw_role_registry.yaml"
    registry_path.write_text(
        """
schema_version: openclaw_role_registry.v1
default_backend: openclaw
roles:
  cio:
    title: CIO
    bot: research
    backend: openclaw
    prompt_path: prompts/cowork-cio.md
    output_dir: reports/openclaw_research
    artifact_prefix: cio
    every_n_seeds: 1
    enabled: true
    telegram_updates: true
    paper_only: true
  coo:
    title: COO
    bot: ops
    backend: openclaw
    prompt_path: prompts/cowork-coo.md
    output_dir: reports/openclaw_ops
    artifact_prefix: coo
    every_n_seeds: 2
    enabled: true
    telegram_updates: false
    paper_only: true
""".strip(),
        encoding="utf-8",
    )

    registry = load_openclaw_role_registry(registry_path)

    assert registry.schema_version == "openclaw_role_registry.v1"
    assert [role.role_id for role in registry.roles_for_bot("research")] == ["cio"]
    assert [role.role_id for role in registry.roles_for_bot("ops")] == ["coo"]
    assert registry.roles["coo"].every_n_seeds == 2
