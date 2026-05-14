import json
import logging
import os

import azure.functions as func
from azure.identity import DefaultAzureCredential

from sharepoint_delta.config import load_settings
from sharepoint_delta.pipeline import run_change_detection
from sharepoint_delta.sharepoint_client import SharePointRestClient


app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 */1 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def sharepoint_change_detection_timer(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logging.warning("sharepoint_change_detection_timer is running later than scheduled")

    if os.getenv("DISABLE_CHANGE_DETECTION_TIMER", "").lower() in {"1", "true", "yes"}:
        logging.info("sharepoint_change_detection_timer is disabled by DISABLE_CHANGE_DETECTION_TIMER")
        return

    run_change_detection()


@app.route(route="sharepoint-rest-test", auth_level=func.AuthLevel.ANONYMOUS)
def sharepoint_rest_test(req: func.HttpRequest) -> func.HttpResponse:
    try:
        settings = load_settings()
        credential = DefaultAzureCredential()
        sharepoint = SharePointRestClient(
            credential,
            settings,
            {"phase": "phase1", "function_name": "sharepoint_rest_test"},
            lambda: None,
        )

        result = sharepoint.probe_access()
        result["endpoint"] = "sharepoint-rest-test"
        status_code = 200 if result["ok"] else 502
    except Exception as exc:
        logging.exception("sharepoint rest test failed")
        result = {
            "ok": False,
            "endpoint": "sharepoint-rest-test",
            "error": type(exc).__name__,
            "message": str(exc),
        }
        status_code = 500

    return func.HttpResponse(
        body=json.dumps(result, ensure_ascii=False, indent=2),
        status_code=status_code,
        mimetype="application/json",
    )
