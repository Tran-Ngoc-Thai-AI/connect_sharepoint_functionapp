from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

import requests


T = TypeVar("T")


TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def retry_call(
    operation: Callable[[], T],
    *,
    operation_name: str,
    max_attempts: int,
    delays: list[float],
    log_context: dict,
    retry_metric: Callable[[], None] | None = None,
) -> T:
    attempt = 1
    while True:
        try:
            return operation()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code not in TRANSIENT_STATUS_CODES or attempt >= max_attempts:
                raise
            delay = _retry_after_seconds(exc.response) or delays[min(attempt - 1, len(delays) - 1)]
            _log_retry(operation_name, attempt, delay, log_context, retry_metric, exc)
            time.sleep(delay)
            attempt += 1
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt >= max_attempts:
                raise
            delay = delays[min(attempt - 1, len(delays) - 1)]
            _log_retry(operation_name, attempt, delay, log_context, retry_metric, exc)
            time.sleep(delay)
            attempt += 1
        except Exception as exc:
            if attempt >= max_attempts or not _is_azure_transient(exc):
                raise
            delay = delays[min(attempt - 1, len(delays) - 1)]
            _log_retry(operation_name, attempt, delay, log_context, retry_metric, exc)
            time.sleep(delay)
            attempt += 1


def _retry_after_seconds(response: requests.Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _is_azure_transient(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return status_code in TRANSIENT_STATUS_CODES


def _log_retry(
    operation_name: str,
    attempt: int,
    delay: float,
    log_context: dict,
    retry_metric: Callable[[], None] | None,
    exc: Exception,
) -> None:
    if retry_metric:
        retry_metric()
    logging.warning(
        "retrying %s after attempt %s in %.1fs: %s",
        operation_name,
        attempt,
        delay,
        exc,
        extra={"custom_dimensions": {**log_context, "retry_count": attempt}},
    )
