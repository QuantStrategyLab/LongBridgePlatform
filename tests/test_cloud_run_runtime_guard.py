from __future__ import annotations

import datetime as dt
import json
import re
import subprocess

from scripts import cloud_run_runtime_guard as guard


def _clear_runtime_guard_env(monkeypatch):
    for name in (
        "RUNTIME_GUARD_CLOUD_RUN_SERVICES",
        "CLOUD_RUN_SERVICES",
        "CLOUD_RUN_SERVICE",
        "CLOUD_RUN_SERVICE_TARGETS_JSON",
        "CLOUD_RUN_REGION",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_services_prefers_explicit_service_over_target_list(monkeypatch):
    _clear_runtime_guard_env(monkeypatch)
    monkeypatch.setenv("CLOUD_RUN_SERVICE", "longbridge-quant-hk-service")
    monkeypatch.setenv(
        "CLOUD_RUN_SERVICE_TARGETS_JSON",
        json.dumps(
            {
                "targets": [
                    {"service": "longbridge-quant-paper-service"},
                    {"service": "longbridge-quant-sg-service"},
                ]
            }
        ),
    )

    assert guard._load_services() == ["longbridge-quant-hk-service"]


def test_load_services_falls_back_to_target_list(monkeypatch):
    _clear_runtime_guard_env(monkeypatch)
    monkeypatch.setenv(
        "CLOUD_RUN_SERVICE_TARGETS_JSON",
        json.dumps(
            {
                "targets": [
                    {"service": "longbridge-quant-paper-service"},
                    {"runtime_target": {"service_name": "longbridge-quant-sg-service"}},
                ]
            }
        ),
    )

    assert guard._load_services() == [
        "longbridge-quant-paper-service",
        "longbridge-quant-sg-service",
    ]


def test_cloud_run_log_filter_includes_region_when_available():
    log_filter = guard._cloud_run_log_filter(
        "longbridge-quant-paper-service",
        "2026-07-01T12:00:00Z",
        "asia-east1",
    )

    assert 'resource.labels.service_name="longbridge-quant-paper-service"' in log_filter
    assert 'resource.labels.location="asia-east1"' in log_filter
    assert 'timestamp >= "2026-07-01T12:00:00Z"' in log_filter


def test_scheduler_job_pattern_includes_service_alias():
    pattern = guard._scheduler_job_pattern_for_services(["longbridge-quant-hk-service"])

    assert re.search(pattern, "longbridge-quant-hk-service-scheduler")
    assert re.search(pattern, "longbridge-quant-hk-scheduler")
    assert not re.search(pattern, "longbridge-quant-sg-scheduler")


def test_telegram_token_falls_back_to_secret_manager(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TG_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TOKEN_SECRET_NAME", "platform-telegram-token")
    monkeypatch.setenv("GCP_PROJECT_ID", "longbridgequant")
    observed = {}

    def fake_run_gcloud(command):
        observed["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="secret-token\n", stderr="")

    monkeypatch.setattr(guard, "_run_gcloud", fake_run_gcloud)

    assert guard._telegram_token() == "secret-token"
    assert observed["command"] == [
        "gcloud",
        "secrets",
        "versions",
        "access",
        "latest",
        "--secret",
        "platform-telegram-token",
        "--project",
        "longbridgequant",
    ]


def test_cloud_run_log_since_uses_latest_ready_revision(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_REGION", "us-central1")
    observed = []

    def fake_run_gcloud(command):
        observed.append(command)
        if command[1:4] == ["run", "services", "describe"]:
            payload = {"status": {"latestReadyRevisionName": "longbridge-quant-hk-service-00002"}}
        else:
            payload = {"metadata": {"creationTimestamp": "2026-07-01T06:50:04.123Z"}}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(guard, "_run_gcloud", fake_run_gcloud)

    fallback = dt.datetime(2026, 7, 1, 6, 0, tzinfo=dt.timezone.utc)
    result = guard._cloud_run_log_since("longbridgequant", "longbridge-quant-hk-service", fallback)

    assert result == dt.datetime(2026, 7, 1, 6, 50, 4, 123000, tzinfo=dt.timezone.utc)
    assert observed[0] == [
        "gcloud",
        "run",
        "services",
        "describe",
        "longbridge-quant-hk-service",
        "--project",
        "longbridgequant",
        "--region",
        "us-central1",
        "--format=json",
    ]
    assert observed[1][1:5] == ["run", "revisions", "describe", "longbridge-quant-hk-service-00002"]


def test_region_for_service_prefers_target_region(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_REGION", "us-central1")
    monkeypatch.setenv(
        "CLOUD_RUN_SERVICE_TARGETS_JSON",
        json.dumps(
            {
                "targets": [
                    {"service": "longbridge-quant-hk-service", "region": "asia-east1"},
                ]
            }
        ),
    )

    assert guard._region_for_service("longbridge-quant-hk-service") == "asia-east1"
