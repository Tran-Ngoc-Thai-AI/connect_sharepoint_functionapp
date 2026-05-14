import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    sharepoint_site_url: str
    sharepoint_library_title: str
    storage_connection_string: str | None
    storage_account_url: str | None
    queue_account_url: str | None
    queue_name: str
    raw_container: str
    metadata_container: str
    state_container: str
    deadletter_container: str
    snapshot_state_blob: str
    sharepoint_timeout_seconds: int
    sharepoint_page_size: int


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _required(name: str) -> str:
    value = _optional(name)
    if not value:
        raise ValueError(f"Missing required app setting: {name}")
    return value


def load_settings() -> Settings:
    return Settings(
        sharepoint_site_url=_required("SHAREPOINT_SITE_URL").rstrip("/"),
        sharepoint_library_title=_required("SHAREPOINT_LIBRARY_TITLE"),
        storage_connection_string=_optional("INGESTION_STORAGE_CONNECTION_STRING"),
        storage_account_url=_optional("STORAGE_ACCOUNT_URL"),
        queue_account_url=_optional("QUEUE_ACCOUNT_URL"),
        queue_name=os.getenv("DOCUMENT_QUEUE_NAME", "document-processing-queue"),
        raw_container=os.getenv("RAW_DOCUMENTS_CONTAINER", "raw-documents"),
        metadata_container=os.getenv("METADATA_CONTAINER", "metadata"),
        state_container=os.getenv("STATE_CONTAINER", "state"),
        deadletter_container=os.getenv("DEADLETTER_CONTAINER", "deadletter"),
        snapshot_state_blob=os.getenv("SNAPSHOT_STATE_BLOB", "document_library_state.json"),
        sharepoint_timeout_seconds=int(os.getenv("SHAREPOINT_TIMEOUT_SECONDS", "30")),
        sharepoint_page_size=int(os.getenv("SHAREPOINT_PAGE_SIZE", "5000")),
    )
