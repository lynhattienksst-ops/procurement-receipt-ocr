"""P0 smoke tests for the Cloud Run dashboard micro-service (`dashboard/app.py`).

Read-only service — these only exercise routing, the token gate, and the
health payload shape. The Sheets probe is not network-mocked here; it degrades
to ``auth_mode: none`` / ``reachable: false`` with no credentials, which is the
contract we assert.
"""
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DASHBOARD_TOKEN", "test-token")
os.environ.pop("GOOGLE_SHEET_ID", None)
os.environ.pop("GOOGLE_SERVICE_ACCOUNT_JSON", None)

from dashboard.app import app  # noqa: E402

TOKEN = os.environ["DASHBOARD_TOKEN"]
client = TestClient(app)


def test_healthz_is_open():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_hint_no_token_leak():
    r = client.get("/")
    assert r.status_code == 200
    assert TOKEN not in r.text


def test_bad_token_is_404_not_403():
    # 404 so a wrong guess cannot distinguish "wrong token" from "no such path"
    assert client.get("/d/wrong").status_code == 404
    assert client.get("/d/wrong/api/health").status_code == 404


def test_good_token_serves_page_with_placeholders_filled():
    r = client.get(f"/d/{TOKEN}")
    assert r.status_code == 200
    assert "{{BUILD_SHA}}" not in r.text
    assert "{{BUILD_TIME}}" not in r.text


def test_api_health_shape_without_credentials():
    r = client.get(f"/d/{TOKEN}/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body) >= {"build", "server_time", "cache_ttl_s", "sheets"}
    sheets = body["sheets"]
    # No GOOGLE_SHEET_ID -> the probe returns before any network call, whatever
    # credential source happens to exist on the host.
    assert sheets["reachable"] is False
    assert sheets["sheet_id_set"] is False
    assert isinstance(sheets["auth_mode"], str) and sheets["auth_mode"]


@pytest.mark.parametrize("path", ["/d/", "/d", "/d//api/health"])
def test_empty_token_denied(path):
    assert client.get(path).status_code in {404, 307}
