import datetime as dt
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execution_report_heartbeat as heartbeat  # noqa: E402


def _clear_runtime_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("RUNTIME_HEARTBEAT_") or name in {
            "CLOUD_RUN_SERVICE",
            "CLOUD_RUN_SERVICES",
            "CLOUD_RUN_SERVICE_TARGETS_JSON",
            "EXECUTION_REPORT_GCS_URI",
            "FIRSTRADE_GCS_STATE_BUCKET",
            "FIRSTRADE_STATE_PREFIX",
            "GCP_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
        }:
            monkeypatch.delenv(name, raising=False)


def test_main_skips_outside_expected_day_of_month_window(monkeypatch, capsys):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NAME", "Monthly runtime")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_EXPECTED_DAY_OF_MONTH", "1-7")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_EXPECTED_TIMEZONE", "America/New_York")

    monkeypatch.setattr(
        heartbeat,
        "_list_gcs_objects",
        lambda *_args, **_kwargs: pytest.fail("GCS should not be queried outside the expected window"),
    )

    result = heartbeat.main(now=dt.datetime(2026, 6, 10, 1, 35, tzinfo=dt.timezone.utc))

    assert result == 0
    output = capsys.readouterr().out
    assert "Execution report heartbeat skipped for Monthly runtime" in output
    assert "outside expected day-of-month window 1-7 in America/New_York" in output
    assert "local_date=2026-06-09" in output


def test_main_checks_reports_inside_expected_day_of_month_window(monkeypatch, capsys):
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("GCP_PROJECT_ID", "longbridgequant")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NAME", "Monthly runtime")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_REPORT_PLATFORM", "longbridge")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE", "MONTHLY")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_REQUIRED_SERVICES", "longbridge-monthly-service")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_GCS_URIS", "gs://bucket/execution-reports")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_EXPECTED_DAY_OF_MONTH", "1-7")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_EXPECTED_TIMEZONE", "America/New_York")

    observed = {}

    def fake_list_gcs_objects(gcs_glob, *, project):
        observed["gcs_glob"] = gcs_glob
        observed["project"] = project
        return [
            {
                "url": "gs://bucket/execution-reports/longbridge/profile/MONTHLY/2026-06/report.json",
                "metadata": {"updated": "2026-06-04T23:20:00Z"},
            }
        ]

    def fake_cat_gcs_json(uri, *, project):
        observed["cat_uri"] = uri
        observed["cat_project"] = project
        return {
            "platform": "longbridge",
            "account_scope": "MONTHLY",
            "service_name": "longbridge-monthly-service",
            "status": "ok",
        }

    monkeypatch.setattr(heartbeat, "_list_gcs_objects", fake_list_gcs_objects)
    monkeypatch.setattr(heartbeat, "_cat_gcs_json", fake_cat_gcs_json)

    result = heartbeat.main(now=dt.datetime(2026, 6, 4, 23, 30, tzinfo=dt.timezone.utc))

    assert result == 0
    assert observed["gcs_glob"] == "gs://bucket/execution-reports/longbridge/**/2026-06/*.json"
    assert observed["project"] == "longbridgequant"
    assert observed["cat_project"] == "longbridgequant"
    assert observed["cat_uri"].endswith("/report.json")
    output = capsys.readouterr().out
    assert "Execution report heartbeat OK for Monthly runtime" in output
    assert "longbridge-monthly-service@2026-06-04T23:20:00+00:00" in output
