#!/usr/bin/env python3
"""Typed loader for OpenClaw role-subagent configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml


@dataclass(frozen=True)
class OpenClawRoleConfig:
    """Single role definition for OpenClaw subagents."""

    role_id: str
    title: str
    bot: str
    backend: str
    prompt_path: str
    output_dir: str
    artifact_prefix: str
    every_n_seeds: int
    enabled: bool
    telegram_updates: bool
    paper_only: bool


@dataclass(frozen=True)
class OpenClawRoleRegistry:
    """Collection of configured OpenClaw role-subagents."""

    schema_version: str
    default_backend: str
    roles: Dict[str, OpenClawRoleConfig]

    def roles_for_bot(self, bot_name: str) -> List[OpenClawRoleConfig]:
        return [
            role for role in self.roles.values()
            if role.enabled and role.bot == bot_name
        ]


def load_openclaw_role_registry(path: Path) -> OpenClawRoleRegistry:
    """Load and validate the OpenClaw role registry."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    roles_raw = raw.get("roles", {}) or {}
    roles: Dict[str, OpenClawRoleConfig] = {}

    for role_id, payload in roles_raw.items():
        role = OpenClawRoleConfig(
            role_id=str(role_id),
            title=str(payload.get("title", role_id)),
            bot=str(payload.get("bot", "")),
            backend=str(payload.get("backend", raw.get("default_backend", "openclaw"))),
            prompt_path=str(payload.get("prompt_path", "")),
            output_dir=str(payload.get("output_dir", "reports/openclaw_research")),
            artifact_prefix=str(payload.get("artifact_prefix", role_id)),
            every_n_seeds=max(int(payload.get("every_n_seeds", 1)), 1),
            enabled=bool(payload.get("enabled", True)),
            telegram_updates=bool(payload.get("telegram_updates", False)),
            paper_only=bool(payload.get("paper_only", True)),
        )
        roles[role.role_id] = role

    return OpenClawRoleRegistry(
        schema_version=str(raw.get("schema_version", "openclaw_role_registry.v1")),
        default_backend=str(raw.get("default_backend", "openclaw")),
        roles=roles,
    )
