#!/usr/bin/env python3
"""Publish local research artifacts to Azure Blob and write a manifest.

Uploads all files from the local research directory to a Blob container
with SHA-256 integrity hashes in the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, Any

from src.utils.azure_blob_artifact_io import AzureBlobArtifactIO


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_args():
    p = argparse.ArgumentParser(description="Publish research artifacts to Azure Blob")
    p.add_argument("--container", required=True)
    p.add_argument("--prefix", required=True)
    p.add_argument("--local-dir", default="reports/research")
    p.add_argument("--manifest-out", default="reports/research/publish_manifest.json")
    return p.parse_args()


def main():
    args = parse_args()
    io = AzureBlobArtifactIO()
    local_dir = Path(args.local_dir)
    files = [p for p in sorted(local_dir.glob("*")) if p.is_file()]

    manifest: Dict[str, Any] = {
        "schema_version": "research_publish_manifest.v1",
        "container": args.container,
        "prefix": args.prefix,
        "files": [],
    }

    cc = io._container(args.container)

    for p in files:
        blob_name = f"{args.prefix.rstrip('/')}/{p.name}"
        data = p.read_bytes()
        cc.upload_blob(name=blob_name, data=data, overwrite=True)

        manifest["files"].append({
            "name": p.name,
            "blob_name": blob_name,
            "sha256": sha256_file(p),
            "size_bytes": p.stat().st_size,
        })

    manifest_out = Path(args.manifest_out)
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(manifest_out)


if __name__ == "__main__":
    main()
