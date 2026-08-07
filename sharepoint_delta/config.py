import ast
import json
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
    sharepoint_document_metadata_fields: dict[str, str]
    host: str ## Database host
    port: int ## Database port
    dbname: str ## Database name
    user: str ## Database user
    password: str ## Database password


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _required(name: str) -> str:
    value = _optional(name)
    if not value:
        raise ValueError(f"Missing required app setting: {name}")
    return value


def _json_mapping(name: str) -> dict[str, str]:
    value = _optional(name)
    if not value:
        return {}

    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        try:
            payload = ast.literal_eval(value)
        except (SyntaxError, ValueError) as literal_exc:
            raise ValueError(
                f"{name} must be a JSON object, for example "
                '{"document_number":"SharePointInternalName"}'
            ) from literal_exc

    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")

    return {str(key): str(field).strip() for key, field in payload.items() if field}


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
        sharepoint_document_metadata_fields=_json_mapping("SHAREPOINT_DOCUMENT_METADATA_FIELDS"),
        host=os.getenv("host", "dbuatnexssi.postgres.database.azure.com"), ## Database host
        port=int(os.getenv("port", "5432")), ## Database port
        dbname=os.getenv("dbname", "postgres"), ## Database name
        user=os.getenv("user", "thaitn36514"), ## Database user 
        password=os.getenv("password", "Sacombank@123"), ## Database password
    )
