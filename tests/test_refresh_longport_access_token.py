from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.refresh_longport_access_token import (  # noqa: E402
    call_longport_refresh,
    plan_refresh,
    run_refresh,
)


def _jwt(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


def test_plan_refresh_threshold_and_expired():
    assert plan_refresh(days_remaining=45, refresh_threshold_days=30, force=False) == (
        False,
        "outside_threshold",
    )
    assert plan_refresh(days_remaining=10, refresh_threshold_days=30, force=False)[0] is True
    assert plan_refresh(days_remaining=-1, refresh_threshold_days=30, force=False) == (
        False,
        "already_expired_needs_portal_reset",
    )
    assert plan_refresh(days_remaining=45, refresh_threshold_days=30, force=True)[0] is True


def test_dry_run_would_refresh_without_network():
    now = time.time()
    observed = {"reads": 0, "writes": 0, "refresh": 0}

    def reader(_project, _secret):
        observed["reads"] += 1
        return _jwt(int(now + 10 * 86400))

    def writer(*_a, **_k):
        observed["writes"] += 1
        raise AssertionError("dry-run must not write")

    def refresher(**_k):
        observed["refresh"] += 1
        raise AssertionError("dry-run must not call LongPort")

    result = run_refresh(
        project_id="demo",
        target="paper",
        dry_run=True,
        refresh_threshold_days=30,
        secret_reader=reader,
        secret_writer=writer,
        refresh_caller=refresher,
        now=now,
    )
    assert result["status"] == "dry_run_would_refresh"
    assert result["ok"] is True
    assert observed == {"reads": 1, "writes": 0, "refresh": 0}
    jwt_value = _jwt(int(now + 10 * 86400))
    assert jwt_value not in json.dumps(result)


def test_live_refresh_writes_version_name_only():
    now = time.time()
    secrets = {
        "longport_token_paper": _jwt(int(now + 5 * 86400)),
        "longport-app-key-paper": "app-key",
        "longport-app-secret-paper": "app-secret",
    }
    written = {}

    def reader(_project, name):
        return secrets[name]

    def writer(_project, name, payload):
        written["name"] = name
        written["payload"] = payload
        return "projects/demo/secrets/longport_token_paper/versions/9"

    def refresher(**kwargs):
        assert kwargs["app_key"] == "app-key"
        assert kwargs["access_token"] == secrets["longport_token_paper"]
        return "brand-new-token"

    result = run_refresh(
        project_id="demo",
        target="paper",
        dry_run=False,
        refresh_threshold_days=30,
        secret_reader=reader,
        secret_writer=writer,
        refresh_caller=refresher,
        now=now,
    )
    assert result["status"] == "refreshed"
    assert result["new_version"].endswith("/versions/9")
    assert written["payload"] == "brand-new-token"
    dumped = json.dumps(result)
    assert "brand-new-token" not in dumped
    assert "app-secret" not in dumped


def test_call_longport_refresh_sends_expired_at():
    class Req:
        last_url = None

        @classmethod
        def get(cls, url, headers, timeout):
            cls.last_url = url

            class Response:
                @staticmethod
                def json():
                    return {"code": 0, "data": {"token": "rotated"}}

            return Response()

    token = call_longport_refresh(
        access_token="tok",
        app_key="k",
        app_secret="s",
        lifetime_days=90,
        requests_module=Req,
    )
    assert token == "rotated"
    query = parse_qs(urlparse(Req.last_url).query)
    assert "expired_at" in query
