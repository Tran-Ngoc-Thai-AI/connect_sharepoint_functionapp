from __future__ import annotations

from importlib.metadata import metadata
import logging
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from azure.identity import DefaultAzureCredential
import psycopg
from tomlkit import document

from .config import load_settings
from .metrics import SyncMetrics
from .sharepoint_client import SharePointRestClient
from .storage import StorageGateway

from .metadata_sync import build_document, decode_sharepoint_metadata
from sharepoint_delta import postgres_sync

PHASE = "phase1"
FUNCTION_NAME = "sharepoint_change_detection_timer"


def run_change_detection() -> None:
    run_id = str(uuid4())
    run_context = {"run_id": run_id, "phase": PHASE, "function_name": FUNCTION_NAME}
    metrics = SyncMetrics()
    logging.info("starting sharepoint change detection", extra={"custom_dimensions": run_context})

    try:
        settings = load_settings()

        conn = psycopg.connect(  ## type: add new connection
            host=settings.host,
            port=settings.port,
            dbname=settings.dbname,
            user=settings.user,
            password=settings.password,
        )

        logging.info("PostgreSQL connected successfully") ## log connection success

        credential = DefaultAzureCredential()
        storage = StorageGateway(settings, credential, run_context, lambda: metrics.inc("retry_count"))
        sharepoint = SharePointRestClient(credential, settings, run_context, lambda: metrics.inc("retry_count"))

        logging.info("building current snapshot from sharepoint", extra={"custom_dimensions": run_context})
        current_snapshot = _timed(
            lambda: _build_current_snapshot(sharepoint, metrics),
            "sharepoint_api_latency_ms",
            run_context,
        )
        saved_snapshot = storage.load_snapshot_state()

        all_changes: list[tuple[str, dict[str, Any]]] = []
        if not saved_snapshot:
            logging.info("resync mode: state file not found", extra={"custom_dimensions": run_context})
            # 1. Find orphaned blobs to delete
            orphaned = _find_orphaned_blobs(storage, current_snapshot, run_context)
            all_changes.extend(orphaned)
            # 2. Treat all files in SharePoint as 'created'
            for item in current_snapshot.values():
                all_changes.append(("created", item))
            logging.info(f"resync mode: found {len(orphaned)} orphans and {len(current_snapshot)} created files", extra={"custom_dimensions": run_context})
        else:
            logging.info("delta mode: detecting changes from saved state", extra={"custom_dimensions": run_context})
            all_changes = _detect_changes(saved_snapshot, current_snapshot)

        logging.info(f"found {len(all_changes)} total changes to process", extra={"custom_dimensions": run_context})
        for event_type, item in all_changes:
            metrics.inc(f"{event_type}_count")
            _process_change(item, event_type, run_id, sharepoint, storage, conn, metrics, run_context) ## process change add conn as parameter

        storage.save_snapshot_state(_state_payload(current_snapshot))

    except Exception:
        logging.exception("a critical error occurred in sharepoint change detection", extra={"custom_dimensions": run_context})
        # Re-raise the exception to make the function fail explicitly
        raise
    finally:
        if conn: ## close connection if it exists and is not None
            conn.close()
        logging.info(
            "finished sharepoint change detection",
            extra={"custom_dimensions": {**run_context, **metrics.snapshot()}},
        )
  


def _build_current_snapshot(sharepoint: SharePointRestClient, metrics: SyncMetrics) -> dict[str, dict[str, Any]]:
    snapshot = {}
    for item in sharepoint.iter_files():
        metrics.inc("files_scanned")
        snapshot[item["file_id"]] = item
    return snapshot


def _detect_changes(
    saved_snapshot: dict[str, Any],
    current_snapshot: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    changes: list[tuple[str, dict[str, Any]]] = []

    for file_id, current in current_snapshot.items():
        previous = saved_snapshot.get(file_id)
        if previous is None:
            changes.append(("created", current))
        elif previous.get("etag") != current.get("etag") or previous.get("last_modified") != current.get("last_modified"):
            changes.append(("updated", current))

    for file_id, previous in saved_snapshot.items():
        if file_id not in current_snapshot:
            deleted = {
                "file_id": file_id,
                "id": file_id,
                "file_name": previous.get("file_name", file_id),
                "etag": previous.get("etag"),
                "last_modified": previous.get("last_modified"),
                "file_size_bytes": previous.get("file_size_bytes"),
                "document_metadata": previous.get("document_metadata", {}),
                "web_url": previous.get("sharepoint_url"),
                "server_relative_url": previous.get("server_relative_url"),
            }
            changes.append(("deleted", deleted))

    return changes


def _find_orphaned_blobs(
    storage: StorageGateway,
    current_snapshot: dict[str, dict[str, Any]],
    log_context: dict[str, str],
) -> list[tuple[str, dict[str, Any]]]:
    logging.info("listing all metadata files to find orphans", extra={"custom_dimensions": log_context})
    blob_file_ids = set(storage.list_metadata_file_ids())
    sharepoint_file_ids = set(current_snapshot.keys())

    orphaned_ids = blob_file_ids - sharepoint_file_ids
    logging.info(f"found {len(orphaned_ids)} orphaned blobs to delete", extra={"custom_dimensions": log_context})

    changes: list[tuple[str, dict[str, Any]]] = []
    for file_id in orphaned_ids:
        # We need to load the metadata to get the file_name for deletion
        metadata = storage.load_metadata(file_id)
        if metadata:
            deleted_item = {
                "file_id": file_id,
                "id": file_id,
                "file_name": metadata.get("file_name", file_id),
                "etag": metadata.get("etag"),
                "last_modified": metadata.get("last_modified"),
                "file_size_bytes": metadata.get("file_size_bytes"),
                "document_metadata": metadata.get("document_metadata", {}),
                "web_url": metadata.get("sharepoint_url"),
                "server_relative_url": metadata.get("server_relative_url"),
            }
            changes.append(("deleted", deleted_item))
    return changes


def _process_change(
    item: dict[str, Any],
    event_type: str,
    run_id: str,
    sharepoint: SharePointRestClient,
    storage: StorageGateway,
    conn: psycopg.Connection,  ## postgresql connection
    metrics: SyncMetrics,
    run_context: dict[str, str],
) -> None:
    file_id = item["file_id"]
    log_context = {**run_context, "file_id": file_id, "event_type": event_type}

    if event_type != "deleted" and _is_duplicate_metadata(storage, item):
        logging.info("skipping duplicate file etag", extra={"custom_dimensions": log_context})
        return

    started = time.perf_counter()

    try:
        previous_metadata = storage.load_metadata(file_id) if event_type == "deleted" else None
        metadata, queue_message = _build_payloads(item, event_type, run_id)

        if event_type != "deleted":
            content = _timed(
                lambda: sharepoint.download_file(item),
                "download_latency_ms",
                log_context,
            )
            blob_path = _timed(
                lambda: storage.upload_raw_document(
                    file_id,
                    metadata["file_name"],
                    content,
                    _build_blob_metadata(item, metadata, event_type, run_id),
                ),
                "blob_upload_latency_ms",
                log_context,
            )
            metadata["blob_path"] = blob_path

            document = build_document(metadata) ## build document from metadata

            logging.info( ## log document summary
                "PostgreSQL document summary: "
                "blob=%s, metadata_fields=%d",
                document["blob_name"],
                len(document["metadata"])
            )

            logging.info("===== BEFORE POSTGRES SYNC =====") ## log before postgres sync
            logging.info("blob=%s", document["blob_name"])
            logging.info("metadata=%s", document["metadata"])

            postgres_sync.sync(conn, document) ## sync document to postgres

            queue_message["blob_path"] = blob_path
        else:
                blob_path = None
                deleted_blob_paths = []
                if previous_metadata:
                    blob_path = previous_metadata.get("blob_path")
                    deleted_blob_paths = _timed(
                        lambda: storage.delete_raw_document(file_id, metadata["file_name"], blob_path),
                        "blob_delete_latency_ms",
                        log_context,
                    )
                else:
                    logging.warning(
                        f"could not find metadata for deleted file_id {file_id}, skipping blob deletion",
                        extra={"custom_dimensions": log_context},
                    )

                metadata["blob_path"] = blob_path
                metadata["deleted_blob_paths"] = deleted_blob_paths

                if blob_path: ## if blob_path is not None
                    postgres_sync.delete(conn, blob_path) ## delete document from postgres

                queue_message["blob_path"] = blob_path

        metadata["blob_path"] = blob_path
        queue_message["blob_path"] = blob_path

        storage.upload_json(storage.settings.metadata_container, f"{file_id}.json", metadata)
        _timed(lambda: storage.send_queue_message(queue_message), "queue_publish_latency_ms", log_context)

        logging.info(
            "processed sharepoint change in %.2fms",
            (time.perf_counter() - started) * 1000,
            extra={"custom_dimensions": log_context},
        )
    except Exception as exc:
        if event_type != "deleted":
            metrics.inc("failed_downloads")
        _write_deadletter(storage, item, event_type, run_id, str(exc), metrics, log_context)
        logging.exception("failed to process sharepoint change", extra={"custom_dimensions": log_context})


def _is_duplicate_metadata(storage: StorageGateway, item: dict[str, Any]) -> bool:
    previous = storage.load_metadata(item["file_id"])
    return bool(previous and previous.get("etag") == item.get("etag"))


def _build_payloads(item: dict[str, Any], event_type: str, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    file_id = item["file_id"]
    file_name = item.get("file_name", file_id)
    etag = item.get("etag")
    sharepoint_url = item.get("web_url")
    last_modified = item.get("last_modified")
    server_relative_url = item.get("server_relative_url")
    file_size_bytes = item.get("file_size_bytes")
    # Trong hàm _build_payloads:
    document_metadata = _document_metadata(item)
    # Decode URL-encoded metadata cho PostgreSQL và AI Search
    decoded_metadata = decode_sharepoint_metadata(document_metadata)

    metadata = {
        "file_id": file_id,
        "file_name": file_name,
        "etag": etag,
        "sharepoint_url": sharepoint_url,
        "server_relative_url": server_relative_url,
        "file_size_bytes": file_size_bytes,
        "document_metadata": document_metadata,  # Giữ nguyên cho blob / storage metadata
        "document_metadata_decoded": decoded_metadata,  # Dùng cho AI Search / consumers cần text hiển thị
        "last_modified": last_modified,
        "status": event_type,
    }
    message = {
        "event_type": event_type,
        "file_id": file_id,
        "file_name": file_name,
        "blob_path": None,
        "etag": etag,
        "sharepoint_url": sharepoint_url,
        "server_relative_url": server_relative_url,
        "file_size_bytes": file_size_bytes,
        "document_metadata": decoded_metadata,  # Sử dụng giá trị đã decode cho queue
        "last_modified": last_modified,
        "run_id": run_id,
        "timestamp": now,
    }
    return metadata, message


def _build_blob_metadata(
    item: dict[str, Any],
    metadata: dict[str, Any],
    event_type: str,
    run_id: str,
) -> dict[str, str]:
    blob_metadata = {
        "source": "sharepoint",
        "sync_status": event_type,
        "sync_run_id": run_id,
        "file_id": metadata.get("file_id"),
        "file_name": metadata.get("file_name"),
        "etag": metadata.get("etag"),
        "sharepoint_url": metadata.get("sharepoint_url"),
        "server_relative_url": metadata.get("server_relative_url"),
        "last_modified": metadata.get("last_modified"),
        "file_size_bytes": metadata.get("file_size_bytes"),
        "sharepoint_item_id": item.get("id"),
    }
    blob_metadata.update(
        {
            key: value
            for key, value in metadata.get("document_metadata", {}).items()
            if value is not None
        }
    )
    return {key: _blob_metadata_value(value) for key, value in blob_metadata.items() if value is not None}


def _document_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("document_metadata")
    if not isinstance(metadata, dict):
        return {}
    return {key: value for key, value in metadata.items() if value is not None}


def _blob_metadata_value(value: Any) -> str:
    return quote(str(value), safe="/:.-_~")[:1024]


def _state_payload(snapshot: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        file_id: {
            "file_name": item.get("file_name"),
            "etag": item.get("etag"),
            "last_modified": item.get("last_modified"),
            "sharepoint_url": item.get("web_url"),
            "server_relative_url": item.get("server_relative_url"),
            "file_size_bytes": item.get("file_size_bytes"),
            "document_metadata": _document_metadata(item),
        }
        for file_id, item in snapshot.items()
    }


def _timed(operation, metric_name: str, log_context: dict[str, str]):
    started = time.perf_counter()
    result = operation()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logging.info(metric_name, extra={"custom_dimensions": {**log_context, metric_name: elapsed_ms}})
    return result


def _write_deadletter(
    storage: StorageGateway,
    item: dict[str, Any],
    event_type: str,
    run_id: str,
    error: str,
    metrics: SyncMetrics,
    log_context: dict[str, str],
) -> None:
    metrics.inc("deadletter_count")
    storage.deadletter(
        item.get("file_id", "unknown"),
        {
            "run_id": run_id,
            "event_type": event_type,
            "error": error,
            "item": item,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
    )
    logging.warning("deadletter written", extra={"custom_dimensions": log_context})
