from urllib.parse import unquote


def decode_sharepoint_metadata(metadata: dict) -> dict:
    """Decode URL-encoded SharePoint metadata."""

    if not metadata:
        return {}

    return {
        k: unquote(v) if isinstance(v, str) else v
        for k, v in metadata.items()
    }


def build_document(metadata: dict) -> dict:
    """
    Chuẩn hóa object dùng cho PostgreSQL.
    Input là metadata trong pipeline.py.
    """

    document_metadata = metadata.get("document_metadata", {})
    document_metadata_types = metadata.get("document_metadata_types", {})

    return {
        "blob_name": metadata["blob_path"],
        "file_size_bytes": metadata.get("file_size_bytes"),
        "last_modified": metadata.get("last_modified"),
        "metadata": decode_sharepoint_metadata(document_metadata),
        "metadata_types": document_metadata_types if isinstance(document_metadata_types, dict) else {},
    }