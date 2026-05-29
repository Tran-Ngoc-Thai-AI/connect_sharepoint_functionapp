from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from azure.identity import DefaultAzureCredential

from .config import load_settings
from .metrics import SyncMetrics
from .sharepoint_client import SharePointRestClient
from .storage import StorageGateway


PHASE = "phase1"
FUNCTION_NAME = "sharepoint_change_detection_timer"


def run_change_detection() -> None:
    run_id = str(uuid4())
    run_context = {"run_id": run_id, "phase": PHASE, "function_name": FUNCTION_NAME}
    metrics = SyncMetrics()

    logging.info("starting sharepoint change detection", extra={"custom_dimensions": run_context})

    settings = load_settings()
    credential = DefaultAzureCredential()
    storage = StorageGateway(settings, credential, run_context, lambda: metrics.inc("retry_count"))
    sharepoint = SharePointRestClient(credential, settings, run_context, lambda: metrics.inc("retry_count"))

    current_snapshot = _timed(
        lambda: _build_current_snapshot(sharepoint, metrics),
        "sharepoint_api_latency_ms",
        run_context,
    )
    saved_snapshot = storage.load_snapshot_state()
    changes = _detect_changes(saved_snapshot, current_snapshot)

    for event_type, item in changes:
        metrics.inc(f"{event_type}_count")
        _process_change(item, event_type, run_id, sharepoint, storage, metrics, run_context)

    storage.save_snapshot_state(_state_payload(current_snapshot))

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
                "web_url": previous.get("sharepoint_url"),
                "server_relative_url": previous.get("server_relative_url"),
            }
            changes.append(("deleted", deleted))

    return changes


def _process_change(
    item: dict[str, Any],
    event_type: str,
    run_id: str,
    sharepoint: SharePointRestClient,
    storage: StorageGateway,
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
                lambda: storage.upload_raw_document(file_id, metadata["file_name"], content),
                "blob_upload_latency_ms",
                log_context,
            )
            metadata["blob_path"] = blob_path
            queue_message["blob_path"] = blob_path
        else:
            blob_path = previous_metadata.get("blob_path") if previous_metadata else None
            deleted_blob_paths = _timed(
                lambda: storage.delete_raw_document(file_id, metadata["file_name"], blob_path),
                "blob_delete_latency_ms",
                log_context,
            )
            metadata["blob_path"] = blob_path
            metadata["deleted_blob_paths"] = deleted_blob_paths
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

    metadata = {
        "file_id": file_id,
        "file_name": file_name,
        "etag": etag,
        "sharepoint_url": sharepoint_url,
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
        "last_modified": last_modified,
        "run_id": run_id,
        "timestamp": now,
    }
    return metadata, message


def _state_payload(snapshot: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        file_id: {
            "file_name": item.get("file_name"),
            "etag": item.get("etag"),
            "last_modified": item.get("last_modified"),
            "sharepoint_url": item.get("web_url"),
            "server_relative_url": item.get("server_relative_url"),
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
