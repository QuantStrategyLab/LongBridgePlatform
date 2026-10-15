from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_hk_notification_canary as canary


NOW = datetime(2026, 10, 2, 10, 25, tzinfo=timezone.utc)
PROJECT = "longbridgequant"
REGION = "asia-east2"
SERVICE = "longbridge-quant-hk-service"
OLD = "longbridge-quant-hk-service-old100"
CANDIDATE = "longbridge-quant-hk-service-candidate779"
BASE_URI = "https://longbridge-quant-hk-service-abc.run.app"


DEFAULT_PROFILE = "hk_global_etf_tactical_rotation"


def _runtime_env(
    selector: str | list[str], profile: str = DEFAULT_PROFILE
) -> dict[str, str]:
    target = {
        "platform_id": "longbridge",
        "service_name": SERVICE,
        "account_scope": "HK",
        "account_selector": selector,
        "deployment_selector": "HK",
        "strategy_profile": profile,
        "execution_mode": "paper",
        "dry_run_only": True,
    }
    import json

    return {
        "RUNTIME_TARGET_JSON": json.dumps(target, separators=(",", ":")),
        "RUNTIME_TARGET_ENABLED": "false",
        "LONGBRIDGE_DRY_RUN_ONLY": "true",
        "STRATEGY_PROFILE": profile,
    }


def _revision(
    name: str,
    commit: str,
    selector: str | list[str] = "HK",
    profile: str = DEFAULT_PROFILE,
) -> dict:
    is_candidate = name == CANDIDATE
    return {
        "name": f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}/revisions/{name}",
        "labels": {"commit-sha": commit},
        "conditions": [
            {"type": "Ready", "state": "CONDITION_SUCCEEDED"},
            {
                "type": "Active",
                "state": "CONDITION_FAILED" if is_candidate else "CONDITION_SUCCEEDED",
            },
            {
                "type": "ResourcesAvailable",
                "state": "CONDITION_RECONCILING"
                if is_candidate
                else "CONDITION_SUCCEEDED",
            },
            {"type": "ContainerReady", "state": "CONDITION_SUCCEEDED"},
        ],
        "containers": [
            {
                "env": [
                    {"name": key, "value": value}
                    for key, value in _runtime_env(selector, profile).items()
                ]
            }
        ],
    }


def _service(selector: str | list[str] = "HK", profile: str = DEFAULT_PROFILE) -> dict:
    env = _runtime_env(selector, profile)
    return {
        "name": f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}",
        "etag": "synthetic-etag",
        "generation": "821",
        "observedGeneration": "821",
        "uri": BASE_URI,
        "terminalCondition": {"type": "Ready", "state": "CONDITION_SUCCEEDED"},
        "traffic": [
            {
                "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": OLD,
                "percent": 100,
            }
        ],
        "trafficStatuses": [
            {"revision": OLD, "percent": 100, "tag": "", "uri": BASE_URI}
        ],
        "template": {
            "containers": [
                {"env": [{"name": key, "value": value} for key, value in env.items()]}
            ]
        },
    }


def _job(suffix: str, uri: str, *, probe: bool = False) -> dict:
    target = {"uri": uri, "httpMethod": "POST"}
    if probe:
        target["oidcToken"] = {
            "serviceAccountEmail": canary.snapshot.SCHEDULER_SERVICE_ACCOUNT,
            "audience": BASE_URI,
        }
    return {
        "name": f"projects/{PROJECT}/locations/{REGION}/jobs/{suffix}",
        "state": "PAUSED",
        "schedule": "0 0,12 * * 1-5",
        "timeZone": "UTC",
        "httpTarget": target,
        "retryConfig": {"retryCount": 0, "maxRetryDuration": "0s"},
    }


def _jobs() -> list[dict]:
    return [
        _job(f"{SERVICE}-scheduler", f"{BASE_URI}/run"),
        _job(f"{SERVICE}-probe-scheduler", f"{BASE_URI}/probe", probe=True),
        _job(f"{SERVICE}-precheck-scheduler", f"{BASE_URI}/precheck"),
    ]


@pytest.mark.parametrize("selector", ["HK", ["HK"]])
def test_preconditions_accept_both_existing_hk_selector_shapes(selector):
    selected, contract, traffic = canary.validate_canary_preconditions(
        service=_service(selector),
        candidate_revision=_revision(
            CANDIDATE, canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE, selector
        ),
        old_revision=_revision(OLD, canary.BASE_APPLICATION_SHA, selector),
        jobs=_jobs(),
        project=PROJECT,
        region=REGION,
        service_name=SERVICE,
        now=NOW,
    )

    assert set(selected) == {
        f"{SERVICE}-scheduler",
        f"{SERVICE}-probe-scheduler",
        f"{SERVICE}-precheck-scheduler",
    }
    assert contract["base_uri"] == BASE_URI
    assert contract["probe_resource"].endswith(f"/{SERVICE}-probe-scheduler")
    assert traffic == [
        {
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
            "revision": OLD,
            "percent": 100,
        }
    ]


def test_preconditions_accept_legal_native_profile_when_old_and_service_match():
    profile = "hk_low_vol_dividend_quality_snapshot"
    selected, contract, _ = canary.validate_canary_preconditions(
        service=_service(profile=profile),
        candidate_revision=_revision(
            CANDIDATE,
            canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE,
            profile=profile,
        ),
        old_revision=_revision(OLD, canary.BASE_APPLICATION_SHA, profile=profile),
        jobs=_jobs(),
        project=PROJECT,
        region=REGION,
        service_name=SERVICE,
        now=NOW,
    )

    assert len(selected) == 3
    assert contract["target"]["strategy_profile"] == profile
    manifest_hk_profile = next(
        item.strategy_profile
        for item in canary.load_runtime_target_manifest().targets
        if item.id == "hk"
    )
    assert manifest_hk_profile == DEFAULT_PROFILE


@pytest.mark.parametrize("drift", ["env", "container_config"])
def test_preconditions_reject_old_revision_profile_or_config_drift(drift):
    old_revision = _revision(OLD, canary.BASE_APPLICATION_SHA)
    if drift == "env":
        for variable in old_revision["containers"][0]["env"]:
            if variable["name"] == "STRATEGY_PROFILE":
                variable["value"] = "hk_low_vol_dividend_quality_snapshot"
            elif variable["name"] == "RUNTIME_TARGET_JSON":
                target = json.loads(variable["value"])
                target["strategy_profile"] = "hk_low_vol_dividend_quality_snapshot"
                variable["value"] = json.dumps(target, separators=(",", ":"))
    else:
        old_revision["containers"][0]["resources"] = {"limits": {"memory": "synthetic"}}

    with pytest.raises(canary.CanaryError, match="serving_config_mismatch"):
        canary.validate_canary_preconditions(
            service=_service(),
            candidate_revision=_revision(
                CANDIDATE, canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE
            ),
            old_revision=old_revision,
            jobs=_jobs(),
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            now=NOW,
        )


@pytest.mark.parametrize(
    ("native_profile", "environment_profile"),
    [
        ("", ""),
        ("unsupported_profile", "unsupported_profile"),
        ("hk_low_vol_dividend_quality_snapshot", DEFAULT_PROFILE),
    ],
)
def test_runtime_profile_must_be_legal_and_match_environment(
    native_profile, environment_profile
):
    env = _runtime_env("HK", native_profile)
    env["STRATEGY_PROFILE"] = environment_profile
    with pytest.raises(canary.CanaryError, match="runtime_target_mismatch"):
        canary._runtime_target(env, service=SERVICE)


def test_preconditions_reject_partial_identity_or_nonpaused_job():
    service = _service(["HK", "SG"])
    with pytest.raises(canary.CanaryError, match="runtime_target_mismatch"):
        canary.validate_canary_preconditions(
            service=service,
            candidate_revision=_revision(
                CANDIDATE, canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE
            ),
            old_revision=_revision(OLD, canary.BASE_APPLICATION_SHA),
            jobs=_jobs(),
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            now=NOW,
        )

    jobs = _jobs()
    jobs[1]["state"] = "ENABLED"
    with pytest.raises(canary.CanaryError, match="scheduler_inventory_invalid"):
        canary.validate_canary_preconditions(
            service=_service(),
            candidate_revision=_revision(
                CANDIDATE, canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE
            ),
            old_revision=_revision(OLD, canary.BASE_APPLICATION_SHA),
            jobs=jobs,
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            now=NOW,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"reconciling": True},
        {"reconciling": None},
        {"reconciling": 0},
        {"reconciling": "false"},
        {"generation": "822"},
        {"generation": "0", "observedGeneration": "0"},
        {"generation": 2**63, "observedGeneration": 2**63},
        {"observedGeneration": "not-a-generation"},
        {"terminalCondition": {"type": "Ready", "state": "CONDITION_FAILED"}},
    ],
)
def test_preconditions_reject_unconverged_service_shape(change):
    service = _service()
    service.update(change)
    with pytest.raises(canary.CanaryError, match="service_not_converged"):
        canary.validate_canary_preconditions(
            service=service,
            candidate_revision=_revision(
                CANDIDATE, canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE
            ),
            old_revision=_revision(OLD, canary.BASE_APPLICATION_SHA),
            jobs=_jobs(),
            project=PROJECT,
            region=REGION,
            service_name=SERVICE,
            now=NOW,
        )


def test_preconditions_accept_explicit_proto_default_false_reconciling():
    service = _service()
    service["reconciling"] = False
    selected, _, _ = canary.validate_canary_preconditions(
        service=service,
        candidate_revision=_revision(
            CANDIDATE, canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE
        ),
        old_revision=_revision(OLD, canary.BASE_APPLICATION_SHA),
        jobs=_jobs(),
        project=PROJECT,
        region=REGION,
        service_name=SERVICE,
        now=NOW,
    )
    assert len(selected) == 3


def test_quiet_window_uses_schedule_not_absent_scheduler_attempt_metadata():
    jobs = {"probe": _job("probe", f"{BASE_URI}/probe", probe=True)}
    canary._require_quiet_window(jobs, NOW)
    jobs["probe"]["schedule"] = "25 10 * * 1-5"
    with pytest.raises(canary.CanaryError, match="scheduler_natural_window"):
        canary._require_quiet_window(jobs, NOW)


@pytest.mark.parametrize(
    ("status", "event", "expected"),
    [
        (
            200,
            {"execution_window": "probe", "event": "health_probe_completed"},
            "completed",
        ),
        (
            500,
            {
                "execution_window": "probe",
                "event": "health_probe_failed",
                "probe_step": "balance",
                "error_code_status": "unknown",
                "error_code": None,
            },
            "failed",
        ),
    ],
)
def test_terminal_probe_outcomes_are_closed(status, event, expected):
    assert canary._candidate_outcome(status, event) == expected


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (302, {"execution_window": "probe", "event": "health_probe_completed"}),
        (403, {"execution_window": "probe", "event": "health_probe_completed"}),
        (
            500,
            {
                "execution_window": "probe",
                "event": "health_probe_failed",
                "probe_step": "unknown",
                "error_code_status": "known",
                "error_code": 4,
            },
        ),
        (
            500,
            {
                "execution_window": "probe",
                "event": "health_probe_failed",
                "probe_step": "balance",
                "error_code_status": "known",
                "error_code": 2**40,
            },
        ),
        (
            200,
            {
                "execution_window": "probe",
                "event": "health_probe_failed",
                "probe_step": "balance",
                "error_code_status": "known",
                "error_code": 1,
            },
        ),
    ],
)
def test_terminal_probe_rejects_unknown_or_mismatched_outcomes(status, event):
    with pytest.raises(canary.CanaryError):
        canary._candidate_outcome(status, event)


def test_request_and_event_logs_must_form_one_matching_terminal_pair():
    entries = [
        {
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"revision_name": CANDIDATE, "service_name": SERVICE},
            },
            "httpRequest": {
                "requestMethod": "POST",
                "requestUrl": "https://candidate-tag.run.app/probe",
                "status": 200,
            },
            "trace": "trace-1",
        },
        {
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"revision_name": CANDIDATE, "service_name": SERVICE},
            },
            "jsonPayload": {
                "event": "health_probe_completed",
                "execution_window": "probe",
            },
            "trace": "trace-1",
        },
    ]
    assert canary._parse_terminal_logs(
        entries,
        expected_revision=CANDIDATE,
        expected_service=SERVICE,
        expected_host="candidate-tag.run.app",
    ) == (200, entries[1]["jsonPayload"])
    entries[1]["trace"] = "trace-other"
    assert (
        canary._parse_terminal_logs(
            entries,
            expected_revision=CANDIDATE,
            expected_service=SERVICE,
            expected_host="candidate-tag.run.app",
        )
        is None
    )
    entries.append(
        {
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"revision_name": CANDIDATE, "service_name": SERVICE},
            },
            "httpRequest": {
                "requestMethod": "POST",
                "requestUrl": f"{BASE_URI}/other",
                "status": 404,
            },
        }
    )
    with pytest.raises(canary.CanaryError, match="probe_terminal_ambiguous"):
        canary._parse_terminal_logs(
            entries,
            expected_revision=CANDIDATE,
            expected_service=SERVICE,
            expected_host="candidate-tag.run.app",
        )


def test_extra_get_request_blocks_candidate_terminal_pair():
    entries = [
        {
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"revision_name": CANDIDATE, "service_name": SERVICE},
            },
            "httpRequest": {
                "requestMethod": "POST",
                "requestUrl": "https://candidate-tag.run.app/probe",
                "status": 200,
            },
            "trace": "trace-1",
        },
        {
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"revision_name": CANDIDATE, "service_name": SERVICE},
            },
            "jsonPayload": {
                "event": "health_probe_completed",
                "execution_window": "probe",
            },
            "trace": "trace-1",
        },
        {
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"revision_name": CANDIDATE, "service_name": SERVICE},
            },
            "httpRequest": {
                "requestMethod": "GET",
                "requestUrl": "https://candidate-tag.run.app/dry-run",
                "status": 200,
            },
        },
    ]
    with pytest.raises(canary.CanaryError, match="probe_terminal_ambiguous"):
        canary._parse_terminal_logs(
            entries,
            expected_revision=CANDIDATE,
            expected_service=SERVICE,
            expected_host="candidate-tag.run.app",
        )


def test_service_mutation_waits_only_for_its_region_operation(monkeypatch):
    operation_name = f"projects/{PROJECT}/locations/{REGION}/operations/op-123"

    class Response:
        status_code = 200
        is_redirect = False

        def __init__(self, value):
            self.content = json.dumps(value).encode()

    class Session:
        def __init__(self):
            self.values = [
                {"name": operation_name, "done": False},
                {"name": operation_name, "done": True},
            ]
            self.urls = []

        def get(self, url, **_kwargs):
            self.urls.append(url)
            return Response(self.values.pop(0))

    session = Session()
    ticks = iter([0.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr(canary.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(canary.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(canary.snapshot, "_close", lambda _value: None)
    canary._await_service_operation(
        session,
        {"name": operation_name},
        resource=f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}",
    )
    assert session.urls == [f"https://run.googleapis.com/v2/{operation_name}"] * 2

    wrong = Session()
    with pytest.raises(canary.CanaryError, match="service_operation_unknown"):
        canary._await_service_operation(
            wrong,
            {"name": f"projects/{PROJECT}/locations/other/operations/op-123"},
            resource=f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}",
        )
    assert wrong.urls == []


def test_revision_get_uses_cloud_run_v2_service_scoped_resource(monkeypatch):
    class Response:
        status_code = 200
        is_redirect = False
        content = b'{"name":"revision"}'

    class Session:
        urls = []

        def get(self, url, **_kwargs):
            self.urls.append(url)
            return Response()

    session = Session()
    monkeypatch.setattr(canary.snapshot, "_close", lambda _value: None)
    assert canary._revision_get(session, PROJECT, REGION, SERVICE, OLD) == {
        "name": "revision"
    }
    assert session.urls == [
        f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/services/{SERVICE}/revisions/{OLD}"
    ]


def test_complete_logging_pagination_and_scheduler_default_status(monkeypatch):
    class Response:
        status_code = 200
        is_redirect = False

        def __init__(self, payload):
            self.content = json.dumps(payload).encode()

    class Session:
        def __init__(self):
            self.calls = []

        def post(self, _url, *, json, **_kwargs):
            self.calls.append(json)
            if len(self.calls) == 1:
                return Response(
                    {"entries": [{"marker": "page1"}], "nextPageToken": "next"}
                )
            return Response({"entries": [{"marker": "page2"}]})

    session = Session()
    monkeypatch.setattr(canary.snapshot, "_close", lambda _value: None)
    entries = canary._read_terminal_logs(session, project=PROJECT, filter_text="fixed")
    assert entries == [{"marker": "page1"}, {"marker": "page2"}]
    assert session.calls[1]["pageToken"] == "next"
    assert (
        canary._job_run_status(
            {"state": "PAUSED", "lastAttemptTime": NOW.isoformat(), "status": {}},
            started_at=NOW,
        )
        == 0
    )
    assert (
        canary._job_run_status(
            {"state": "ENABLED", "lastAttemptTime": NOW.isoformat(), "status": {}},
            started_at=NOW,
        )
        == 0
    )
    assert (
        canary._job_run_status(
            {"state": "UNKNOWN", "lastAttemptTime": NOW.isoformat(), "status": {}},
            started_at=NOW,
        )
        is None
    )

    class RepeatingTokenSession:
        def post(self, _url, **_kwargs):
            return Response({"entries": [], "nextPageToken": "same"})

    with pytest.raises(canary.CanaryError, match="probe_log_pagination_invalid"):
        canary._read_terminal_logs(
            RepeatingTokenSession(), project=PROJECT, filter_text="fixed"
        )


def test_wait_terminal_accepts_matching_attempt_while_scheduler_remains_enabled(
    monkeypatch,
):
    entries = [
        {
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"revision_name": CANDIDATE, "service_name": SERVICE},
            },
            "httpRequest": {
                "requestMethod": "POST",
                "requestUrl": "https://candidate-tag.run.app/probe",
                "status": 200,
            },
            "trace": "trace-1",
        },
        {
            "resource": {
                "type": "cloud_run_revision",
                "labels": {"revision_name": CANDIDATE, "service_name": SERVICE},
            },
            "jsonPayload": {
                "event": "health_probe_completed",
                "execution_window": "probe",
            },
            "trace": "trace-1",
        },
    ]

    class Response:
        status_code = 200
        is_redirect = False

        def __init__(self, payload):
            self.content = json.dumps(payload).encode()

    class Session:
        def get(self, _url, **_kwargs):
            return Response(
                {
                    "state": "ENABLED",
                    "lastAttemptTime": NOW.isoformat(),
                    "status": {},
                }
            )

        def post(self, _url, *, json, **_kwargs):
            assert "httpRequest.requestMethod:*" in json["filter"]
            return Response({"entries": entries})

    monkeypatch.setattr(canary.snapshot, "_close", lambda _value: None)
    status = canary._wait_terminal(
        session=Session(),
        project=PROJECT,
        service=SERVICE,
        candidate_revision=CANDIDATE,
        candidate_host="candidate-tag.run.app",
        job_config=SimpleNamespace(
            scheduler_resource=(
                f"projects/{PROJECT}/locations/{REGION}/jobs/{SERVICE}-probe-scheduler"
            )
        ),
        started_at=NOW,
        monotonic=lambda: 100.0,
        sleep=lambda _seconds: None,
    )

    assert status == ("completed", 200, entries[1]["jsonPayload"])


def test_prior_full_revision_request_window_requires_exact_original_terminal():
    record = {
        "resource": {
            "type": "cloud_run_revision",
            "labels": {"revision_name": OLD, "service_name": SERVICE},
        },
        "timestamp": canary.PREVIOUS_REQUEST_TIMESTAMP,
        "httpRequest": {
            "requestMethod": "POST",
            "requestUrl": f"{BASE_URI}/probe",
            "status": 500,
            "latency": canary.PREVIOUS_REQUEST_LATENCY,
        },
    }
    canary._validate_previous_request(
        [record], old_revision=OLD, base_uri=BASE_URI, service=SERVICE, now=NOW
    )
    with pytest.raises(canary.CanaryError, match="previous_request_window_changed"):
        canary._validate_previous_request(
            [record, {**record, "timestamp": NOW.isoformat()}],
            old_revision=OLD,
            base_uri=BASE_URI,
            service=SERVICE,
            now=NOW,
        )
    get_request = {
        **record,
        "httpRequest": {
            **record["httpRequest"],
            "requestMethod": "GET",
            "requestUrl": f"{BASE_URI}/account-snapshot",
        },
    }
    with pytest.raises(canary.CanaryError, match="previous_request_window_changed"):
        canary._validate_previous_request(
            [record, get_request],
            old_revision=OLD,
            base_uri=BASE_URI,
            service=SERVICE,
            now=NOW,
        )


def test_log_window_filter_includes_every_http_method():
    filter_text = canary._log_filter(
        service=SERVICE,
        started_at=canary.PREVIOUS_REQUEST_START,
        requests_only=True,
    )
    assert "httpRequest.requestMethod:*" in filter_text
    assert 'httpRequest.requestMethod="POST"' not in filter_text


def test_private_backup_is_create_only_and_readback_verified(tmp_path):
    path = tmp_path / canary.BACKUP_RELATIVE_PATH
    body = b'{"synthetic":"backup"}'

    canary._create_backup_file(path, body, root=tmp_path)

    assert path.read_bytes() == body
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    with pytest.raises(canary.CanaryError, match="backup_create_only_failed"):
        canary._create_backup_file(path, b"replacement", root=tmp_path)
    assert path.read_bytes() == body


def test_private_backup_rejects_symlinked_directory_and_wrong_path(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (tmp_path / "qsl-account-facts").symlink_to(elsewhere, target_is_directory=True)
    path = tmp_path / canary.BACKUP_RELATIVE_PATH
    with pytest.raises(canary.CanaryError, match="backup_create_only_failed"):
        canary._create_backup_file(path, b"synthetic", root=tmp_path)

    wrong_path = tmp_path / "other.json"
    with pytest.raises(canary.CanaryError, match="backup_config_invalid"):
        canary._create_backup_file(wrong_path, b"synthetic", root=tmp_path)


def test_private_backup_rejects_nonprivate_existing_directory(tmp_path):
    private_parent = tmp_path / "qsl-account-facts"
    private_parent.mkdir(mode=0o700)
    os.chmod(private_parent, 0o755)
    path = tmp_path / canary.BACKUP_RELATIVE_PATH
    with pytest.raises(
        canary.CanaryError, match="backup_directory_permissions_invalid"
    ):
        canary._create_backup_file(path, b"synthetic", root=tmp_path)


def test_revision_generated_single_container_name_matches_omitted_template():
    service = _service()
    old = _revision(OLD, canary.BASE_APPLICATION_SHA)
    candidate = _revision(CANDIDATE, canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE)
    old["containers"][0].update(
        image="example.invalid/public/worker@sha256:" + "a" * 64, name="worker-1"
    )
    assert canary._revision_configuration_matches(service["template"], old)
    canary.validate_canary_preconditions(
        service=service,
        old_revision=old,
        candidate_revision=candidate,
        jobs=_jobs(),
        project=PROJECT,
        region=REGION,
        service_name=SERVICE,
        now=NOW,
    )


@pytest.mark.parametrize(
    "change",
    [
        "explicit",
        "arbitrary",
        "container_dependency",
        "template_dependency",
        "revision_dependency",
    ],
)
def test_generated_container_name_does_not_hide_explicit_names_or_references(change):
    service = _service()
    revision = _revision(OLD, canary.BASE_APPLICATION_SHA)
    revision["containers"][0].update(
        image="example.invalid/public/worker:source", name="worker-1"
    )
    template = service["template"]
    if change == "explicit":
        template["containers"][0]["name"] = "worker-2"
    elif change == "arbitrary":
        revision["containers"][0]["name"] = "unrelated-1"
    elif change == "container_dependency":
        revision["containers"][0]["dependsOn"] = ["other"]
    elif change == "template_dependency":
        template["annotations"] = {
            "run.googleapis.com/container-dependencies": '{"worker-1":[]}'
        }
    else:
        revision["annotations"] = {
            "run.googleapis.com/container-dependencies": '{"worker-1":[]}'
        }
    assert not canary._revision_configuration_matches(template, revision)


def test_service_config_ignores_only_output_modifier_and_keeps_config_changes():
    original = _service()
    original["lastModifier"] = "synthetic-operator-before"
    current = dict(original, lastModifier="synthetic-operator-after")
    assert canary._service_config_view(current) == canary._service_config_view(original)
    current["ingress"] = "INGRESS_TRAFFIC_ALL"
    assert canary._service_config_view(current) != canary._service_config_view(original)


def test_canary_tag_respects_provider_combined_name_limit():
    for length in (20, 31, 36, 37):
        name = "s" * length
        tag = canary._canary_tag(name)
        assert len(name) + len(tag) <= 46
        assert tag.startswith("hk-") and len(tag) >= 9
    with pytest.raises(canary.CanaryError, match="service_tag_name_budget_invalid"):
        canary._canary_tag("s" * 38)


def test_backup_reuse_is_read_only_and_requires_exact_original_snapshot(
    tmp_path, monkeypatch
):
    path = tmp_path / canary.BACKUP_RELATIVE_PATH
    monkeypatch.setattr(canary, "BACKUP_ROOT", tmp_path)
    monkeypatch.setattr(canary, "BACKUP_PATH", path)
    params = dict(
        backup_path=str(path),
        service={"synthetic": 1},
        candidate={"synthetic": 2},
        old={"synthetic": 3},
        jobs={"probe": {"state": "PAUSED"}},
    )
    canary._create_backup(**params)
    before = path.read_bytes()
    canary._create_backup(**params, reuse_existing=True)
    assert path.read_bytes() == before
    params["jobs"]["probe"]["lastAttemptTime"] = "2026-10-02T12:00:00Z"
    with pytest.raises(canary.CanaryError, match="backup_reuse_state_changed"):
        canary._create_backup(**params, reuse_existing=True)
    assert path.read_bytes() == before
    path.chmod(0o644)
    with pytest.raises(canary.CanaryError, match="backup_reuse_invalid"):
        canary._create_backup(**params, reuse_existing=True)


def _run_synthetic_canary_with_delayed_dispatch(
    monkeypatch, tmp_path, *, wait_fails=False, pause_fails=False
):
    state = {
        "service": _service(),
        "jobs": _jobs(),
        "events": [],
        "run_count": 0,
        "pause_count": 0,
        "uri_restores": 0,
        "traffic_restores": 0,
    }
    original_uri = f"{BASE_URI}/probe"
    previous_request = {
        "resource": {
            "type": "cloud_run_revision",
            "labels": {"revision_name": OLD, "service_name": SERVICE},
        },
        "timestamp": canary.PREVIOUS_REQUEST_TIMESTAMP,
        "httpRequest": {
            "requestMethod": "POST",
            "requestUrl": original_uri,
            "status": 500,
            "latency": canary.PREVIOUS_REQUEST_LATENCY,
        },
    }
    backup_path = tmp_path / canary.BACKUP_RELATIVE_PATH
    monkeypatch.setattr(canary, "BACKUP_ROOT", tmp_path)
    monkeypatch.setattr(canary, "BACKUP_PATH", backup_path)

    monkeypatch.setattr(
        canary,
        "_service_get",
        lambda _session, _resource: json.loads(json.dumps(state["service"])),
    )

    def revision_get(_session, _project, _region, _service, revision):
        if revision == CANDIDATE:
            return _revision(
                CANDIDATE, canary.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE
            )
        return _revision(OLD, canary.BASE_APPLICATION_SHA)

    monkeypatch.setattr(canary, "_revision_get", revision_get)
    monkeypatch.setattr(
        canary,
        "_scheduler_list",
        lambda *_args: json.loads(json.dumps(state["jobs"])),
    )
    monkeypatch.setattr(
        canary,
        "_read_terminal_logs",
        lambda *_args, **_kwargs: [previous_request],
    )
    monkeypatch.setattr(canary, "_validate_image_only_source", lambda **_kwargs: None)

    def patch_traffic(_session, _resource, *, etag, traffic):
        del etag
        state["events"].append("traffic_patch")
        if not any(row.get("tag") for row in traffic):
            state["traffic_restores"] += 1
        state["service"]["traffic"] = json.loads(json.dumps(list(traffic)))
        statuses = []
        for row in traffic:
            tag = row.get("tag") or ""
            statuses.append(
                {
                    "revision": row["revision"],
                    "percent": row.get("percent", 0),
                    "tag": tag,
                    "uri": "https://candidate-tag.run.app" if tag else BASE_URI,
                }
            )
        state["service"]["trafficStatuses"] = statuses

    monkeypatch.setattr(canary, "_service_patch_traffic", patch_traffic)

    def patch_uri(_session, _resource, uri):
        state["events"].append("uri_patch")
        if uri == original_uri:
            state["uri_restores"] += 1
        state["jobs"][1]["httpTarget"]["uri"] = uri

    monkeypatch.setattr(canary, "_scheduler_patch_uri", patch_uri)
    monkeypatch.setattr(canary, "_candidate_tag_url", lambda *_args, **_kwargs: "https://candidate-tag.run.app")

    monkeypatch.setattr(
        canary.snapshot,
        "_scheduler_get",
        lambda _session, _config: json.loads(json.dumps(state["jobs"][1])),
    )

    def scheduler_action(_session, _config, action):
        state["events"].append(action)
        if action == "resume":
            state["jobs"][1]["state"] = "ENABLED"
        else:
            state["pause_count"] += 1
            if pause_fails:
                raise RuntimeError("synthetic pause failure")
            state["jobs"][1]["state"] = "PAUSED"

    monkeypatch.setattr(canary.snapshot, "_scheduler_action", scheduler_action)

    def scheduler_run(_session, _config):
        state["events"].append("run")
        state["run_count"] += 1

    monkeypatch.setattr(canary.snapshot, "_scheduler_run", scheduler_run)

    def wait_terminal(**_kwargs):
        state["events"].append("wait")
        # Model a delayed Cloud Scheduler dispatch: the job must remain enabled
        # through terminal observation, after exactly one accepted Run request.
        assert state["jobs"][1]["state"] == "ENABLED"
        assert state["run_count"] == 1
        if wait_fails:
            raise canary._stop("probe_terminal_unknown")
        return "completed", 200, {
            "event": "health_probe_completed",
            "execution_window": "probe",
        }

    monkeypatch.setattr(canary, "_wait_terminal", wait_terminal)
    monkeypatch.setattr(canary.snapshot, "_close", lambda _session: None)
    monkeypatch.setattr(
        canary,
        "_require_quiet_window",
        lambda _jobs, _now: None,
    )

    env = {
        "WORKFLOW_TARGET": "HK",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
        "GOOGLE_CLOUD_PROJECT": PROJECT,
        "HK_NOTIFICATION_CANARY_BACKUP_PATH": str(backup_path),
        "CLOUD_RUN_SERVICE": SERVICE,
        "CLOUD_RUN_REGION": REGION,
        "HK_CANDIDATE_REVISION": CANDIDATE,
    }

    def run():
        return canary.run_hk_notification_canary(
            env=env,
            session_factory=lambda: object(),
            now_reader=lambda: NOW,
            monotonic=lambda: 100.0,
            sleep=lambda _seconds: None,
        )

    return run, state


def test_run_waits_for_delayed_dispatch_before_pause_and_restores_after_terminal(
    monkeypatch, tmp_path
):
    run, state = _run_synthetic_canary_with_delayed_dispatch(
        monkeypatch, tmp_path
    )

    assert run() == "completed"
    assert state["events"].index("run") < state["events"].index("wait")
    assert state["events"].index("wait") < state["events"].index("pause")
    assert state["uri_restores"] == 1
    assert state["traffic_restores"] == 1
    assert state["run_count"] == 1
    assert state["pause_count"] == 1
    assert state["jobs"][1]["state"] == "PAUSED"
    assert state["jobs"][1]["httpTarget"]["uri"] == f"{BASE_URI}/probe"
    assert canary._traffic_rows(state["service"]) == [
        {"revision": OLD, "percent": 100, "tag": "", "uri": BASE_URI}
    ]


def test_wait_failure_pauses_once_and_preserves_unknown_without_config_restore(
    monkeypatch, tmp_path
):
    run, state = _run_synthetic_canary_with_delayed_dispatch(
        monkeypatch, tmp_path, wait_fails=True
    )

    with pytest.raises(canary.CanaryError, match="probe_terminal_unknown"):
        run()
    assert state["events"].index("wait") < state["events"].index("pause")
    assert state["jobs"][1]["state"] == "PAUSED"
    assert state["pause_count"] == 1
    assert state["run_count"] == 1
    assert state["uri_restores"] == 0
    assert state["traffic_restores"] == 0


def test_pause_failure_after_wait_blocks_uri_and_traffic_restore_without_rerun(
    monkeypatch, tmp_path
):
    run, state = _run_synthetic_canary_with_delayed_dispatch(
        monkeypatch, tmp_path, pause_fails=True
    )

    with pytest.raises(canary.CanaryError, match="probe_pause_unknown"):
        run()
    assert state["events"].index("wait") < state["events"].index("pause")
    assert state["jobs"][1]["state"] == "ENABLED"
    assert state["pause_count"] == 1
    assert state["run_count"] == 1
    assert state["uri_restores"] == 0
    assert state["traffic_restores"] == 0


def test_pause_failure_does_not_mask_terminal_observation_failure(
    monkeypatch, tmp_path
):
    run, state = _run_synthetic_canary_with_delayed_dispatch(
        monkeypatch, tmp_path, wait_fails=True, pause_fails=True
    )

    with pytest.raises(canary.CanaryError, match="probe_terminal_unknown") as raised:
        run()
    assert str(raised.value.__cause__) == "hk_notification_canary_probe_pause_unknown"
    assert state["pause_count"] == 1
    assert state["run_count"] == 1
    assert state["uri_restores"] == 0
    assert state["traffic_restores"] == 0
