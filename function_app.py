import json
import logging
import os
from html import escape

import azure.functions as func
from azure.identity import DefaultAzureCredential

from sharepoint_delta.config import load_settings
from sharepoint_delta.pipeline import run_change_detection
from sharepoint_delta.sharepoint_client import SharePointRestClient


app = func.FunctionApp()
CODE_VERSION = os.getenv("CODE_VERSION", "v1.3.1")


@app.route(route="{*path}", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def version_page(req: func.HttpRequest) -> func.HttpResponse:
    version = escape(CODE_VERSION)
    body = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SharePoint Delta Version</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: Arial, sans-serif;
      background: #f4f7fb;
      color: #1f2937;
    }}
    main {{
      width: min(92vw, 420px);
      padding: 28px;
      border: 1px solid #d7dee8;
      border-radius: 8px;
      background: #ffffff;
      box-shadow: 0 12px 30px rgba(31, 41, 55, 0.08);
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 24px;
      line-height: 1.2;
    }}
    .label {{
      margin: 0 0 8px;
      color: #52606d;
      font-size: 14px;
    }}
    .version {{
      margin: 0;
      font-size: 32px;
      font-weight: 700;
      color: #0f766e;
    }}
  </style>
</head>
<body>
  <main>
    <h1>SharePoint Delta</h1>
    <p class="label">Current code tag</p>
    <p class="version">{version}</p>
  </main>
</body>
</html>"""
    return func.HttpResponse(body=body, status_code=200, mimetype="text/html")


@app.timer_trigger(
    schedule="0 0 11 * * *",
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
