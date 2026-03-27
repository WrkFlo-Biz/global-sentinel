"""Thin wrapper around AzureBlobArtifactIO for loading request artifacts.

Falls back gracefully if Azure SDK is not installed.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.utils.azure_blob_artifact_io import AzureBlobArtifactIO


class BlobRequestLoader:
    def __init__(self, account_name: Optional[str] = None):
        self.io = AzureBlobArtifactIO(account_name=account_name)

    @property
    def available(self) -> bool:
        return self.io.available

    def load_request_json(self, container_name: str, blob_name: str) -> Dict[str, Any]:
        return self.io.download_json(container_name, blob_name)

    def save_json(self, container_name: str, blob_name: str, payload: Dict[str, Any]) -> str:
        return self.io.upload_json(container_name, blob_name, payload)
