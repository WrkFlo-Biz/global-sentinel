#!/usr/bin/env python3
"""Attach a bounded research score to a research snapshot.

The research score is added as an overlay — it never directly
drives execution, only provides a secondary research signal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def attach_research_score(
    snapshot: Dict[str, Any],
    research_score: Dict[str, Any],
    incident_mode: str = "NORMAL",
) -> Dict[str, Any]:
    out = dict(snapshot)

    out.setdefault("research_overlays", {})
    out["research_overlays"]["quantum_research_score"] = {
        "research_score": research_score.get("research_score"),
        "recommended_influence": research_score.get("recommended_influence"),
        "guardrails": research_score.get("guardrails"),
        "request_id": research_score.get("request_id"),
        "package_id": research_score.get("package_id"),
    }

    out.setdefault("runtime_flags", {})
    out["runtime_flags"]["quantum_research_attached"] = True
    out["runtime_flags"]["quantum_direct_execution_forbidden"] = True

    # Build artifact manifest for lineage tracking
    try:
        from src.lineage.artifact_manifest_builder import ArtifactManifestBuilder
        parent_ids = []
        if snapshot.get("_artifact_id"):
            parent_ids.append(snapshot["_artifact_id"])
        if research_score.get("_artifact_id"):
            parent_ids.append(research_score["_artifact_id"])

        # Add config fingerprint for replayability
        config_fp = {}
        try:
            from src.core.config_fingerprint import compute_config_fingerprint
            config_fp = compute_config_fingerprint()
        except Exception:
            pass

        builder = (ArtifactManifestBuilder()
            .set_type("research_score_attachment")
            .set_parents(parent_ids)
            .set_source_packets([
                research_score.get("request_id", ""),
                research_score.get("package_id", ""),
            ])
            .set_incident_mode(incident_mode)
            .set_code_version_from_git()
            .set_content_hash(out.get("research_overlays", {})))

        if config_fp:
            builder.set_runtime_flags({
                "config_fingerprint": config_fp.get("combined_fingerprint", ""),
                "config_versions": config_fp.get("configs", {}),
            })

        # Add time-window state if available in snapshot
        tw = snapshot.get("time_window", {})
        if isinstance(tw, dict) and tw.get("window_name"):
            builder.set_time_window(tw["window_name"])

        manifest = builder.build()
        out["_artifact_manifest"] = manifest.to_dict()
        out["_artifact_id"] = manifest.artifact_id
    except Exception:
        pass  # Graceful degradation if lineage module unavailable

    return out


def parse_args():
    p = argparse.ArgumentParser(description="Attach research score to snapshot")
    p.add_argument("--snapshot-json", required=True)
    p.add_argument("--research-score-json", required=True)
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def main():
    args = parse_args()
    snapshot = load_json(Path(args.snapshot_json))
    research_score = load_json(Path(args.research_score_json))
    out = attach_research_score(snapshot, research_score)
    out_path = Path(args.output_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
