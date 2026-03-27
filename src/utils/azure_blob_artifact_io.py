"""Azure Blob artifact helper using DefaultAzureCredential.

Requires azure-identity and azure-storage-blob packages.
Falls back gracefully if Azure SDK is not installed.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


class AzureBlobArtifactIO:

    def __init__(self, account_name: Optional[str] = None):
        self.account_name = account_name or os.environ.get("STORAGE_ACCOUNT_NAME", "")
        self.account_url = f"https://{self.account_name}.blob.core.windows.net"

        try:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
            self.credential = DefaultAzureCredential()
            self.client = BlobServiceClient(account_url=self.account_url, credential=self.credential)
            self._available = True
        except ImportError:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def _container(self, container_name: str):
        if not self._available:
            raise RuntimeError("Azure Blob SDK not installed")
        return self.client.get_container_client(container_name)

    def upload_json(self, container_name: str, blob_name: str, payload: Dict[str, Any]) -> str:
        data = json.dumps(payload, indent=2).encode("utf-8")
        cc = self._container(container_name)
        cc.upload_blob(name=blob_name, data=data, overwrite=True)
        return blob_name

    def download_json(self, container_name: str, blob_name: str) -> Dict[str, Any]:
        cc = self._container(container_name)
        blob = cc.download_blob(blob_name)
        return json.loads(blob.readall().decode("utf-8"))

    def list_blobs(self, container_name: str, prefix: str = "") -> List[str]:
        cc = self._container(container_name)
        return [b.name for b in cc.list_blobs(name_starts_with=prefix)]
