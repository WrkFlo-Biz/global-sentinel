"""Azure Blob-driven pipeline runner for research score flow.

Pulls request/snapshot/trade outcome artifacts from Azure Blob,
runs the research score pipeline locally, then pushes outputs back.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from src.utils.azure_blob_artifact_io import AzureBlobArtifactIO
from src.research.run_research_score_pipeline import main as run_local_pipeline_main


class AzureBlobPipelineRunner:

    def __init__(self, account_name: Optional[str] = None):
        self.io = AzureBlobArtifactIO(account_name=account_name)

    def download_inputs(
        self,
        *,
        request_container: str,
        request_blob: str,
        trade_container: str,
        trade_blob: str,
        snapshot_container: Optional[str],
        snapshot_blob: Optional[str],
        workdir: Path,
    ) -> Dict[str, Optional[Path]]:
        workdir.mkdir(parents=True, exist_ok=True)

        request_path = workdir / "request.json"
        request_path.write_text(
            json.dumps(self.io.download_json(request_container, request_blob), indent=2),
            encoding="utf-8",
        )

        trade_path = workdir / "trade_outcomes.json"
        trade_path.write_text(
            json.dumps(self.io.download_json(trade_container, trade_blob), indent=2),
            encoding="utf-8",
        )

        snapshot_path: Optional[Path] = None
        if snapshot_container and snapshot_blob:
            snapshot_path = workdir / "research_snapshot.json"
            snapshot_path.write_text(
                json.dumps(self.io.download_json(snapshot_container, snapshot_blob), indent=2),
                encoding="utf-8",
            )

        return {
            "request_json": request_path,
            "trade_outcomes_json": trade_path,
            "snapshot_json": snapshot_path,
        }

    def upload_outputs(
        self,
        *,
        output_container: str,
        local_dir: Path,
        prefix: str,
    ) -> Dict[str, str]:
        uploaded: Dict[str, str] = {}
        for p in sorted(local_dir.glob("*.json")):
            blob_name = f"{prefix.rstrip('/')}/{p.name}"
            self.io.upload_json(output_container, blob_name, json.loads(p.read_text(encoding="utf-8")))
            uploaded[p.name] = blob_name
        for p in sorted(local_dir.glob("*.md")):
            blob_name = f"{prefix.rstrip('/')}/{p.name}"
            cc = self.io._container(output_container)
            cc.upload_blob(name=blob_name, data=p.read_text(encoding="utf-8").encode("utf-8"), overwrite=True)
            uploaded[p.name] = blob_name
        return uploaded


def parse_args():
    p = argparse.ArgumentParser(description="Blob-driven research pipeline runner")
    p.add_argument("--request-container", required=True)
    p.add_argument("--request-blob", required=True)
    p.add_argument("--trade-container", required=True)
    p.add_argument("--trade-blob", required=True)
    p.add_argument("--snapshot-container", required=False, default=None)
    p.add_argument("--snapshot-blob", required=False, default=None)
    p.add_argument("--output-container", required=True)
    p.add_argument("--output-prefix", required=True)
    p.add_argument("--workdir", default="artifacts/blob_pipeline")
    return p.parse_args()


def main():
    args = parse_args()
    runner = AzureBlobPipelineRunner()
    workdir = Path(args.workdir)
    paths = runner.download_inputs(
        request_container=args.request_container,
        request_blob=args.request_blob,
        trade_container=args.trade_container,
        trade_blob=args.trade_blob,
        snapshot_container=args.snapshot_container,
        snapshot_blob=args.snapshot_blob,
        workdir=workdir,
    )

    reports_dir = Path("reports/research")
    reports_dir.mkdir(parents=True, exist_ok=True)

    argv = [
        "run_research_score_pipeline.py",
        "--request-json", str(paths["request_json"]),
        "--trade-outcomes-json", str(paths["trade_outcomes_json"]),
        "--quantum-artifact-dir", "artifacts/quantum",
        "--classical-artifact-dir", "artifacts/classical",
        "--evaluation-out", "reports/research/evaluation_latest.json",
        "--research-score-out", "reports/research/research_score_latest.json",
    ]
    if paths["snapshot_json"] is not None:
        argv.extend([
            "--snapshot-json", str(paths["snapshot_json"]),
            "--snapshot-out", "reports/research/snapshot_with_research_score.json",
        ])

    prev = sys.argv
    try:
        sys.argv = argv
        run_local_pipeline_main()
    finally:
        sys.argv = prev

    uploaded = runner.upload_outputs(
        output_container=args.output_container,
        local_dir=reports_dir,
        prefix=args.output_prefix,
    )
    print(json.dumps({"uploaded": uploaded}, indent=2))


if __name__ == "__main__":
    main()
