from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from azure.identity import DefaultAzureCredential

from .config import Settings
from .retry import retry_call


class StorageGateway:
    def __init__(self, settings: Settings, credential: DefaultAzureCredential, run_context: dict, on_retry) -> None:
        from azure.storage.blob import BlobServiceClient
        from azure.storage.queue import QueueServiceClient

        self.settings = settings
        self.run_context = run_context
        self.on_retry = on_retry

        if settings.storage_connection_string:
            self.blobs = BlobServiceClient.from_connection_string(settings.storage_connection_string)
            self.queues = QueueServiceClient.from_connection_string(settings.storage_connection_string)
        else:
            if not settings.storage_account_url:
                raise ValueError("Set STORAGE_ACCOUNT_URL or INGESTION_STORAGE_CONNECTION_STRING")
            queue_url = settings.queue_account_url or settings.storage_account_url.replace(".blob.", ".queue.")
            self.blobs = BlobServiceClient(account_url=settings.storage_account_url, credential=credential)
            self.queues = QueueServiceClient(account_url=queue_url, credential=credential)

        self._ensure_resources()

    def load_snapshot_state(self) -> dict[str, Any]:
        return self._download_json(self.settings.state_container, self.settings.snapshot_state_blob) or {}

    def save_snapshot_state(self, snapshot: dict[str, Any]) -> None:
        self.upload_json(
            self.settings.state_container,
            self.settings.snapshot_state_blob,
            snapshot,
        )

    def load_metadata(self, file_id: str) -> dict[str, Any] | None:
        return self._download_json(self.settings.metadata_container, f"{file_id}.json")

    def list_metadata_file_ids(self) -> list[str]:
        container_client = self.blobs.get_container_client(self.settings.metadata_container)
        return [
            blob.name.removesuffix(".json")
            for blob in container_client.list_blobs()
            if blob.name.endswith(".json")
        ]

    def upload_raw_document(
        self,
        file_id: str,
        file_name: str,
        content: bytes,
        metadata: dict[str, str] | None = None,
    ) -> str:
        from azure.storage.blob import ContentSettings

        blob_name = f"{file_name}/{file_name}"

        def operation() -> None:
            self.blobs.get_blob_client(self.settings.raw_container, blob_name).upload_blob(
                content,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/octet-stream"),
                metadata=metadata,
            )

        retry_call(
            operation,
            operation_name="blob_upload_raw_document",
            max_attempts=3,
            delays=[1, 2, 4],
            log_context={**self.run_context, "file_id": file_id},
            retry_metric=self.on_retry,
        )
        return f"{self.settings.raw_container}/{blob_name}"

    def delete_raw_document(self, file_id: str, file_name: str, blob_path: str | None = None) -> list[str]:
        from azure.core.exceptions import ResourceNotFoundError

        candidates = self._raw_document_candidates(file_id, file_name, blob_path)

        def operation() -> None:
            deleted_count = 0
            for container, blob_name in candidates:
                try:
                    self.blobs.get_blob_client(container, blob_name).delete_blob()
                    deleted_count += 1
                except ResourceNotFoundError:
                    pass
            
            if deleted_count == 0:
                raise FileNotFoundError(f"Could not delete blob for file_id {file_id}. Tried candidates: {candidates}")

        retry_call(
            operation,
            operation_name="blob_delete_raw_document",
            max_attempts=3,
            delays=[1, 2, 4],
            log_context={**self.run_context, "file_id": file_id},
            retry_metric=self.on_retry,
        )
        return [f"{container}/{blob_name}" for container, blob_name in candidates]

    def upload_json(self, container: str, blob_name: str, payload: dict[str, Any]) -> None:
        from azure.storage.blob import ContentSettings

        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        def operation() -> None:
            self.blobs.get_blob_client(container, blob_name).upload_blob(
                body,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json"),
            )

        retry_call(
            operation,
            operation_name="blob_upload_json",
            max_attempts=3,
            delays=[1, 2, 4],
            log_context=self.run_context,
            retry_metric=self.on_retry,
        )

    def send_queue_message(self, message: dict[str, Any]) -> None:
        body = json.dumps(message, ensure_ascii=False)

        def operation() -> None:
            self.queues.get_queue_client(self.settings.queue_name).send_message(body)

        retry_call(
            operation,
            operation_name="queue_publish_document",
            max_attempts=3,
            delays=[1, 2, 4],
            log_context={**self.run_context, "file_id": message.get("file_id")},
            retry_metric=self.on_retry,
        )

    def deadletter(self, name: str, payload: dict[str, Any]) -> None:
        blob_name = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S.%fZ')}-{name}.json"
        self.upload_json(self.settings.deadletter_container, blob_name, payload)

    def _download_json(self, container: str, blob_name: str) -> dict[str, Any] | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            data = self.blobs.get_blob_client(container, blob_name).download_blob().readall()
        except ResourceNotFoundError:
            return None
        return json.loads(data.decode("utf-8"))

    def _raw_document_candidates(
        self,
        file_id: str,
        file_name: str,
        blob_path: str | None,
    ) -> list[tuple[str, str]]:
        candidates: list[tuple[str, str]] = []

        if blob_path:
            container, _, blob_name = blob_path.partition("/")
            if container and blob_name:
                candidates.append((container, blob_name))

        candidates.append((self.settings.raw_container, f"{file_name}/{file_name}"))
        candidates.append((self.settings.raw_container, f"{file_id}/{file_name}"))

        return list(dict.fromkeys(candidates))

    def _ensure_resources(self) -> None:
        from azure.core.exceptions import ResourceExistsError

        for container in [
            self.settings.raw_container,
            self.settings.metadata_container,
            self.settings.state_container,
            self.settings.deadletter_container,
        ]:
            try:
                self.blobs.create_container(container)
            except ResourceExistsError:
                pass

        try:
            self.queues.create_queue(self.settings.queue_name)
        except ResourceExistsError:
            pass