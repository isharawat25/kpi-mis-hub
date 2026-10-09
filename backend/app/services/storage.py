"""Blob storage: Azure Blob Storage when configured, local filesystem otherwise."""
import uuid
from pathlib import Path

from ..config import get_settings


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, path: str, data: bytes, content_type: str | None = None) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path

    def get(self, path: str) -> bytes:
        return (self.root / path).read_bytes()


class AzureBlobStorage:
    def __init__(self, conn_str: str, container: str):
        from azure.storage.blob import BlobServiceClient
        self.client = BlobServiceClient.from_connection_string(conn_str)
        self.container = self.client.get_container_client(container)
        if not self.container.exists():
            self.container.create_container()

    def put(self, path: str, data: bytes, content_type: str | None = None) -> str:
        from azure.storage.blob import ContentSettings
        settings = ContentSettings(content_type=content_type) if content_type else None
        self.container.upload_blob(path, data, overwrite=True, content_settings=settings)
        return path

    def get(self, path: str) -> bytes:
        return self.container.download_blob(path).readall()


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        s = get_settings()
        if s.azure_storage_connection_string:
            _storage = AzureBlobStorage(s.azure_storage_connection_string, s.azure_container)
        else:
            _storage = LocalStorage(s.local_storage_dir)
    return _storage


def make_blob_path(folder: str, filename: str) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[-150:]
    return f"{folder}/{uuid.uuid4().hex}_{safe}"
