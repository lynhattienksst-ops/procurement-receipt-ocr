"""
Dashboard micro-service (Cloud Run) — READ-ONLY view over the live Google Sheet.

Serves the executive + ops dashboards for the Procurement Receipt OCR Hub.
This service NEVER writes to ``Data_Header_V2`` / ``Data_Lines_V2`` or any source
tab — same read-only posture as ``services/reconciliation_engine.py``.

P0 milestone (this file): token-gated landing page + health endpoints + a live
Google Sheets auth/reachability probe. The data-quality layer (P1), the
analytical layer (P2) and the real charts (P3) land in later phases.

Auth resolution order (first that works wins):
  1. ``GOOGLE_SERVICE_ACCOUNT_JSON`` — full JSON string (local / CI)
  2. ``GOOGLE_SERVICE_ACCOUNT_FILE`` / ``service_account.json`` on disk (local dev)
  3. ``google.auth.default()`` — Application Default Credentials; on Cloud Run the
     service runs AS the procurement service account, which already has Sheet access

Env:
  DASHBOARD_TOKEN           (required) — secret path segment, ``/d/<token>``
  GOOGLE_SHEET_ID           (required) — spreadsheet to read
  GOOGLE_SHEET_HEADER_NAME  (default ``Data_Header_V2``)
  GOOGLE_SHEET_LINES_NAME   (default ``Data_Lines_V2``)
  DASHBOARD_CACHE_TTL       (default ``180``) — seconds; used from P1 onward
  BUILD_SHA / BUILD_TIME    — stamped by the deploy workflow
"""
import hmac
import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dashboard")

SCOPES_RO = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
HEADER_TAB = os.getenv("GOOGLE_SHEET_HEADER_NAME", "Data_Header_V2")
LINES_TAB = os.getenv("GOOGLE_SHEET_LINES_NAME", "Data_Lines_V2")
CACHE_TTL = int(os.getenv("DASHBOARD_CACHE_TTL", "180"))
BUILD_SHA = os.getenv("BUILD_SHA", "dev")
BUILD_TIME = os.getenv("BUILD_TIME", "")

_INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

app = FastAPI(title="Procurement Dashboard", docs_url=None, redoc_url=None, openapi_url=None)


def _token_ok(token: str) -> bool:
    """Constant-time comparison; empty configured token denies everything."""
    return bool(DASHBOARD_TOKEN) and hmac.compare_digest(token, DASHBOARD_TOKEN)


def _load_credentials():
    """Return ``(credentials, auth_mode)`` for the read-only Sheets scope. Never raises."""
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        try:
            from google.oauth2 import service_account
            info = json.loads(raw)
            return service_account.Credentials.from_service_account_info(info, scopes=SCOPES_RO), "sa_json_env"
        except Exception as e:  # noqa: BLE001
            logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON unusable: %s", e)

    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    for p in (path, "service_account.json", "/app/service_account.json"):
        if p and os.path.exists(p):
            try:
                from google.oauth2 import service_account
                return service_account.Credentials.from_service_account_file(p, scopes=SCOPES_RO), f"sa_file:{p}"
            except Exception as e:  # noqa: BLE001
                logger.warning("service_account file %s unusable: %s", p, e)

    try:
        import google.auth
        creds, _ = google.auth.default(scopes=SCOPES_RO)
        return creds, "adc"
    except Exception as e:  # noqa: BLE001
        logger.warning("ADC unavailable: %s", e)
    return None, "none"


def _sheets_probe() -> dict:
    """Cheap live check: can we authenticate and open the spreadsheet metadata?"""
    out = {"sheet_id_set": bool(SHEET_ID), "auth_mode": "none", "reachable": False}
    creds, mode = _load_credentials()
    out["auth_mode"] = mode
    if not creds or not SHEET_ID:
        return out
    try:
        from googleapiclient.discovery import build
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        meta = svc.spreadsheets().get(
            spreadsheetId=SHEET_ID, fields="properties.title,sheets.properties.title"
        ).execute()
        tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
        out.update(
            reachable=True,
            spreadsheet_title=meta.get("properties", {}).get("title"),
            header_tab_present=HEADER_TAB in tabs,
            lines_tab_present=LINES_TAB in tabs,
        )
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    return out


@app.get("/healthz")
def healthz():
    """Unauthenticated liveness probe for Cloud Run."""
    return {"status": "ok", "build": BUILD_SHA}


@app.get("/", response_class=PlainTextResponse)
def root():
    return "Procurement Dashboard. Open /d/<token>."


@app.get("/d/{token}", response_class=HTMLResponse)
def dashboard(token: str):
    if not _token_ok(token):
        return PlainTextResponse("Not found", status_code=404)
    html = (
        _INDEX_HTML
        .replace("{{BUILD_SHA}}", BUILD_SHA)
        .replace("{{BUILD_TIME}}", BUILD_TIME or "—")
    )
    return HTMLResponse(html)


@app.get("/d/{token}/api/health", response_class=JSONResponse)
def api_health(token: str):
    if not _token_ok(token):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return {
        "status": "ok",
        "build": {"sha": BUILD_SHA, "time": BUILD_TIME},
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cache_ttl_s": CACHE_TTL,
        "sheets": _sheets_probe(),
    }
