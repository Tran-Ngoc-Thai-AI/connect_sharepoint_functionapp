from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterator
from urllib.parse import quote, urlparse

import requests
from azure.core.credentials import TokenCredential

from .config import Settings
from .retry import retry_call


ALLOWED_EXTENSIONS = {".docx", ".pdf", ".xlsx", ".pptx"}
SKIPPED_SUFFIXES = {".tmp", ".aspx"}
SKIPPED_PATH_PARTS = {".forms", "forms"}


class SharePointRestClient:
    def __init__(self, credential: TokenCredential, settings: Settings, run_context: dict, on_retry) -> None:
        self.credential = credential
        self.settings = settings
        self.run_context = run_context
        self.on_retry = on_retry
        parsed = urlparse(settings.sharepoint_site_url)
        self.resource = f"{parsed.scheme}://{parsed.netloc}"
        self.scope = f"{self.resource}/.default"

    def iter_files(self) -> Iterator[dict]:
        select = ",".join(
            [
                "Id",
                "UniqueId",
                "FileLeafRef",
                "FileRef",
                "Modified",
                "FSObjType",
                "File/Name",
                "File/ServerRelativeUrl",
                "File/TimeLastModified",
                "File/ETag",
                "File/Length",
            ]
        )
        library_title = _escape_odata_string(self.settings.sharepoint_library_title)
        url = (
            f"{self.settings.sharepoint_site_url}/_api/web/lists/getbytitle('{library_title}')/items"
            f"?$select={select}&$expand=File&$filter=FSObjType eq 0"
            f"&$top={self.settings.sharepoint_page_size}"
        )

        while url:
            payload = self._get_json(url)
            items = _extract_items(payload)
            for item in items:
                normalized = _normalize_file_item(item, self.settings.sharepoint_site_url)
                if normalized and _is_supported_file(normalized["file_name"], normalized["server_relative_url"]):
                    yield normalized
            url = _next_link(payload)

    def download_file(self, item: dict) -> bytes:
        server_relative_url = quote(item["server_relative_url"], safe="/")
        url = (
            f"{self.settings.sharepoint_site_url}/_api/web/"
            f"GetFileByServerRelativeUrl('{server_relative_url}')/$value"
        )

        def operation() -> bytes:
            response = requests.get(
                url,
                headers=self._headers(accept=None),
                timeout=self.settings.sharepoint_timeout_seconds,
            )
            if response.status_code >= 400:
                self._log_sharepoint_error(response, url)
            response.raise_for_status()
            return response.content

        return retry_call(
            operation,
            operation_name="sharepoint_download_file",
            max_attempts=5,
            delays=[1, 2, 4, 8],
            log_context=self.run_context,
            retry_metric=self.on_retry,
        )

    def probe_access(self) -> dict:
        token = self._access_token()
        probes = [
            ("web", f"{self.settings.sharepoint_site_url}/_api/web?$select=Title,Url"),
            (
                "library",
                f"{self.settings.sharepoint_site_url}/_api/web/lists/getbytitle("
                f"'{_escape_odata_string(self.settings.sharepoint_library_title)}')?$select=Id,Title,ItemCount",
            ),
            (
                "items",
                f"{self.settings.sharepoint_site_url}/_api/web/lists/getbytitle("
                f"'{_escape_odata_string(self.settings.sharepoint_library_title)}')/items"
                "?$select=Id,FileLeafRef,FileRef,Modified,FSObjType&$filter=FSObjType eq 0&$top=1",
            ),
        ]

        results = []
        headers = self._headers(token=token)
        for name, url in probes:
            response = requests.get(url, headers=headers, timeout=self.settings.sharepoint_timeout_seconds)
            if response.status_code >= 400:
                self._log_sharepoint_error(response, url)
            results.append(
                {
                    "name": name,
                    "status_code": response.status_code,
                    "ok": response.ok,
                    "url": url.split("?")[0],
                    "response": _safe_response(response),
                }
            )

        return {
            "ok": all(result["ok"] for result in results),
            "token_claims": _safe_token_claims(token),
            "results": results,
        }

    def _get_json(self, url: str) -> dict:
        def operation() -> dict:
            response = requests.get(
                url,
                headers=self._headers(),
                timeout=self.settings.sharepoint_timeout_seconds,
            )
            if response.status_code >= 400:
                self._log_sharepoint_error(response, url)
            response.raise_for_status()
            return response.json()

        return retry_call(
            operation,
            operation_name="sharepoint_rest_api",
            max_attempts=5,
            delays=[1, 2, 4, 8],
            log_context=self.run_context,
            retry_metric=self.on_retry,
        )

    def _headers(self, token: str | None = None, accept: str | None = "application/json;odata=nometadata") -> dict:
        headers = {"Authorization": f"Bearer {token or self._access_token()}"}
        if accept:
            headers["Accept"] = accept
        return headers

    def _access_token(self) -> str:
        return self.credential.get_token(self.scope).token

    def _log_sharepoint_error(self, response: requests.Response, url: str) -> None:
        logging.error(
            "sharepoint rest request failed",
            extra={
                "custom_dimensions": {
                    **self.run_context,
                    "sharepoint_status_code": response.status_code,
                    "sharepoint_url": url.split("?")[0],
                    "sharepoint_response": response.text[:1000],
                }
            },
        )


def _extract_items(payload: dict) -> list[dict]:
    if isinstance(payload.get("value"), list):
        return payload["value"]
    data = payload.get("d", {})
    results = data.get("results")
    return results if isinstance(results, list) else []


def _next_link(payload: dict) -> str | None:
    if payload.get("@odata.nextLink"):
        return payload["@odata.nextLink"]
    return payload.get("d", {}).get("__next")


def _normalize_file_item(item: dict, site_url: str) -> dict | None:
    file_obj = item.get("File") or {}
    file_name = item.get("FileLeafRef") or file_obj.get("Name")
    server_relative_url = item.get("FileRef") or file_obj.get("ServerRelativeUrl")
    if not file_name or not server_relative_url:
        return None

    file_id = str(item.get("UniqueId") or item.get("Id") or server_relative_url)
    etag = file_obj.get("ETag") or item.get("OData__UIVersionString") or item.get("Modified")
    last_modified = file_obj.get("TimeLastModified") or item.get("Modified")

    return {
        "id": file_id,
        "file_id": file_id,
        "name": file_name,
        "file_name": file_name,
        "etag": etag,
        "last_modified": last_modified,
        "server_relative_url": server_relative_url,
        "web_url": f"{site_url.rstrip('/')}/{server_relative_url.lstrip('/')}",
    }


def _is_supported_file(file_name: str, server_relative_url: str) -> bool:
    lower_name = file_name.lower()
    lower_path = server_relative_url.lower()
    if any(lower_name.endswith(suffix) for suffix in SKIPPED_SUFFIXES):
        return False
    if any(part in lower_path.split("/") for part in SKIPPED_PATH_PARTS):
        return False
    return any(lower_name.endswith(extension) for extension in ALLOWED_EXTENSIONS)


def _escape_odata_string(value: str) -> str:
    return value.replace("'", "''")


def _safe_response(response: requests.Response) -> dict | str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:1000]

    if response.ok:
        return {
            "id": payload.get("Id") or payload.get("id"),
            "title": payload.get("Title") or payload.get("title"),
            "url": payload.get("Url") or payload.get("url"),
            "item_count": payload.get("ItemCount"),
            "value_count": len(payload.get("value", [])) if isinstance(payload.get("value"), list) else None,
            "has_next_link": bool(payload.get("@odata.nextLink") or payload.get("d", {}).get("__next")),
        }
    return payload


def _safe_token_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except Exception:
        return {"decoded": False}

    return {
        "decoded": True,
        "aud": claims.get("aud"),
        "appid": claims.get("appid"),
        "azp": claims.get("azp"),
        "oid": claims.get("oid"),
        "tid": claims.get("tid"),
        "roles": claims.get("roles", []),
        "scp": claims.get("scp"),
    }
