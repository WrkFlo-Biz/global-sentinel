#!/usr/bin/env python3
"""Append role-based advisory tickets to the recommendation queue."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OpenClawRecommendationQueueWriter:
    """Persist human-gated role advisories to the recommendation queue."""

    def __init__(
        self,
        repo_root: Path,
        queue_relpath: str = "logs/self_improvement/recommendation_queue.jsonl",
    ):
        self.repo_root = repo_root
        self.queue_path = repo_root / queue_relpath
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    def append_role_advisory(
        self,
        *,
        role_artifact: Dict[str, Any],
        artifact_path: Path,
    ) -> Dict[str, Any]:
        entry = {
            "timestamp_utc": _utc_now_iso(),
            "status": "proposed",
            "category": "role_advisory",
            "source": "openclaw_role_briefing",
            "role_id": role_artifact.get("role_id"),
            "title": f"{role_artifact.get('title', role_artifact.get('role_id', 'role'))} advisory",
            "summary": (role_artifact.get("actions") or ["No suggested actions"])[0],
            "constraints": {
                "manual_approval_required": True,
                "replay_required": True,
                "apply_post_close_only": True,
                "paper_only": bool((role_artifact.get("safety") or {}).get("paper_only", True)),
                "live_execution_forbidden": True,
            },
            "references": {
                "artifact_json": str(artifact_path),
                "inputs": role_artifact.get("inputs", {}),
            },
            "payload": {
                "status": role_artifact.get("status"),
                "observed_facts": role_artifact.get("observed_facts", [])[:5],
                "inferences": role_artifact.get("inferences", [])[:3],
                "actions": role_artifact.get("actions", [])[:5],
                "metrics": role_artifact.get("metrics", {}),
            },
        }
        with self.queue_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
