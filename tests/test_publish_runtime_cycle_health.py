from copy import deepcopy
import datetime as dt
import hashlib
import importlib.util
import json
import sys
from types import SimpleNamespace
from types import ModuleType
from pathlib import Path

import pytest

from scripts import publish_runtime_cycle_health as publisher
from scripts.collect_runtime_cycle_health import cycle_configuration_sha256
from scripts.runtime_cycle_health_source import SourceContractError


_spec = importlib.util.spec_from_file_location(
    "cycle_fixtures", Path(__file__).with_name("test_runtime_cycle_health_collection.py")
)
fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixtures)
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 7, 16, 0, tzinfo=UTC)
REQUIRED_FROM = "2026-10-05T00:00:00Z"
OLD_COVERED = "2026-10-06T15:00:00Z"


def calendar(_name, *, start_date, end_date):
    return {
        start_date + dt.timedelta(days=i)
        for i in range((end_date - start_date).days + 1)
        if (start_date + dt.timedelta(days=i)).weekday() < 5
    }


def env():
    return {
        "RUNTIME_CYCLE_HEALTH_ENABLED": "true",
        "RUNTIME_CYCLE_HEALTH_SYNC_URL": "https://qrs.example.test/api/internal/runtime-cycle-health-source",
        "RUNTIME_CYCLE_HEALTH_SOURCE_BINDING_ID": fixtures.f.CTX.source_binding_id,
        "ACCOUNT_FACTS_SYNC_TOKEN": "synthetic-token",
        "CLOUD_RUN_SERVICE": fixtures.f.CTX.service,
        "GCP_PROJECT_ID": "synthetic-project",
        "CLOUD_RUN_REGION": "synthetic-region",
        "RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES": "30",
        "RUNTIME_HEARTBEAT_GCS_URIS": "gs://synthetic-bucket/reports",
        "RUNTIME_TARGET_CONFIGURED_STATE": "enabled",
        "RUNTIME_TARGET_EXECUTION_MODE": "paper",
        "RUNTIME_GUARD_STATUS": "pass",
        "EXECUTION_HEARTBEAT_STATUS": "pass",
    }


def checkpoint_response(source):
    return {
        "ok": True,
        "schema_version": "qsl_runtime_cycle_health_checkpoint.v2",
        "status": "initialized",
        "source_id": "longbridge.paper",
        "target_id": "longbridge.paper",
        "configuration_sha256": cycle_configuration_sha256(
            source, report_root_uri=source["report_root_uri"]
        ),
        "source_binding_id": fixtures.f.CTX.source_binding_id,
        "authority_revision": 1,
        "checkpoint_revision": 2,
        "observed_at": "2026-10-06T15:30:00Z",
        "observation_sha256": "d" * 64,
        "received_at": "2026-10-06T15:30:01Z",
        "required_from": REQUIRED_FROM,
        "covered_through": OLD_COVERED,
        "coverage_complete": True,
        "coverage_incomplete_reason": None,
        "checkpoint": {
            "schema_version": "qsl_runtime_cycle_health_checkpoint_state.v2",
            "configuration_sha256": cycle_configuration_sha256(
                source, report_root_uri=source["report_root_uri"]
            ),
            "required_from": REQUIRED_FROM,
            "baseline_established": True,
            "covered_through": OLD_COVERED,
            "state": {
                "schema_version": "qsl_runtime_cycle_health_state_summary.v1",
                "target_id": "longbridge.paper",
                "source_binding_id": fixtures.f.CTX.source_binding_id,
                "incident_count": 0,
                "fault_attempt_count": 0,
                "unresolved_count": 0,
                "highest_severity": None,
                "active_category": None,
                "latest_fault_at": None,
                "resolution_history_count": 0,
                "history_complete": False,
                "execution_authority_granted": False,
            },
        },
        "history_page": {"items": [], "next_cursor": None, "complete": True},
        "adopted": False,
        "no_order": True,
        "execution_authority_granted": False,
    }


@pytest.fixture
def source_wiring(monkeypatch):
    source = fixtures.source("0 15 * * 1-5")
    source["report_root_uri"] = "gs://synthetic-bucket/reports"
    monkeypatch.setattr(publisher, "_serving_source", lambda _env, binding_id: deepcopy(source))
    monkeypatch.setattr(publisher, "_market_session_dates", calendar)
    monkeypatch.setattr(
        publisher,
        "_gcs_callbacks",
        lambda _project, _bucket: (
            lambda prefix, token, start, end: {"items": [], "nextPageToken": None},
            lambda _uri: pytest.fail("no object should be read for an empty synthetic prefix"),
        ),
    )
    return source


def test_feature_is_disabled_without_reading_configuration_or_transport(monkeypatch):
    monkeypatch.setattr(
        publisher,
        "_serving_source",
        lambda *_args, **_kwargs: pytest.fail("disabled path must not read GCP metadata"),
    )
    monkeypatch.setattr(
        publisher,
        "_qrs_request",
        lambda *_args, **_kwargs: pytest.fail("disabled path must not call QRS"),
    )
    assert publisher.publish({"RUNTIME_CYCLE_HEALTH_ENABLED": "false"}, now=NOW) == "disabled"


def revision_response(report_env_rows):
    source = fixtures.source("0 15 * * 1-5")
    revision = deepcopy(source["serving_context"]["revision"])
    revision["metadata"]["name"] = "synthetic-paper-r1"
    revision["metadata"]["labels"]["serving.knative.dev/service"] = "synthetic-paper"
    revision["status"] = {"conditions": [{"type": "Ready", "status": "True"}]}
    revision["spec"]["containers"][0]["env"].extend(report_env_rows)
    return {
        "status": {
            "traffic": [{"revisionName": "synthetic-paper-r1", "percent": 100}]
        }
    }, revision


def test_serving_revision_supplies_exact_plain_report_root_without_workflow_var(monkeypatch):
    service, revision = revision_response([
        {"name": "EXECUTION_REPORT_GCS_URI", "value": "gs://synthetic-bucket/runtime-reports"}
    ])
    monkeypatch.setattr(
        publisher.heartbeat,
        "_run_gcloud",
        lambda *_args: SimpleNamespace(returncode=0, stdout=json.dumps(revision)),
    )
    _, values, root = publisher._read_revision(
        service, service="synthetic-paper", project="synthetic-project", region="synthetic-region"
    )
    assert root == "gs://synthetic-bucket/runtime-reports"
    assert values["EXECUTION_REPORT_GCS_URI"] == root


def test_serving_revision_report_root_rejects_secretref_empty_duplicate_and_conflict(monkeypatch):
    fixtures_rows = [
        [{"name": "EXECUTION_REPORT_GCS_URI", "valueSource": {"secretKeyRef": {"secret": "redacted"}}}],
        [{"name": "EXECUTION_REPORT_GCS_URI", "value": ""}],
        [
            {"name": "EXECUTION_REPORT_GCS_URI", "value": "gs://synthetic-bucket/reports"},
            {"name": "RUNTIME_HEARTBEAT_GCS_URIS", "value": "gs://synthetic-bucket/reports"},
        ],
        [{"name": "EXECUTION_REPORT_GCS_URI", "value": "gs://bucket-a/reports,gs://bucket-b/reports"}],
    ]
    for rows in fixtures_rows:
        service, revision = revision_response(rows)
        monkeypatch.setattr(
            publisher.heartbeat,
            "_run_gcloud",
            lambda *_args, revision=revision: SimpleNamespace(returncode=0, stdout=json.dumps(revision)),
        )
        with pytest.raises(publisher.CollectionUnavailable, match="report_prefix_unavailable"):
            publisher._read_revision(
                service, service="synthetic-paper", project="synthetic-project", region="synthetic-region"
            )


def test_explicit_workflow_report_root_must_match_verified_serving_revision():
    source = {"report_root_uri": "gs://synthetic-bucket/runtime-reports"}
    assert publisher._report_root(source, {}) == source["report_root_uri"]
    with pytest.raises(publisher.CollectionUnavailable, match="report_prefix_conflict"):
        publisher._report_root(source, {"RUNTIME_HEARTBEAT_GCS_URIS": "gs://other-bucket/reports"})
    with pytest.raises(publisher.CollectionUnavailable, match="report_prefix_unavailable"):
        publisher._report_root(source, {"RUNTIME_HEARTBEAT_GCS_URIS": "gs://a/reports,gs://b/reports"})


def test_publish_uses_get_cursor_then_exact_post_ack_without_network(source_wiring, monkeypatch):
    source = source_wiring
    calls = []

    def fake_request(method, _url, *, token, binding_id, payload=None):
        calls.append((method, token, binding_id))
        if method == "GET":
            return checkpoint_response(source)
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        coverage = payload["targets"][0]["cycle_health"]["coverage"]
        response = checkpoint_response(source)
        response.update({
            "ok": True,
            "schema_version": "qsl_runtime_cycle_health_ack.v1",
            "result": "stored",
            "adopted": False,
            "no_order": True,
            "execution_authority_granted": False,
            "authority_revision": 2,
            "checkpoint_revision": 3,
            "observed_at": payload["computed_at"],
            "received_at": payload["computed_at"],
            "observation_sha256": hashlib.sha256(serialized).hexdigest(),
            "covered_through": coverage["through"],
            "coverage_complete": True,
            "coverage_incomplete_reason": None,
        })
        response["checkpoint"]["covered_through"] = coverage["through"]
        response["checkpoint"]["required_from"] = REQUIRED_FROM
        return response

    monkeypatch.setattr(publisher, "_qrs_request", fake_request)
    monkeypatch.setattr(publisher, "_report_root", lambda *_args: "gs://synthetic-bucket/reports")

    result = publisher.publish(env(), now=NOW)

    assert result == "stored_complete"
    assert calls == [
        ("GET", "synthetic-token", fixtures.f.CTX.source_binding_id),
        ("POST", "synthetic-token", fixtures.f.CTX.source_binding_id),
    ]


def test_uninitialized_admitted_source_can_store_first_complete_observation(source_wiring, monkeypatch):
    source = source_wiring

    def fake_request(method, _url, *, payload=None, **_kwargs):
        admitted = checkpoint_response(source)
        if method == "GET":
            admitted.update({
                "status": "uninitialized",
                "checkpoint_revision": 0,
                "observed_at": None,
                "observation_sha256": None,
                "received_at": None,
                "checkpoint": None,
                "covered_through": None,
                "coverage_complete": False,
                "coverage_incomplete_reason": None,
                "history_page": {"items": [], "snapshot_through_id": 0, "complete": True, "next_cursor": None},
                "prior_partitions": [],
                "continuity": "same_binding",
            })
            return admitted
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        coverage = payload["targets"][0]["cycle_health"]["coverage"]
        admitted.update({
            "ok": True,
            "schema_version": "qsl_runtime_cycle_health_ack.v1",
            "result": "stored",
            "adopted": False,
            "no_order": True,
            "execution_authority_granted": False,
            "authority_revision": 2,
            "checkpoint_revision": 1,
            "observed_at": payload["computed_at"],
            "received_at": payload["computed_at"],
            "observation_sha256": hashlib.sha256(serialized).hexdigest(),
            "covered_through": coverage["through"],
            "coverage_complete": True,
            "coverage_incomplete_reason": None,
        })
        admitted["checkpoint"]["covered_through"] = coverage["through"]
        admitted["checkpoint"]["required_from"] = REQUIRED_FROM
        return admitted

    monkeypatch.setattr(publisher, "_qrs_request", fake_request)
    monkeypatch.setattr(publisher, "_report_root", lambda *_args: "gs://synthetic-bucket/reports")
    assert publisher.publish(env(), now=NOW) == "stored_complete"


def test_missing_qrs_admission_stops_before_gcs_or_post(source_wiring, monkeypatch):
    calls = []
    monkeypatch.setattr(publisher, "_qrs_request", lambda *args, **kwargs: calls.append(args[0]) or {})
    monkeypatch.setattr(
        publisher,
        "_gcs_callbacks",
        lambda *_args: pytest.fail("GCS must not be read before QRS admission"),
    )
    with pytest.raises(SourceContractError):
        publisher.publish(env(), now=NOW)
    assert calls == ["GET"]


def test_historical_short_window_uses_current_observation_time_and_posts_chunk_end(source_wiring, monkeypatch):
    source = source_wiring
    calls = []
    chunk_end = NOW - dt.timedelta(seconds=1)

    def fake_request(method, _url, *, payload=None, **_kwargs):
        calls.append(method)
        response = checkpoint_response(source)
        if method == "GET":
            return response
        assert payload["generated_at"] == NOW.isoformat().replace("+00:00", "Z")
        assert payload["computed_at"] == NOW.isoformat().replace("+00:00", "Z")
        coverage = payload["targets"][0]["cycle_health"]["coverage"]
        assert coverage["through"] == chunk_end.isoformat().replace("+00:00", "Z")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        response.update({
            "ok": True,
            "schema_version": "qsl_runtime_cycle_health_ack.v1",
            "result": "stored",
            "adopted": False,
            "no_order": True,
            "execution_authority_granted": False,
            "authority_revision": 2,
            "checkpoint_revision": 3,
            "observed_at": payload["computed_at"],
            "received_at": payload["computed_at"],
            "observation_sha256": hashlib.sha256(serialized).hexdigest(),
            "covered_through": coverage["through"],
            "coverage_complete": True,
            "coverage_incomplete_reason": None,
        })
        response["checkpoint"]["covered_through"] = coverage["through"]
        response["checkpoint"]["required_from"] = REQUIRED_FROM
        return response

    monkeypatch.setattr(publisher, "_qrs_request", fake_request)
    monkeypatch.setattr(
        publisher,
        "bounded_cycle_window_end",
        lambda *_args, **_kwargs: (NOW - dt.timedelta(seconds=1), None),
    )
    monkeypatch.setattr(publisher, "_report_root", lambda *_args: "gs://synthetic-bucket/reports")
    assert publisher.publish(env(), now=NOW) == "stored_complete"
    assert calls == ["GET", "POST"]


def test_backfill_advances_in_bounded_chunks_with_current_observation_time(source_wiring, monkeypatch):
    source = source_wiring
    required_from = "2026-09-01T00:00:00Z"
    cursor = {"value": None}
    snapshots = []

    def fake_request(method, _url, *, payload=None, **_kwargs):
        response = checkpoint_response(source)
        response["required_from"] = required_from
        response["checkpoint"]["required_from"] = required_from
        response["covered_through"] = cursor["value"]
        response["checkpoint"]["covered_through"] = cursor["value"]
        if method == "GET":
            return response
        cycle_health = payload["targets"][0]["cycle_health"]
        snapshots.append((payload["computed_at"], cycle_health["coverage"]["from"], cycle_health["coverage"]["through"]))
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        response.update({
            "ok": True,
            "schema_version": "qsl_runtime_cycle_health_ack.v1",
            "result": "stored",
            "adopted": False,
            "no_order": True,
            "execution_authority_granted": False,
            "authority_revision": 2,
            "checkpoint_revision": len(snapshots) + 2,
            "observed_at": payload["computed_at"],
            "received_at": payload["computed_at"],
            "observation_sha256": hashlib.sha256(serialized).hexdigest(),
            "covered_through": cycle_health["coverage"]["through"],
            "coverage_complete": True,
            "coverage_incomplete_reason": None,
        })
        cursor["value"] = cycle_health["coverage"]["through"]
        response["checkpoint"]["covered_through"] = cursor["value"]
        return response

    def fake_collect(*, since, now, coverage_through, **_kwargs):
        from datetime import timezone
        def stamp(value):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

        return {"cycle_health": {
            "schema_version": "qsl_runtime_cycle_health.v1",
            "configuration_sha256": "a" * 64,
            "schedule": {},
            "coverage": {"from": stamp(since), "through": stamp(coverage_through), "complete": True},
            "cycles": [],
            "resolutions": [],
        }}

    monkeypatch.setattr(publisher, "_qrs_request", fake_request)
    monkeypatch.setattr(publisher, "collect_runtime_cycle_health", fake_collect)
    monkeypatch.setattr(publisher, "_report_root", lambda *_args: "gs://synthetic-bucket/reports")
    first_now = dt.datetime(2026, 10, 8, 16, 0, tzinfo=UTC)
    second_now = first_now + dt.timedelta(minutes=1)

    assert publisher.publish(env(), now=first_now) == "stored_complete"
    assert snapshots[0][0] == "2026-10-08T16:00:00Z"
    assert snapshots[0][2] < snapshots[0][0]
    assert cursor["value"] == snapshots[0][2]
    assert publisher.publish(env(), now=second_now) == "stored_complete"
    assert snapshots[1][0] == "2026-10-08T16:01:00Z"
    assert snapshots[1][1] <= snapshots[0][2]
    assert snapshots[1][2] > snapshots[0][2]
    assert snapshots[1][2] == "2026-10-08T16:01:00Z"


def test_incomplete_ack_is_stored_without_advancing_checkpoint(source_wiring, monkeypatch):
    source = source_wiring

    def fake_request(method, _url, *, token, binding_id, payload=None):
        if method == "GET":
            return checkpoint_response(source)
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        response = checkpoint_response(source)
        response.update({
            "ok": True,
            "schema_version": "qsl_runtime_cycle_health_ack.v1",
            "result": "stored",
            "adopted": False,
            "no_order": True,
            "execution_authority_granted": False,
            "authority_revision": 2,
            "checkpoint_revision": 3,
            "observed_at": payload["computed_at"],
            "received_at": payload["computed_at"],
            "observation_sha256": hashlib.sha256(serialized).hexdigest(),
            "covered_through": OLD_COVERED,
            "coverage_complete": False,
            "coverage_incomplete_reason": "coverage_gap",
        })
        response["checkpoint"]["covered_through"] = OLD_COVERED
        response["checkpoint"]["required_from"] = REQUIRED_FROM
        return response

    monkeypatch.setattr(publisher, "_qrs_request", fake_request)
    monkeypatch.setattr(publisher, "_report_root", lambda *_args: "gs://synthetic-bucket/reports")
    assert publisher.publish(env(), now=NOW) == "stored_incomplete"


def test_qrs_request_disables_redirects_and_caps_stream_before_full_body(monkeypatch):
    import requests

    class Response:
        status_code = 200
        closed = False

        def iter_content(self, chunk_size):
            assert chunk_size == 16 * 1024
            yield b"x" * (publisher._MAX_BODY_BYTES + 1)
            pytest.fail("body reader must stop as soon as the limit is crossed")

        def close(self):
            self.closed = True

    response = Response()
    captured = {}

    def request(*args, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(requests, "request", request)
    with pytest.raises(publisher.CollectionUnavailable, match="source_transport_unconfirmed"):
        publisher._qrs_request("GET", "https://qrs.example.test/api/internal/runtime-cycle-health-source",
                               token="synthetic-token", binding_id=fixtures.f.CTX.source_binding_id)
    assert captured["allow_redirects"] is False
    assert captured["stream"] is True
    assert response.closed is True


def test_qrs_request_rejects_redirect_without_following_it(monkeypatch):
    import requests

    class Response:
        status_code = 302
        closed = False

        def iter_content(self, _chunk_size=None, **_kwargs):
            pytest.fail("redirect body must not be consumed")

        def close(self):
            self.closed = True

    response = Response()
    captured = {}

    def request(*args, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(requests, "request", request)
    with pytest.raises(publisher.CollectionUnavailable, match="source_transport_unconfirmed"):
        publisher._qrs_request("GET", "https://qrs.example.test/api/internal/runtime-cycle-health-source",
                               token="synthetic-token", binding_id=fixtures.f.CTX.source_binding_id)
    assert captured["allow_redirects"] is False
    assert response.closed is True


def test_empty_gcs_listing_is_terminal_empty_page(monkeypatch):
    google = ModuleType("google")
    cloud = ModuleType("google.cloud")
    storage = ModuleType("google.cloud.storage")

    class Iterator:
        pages = iter(())
        next_page_token = None

    class Client:
        def __init__(self, project):
            assert project == "synthetic-project"

        def list_blobs(self, bucket_name, **kwargs):
            assert bucket_name == "synthetic-bucket"
            assert kwargs["start_offset"] == "prefix/20261007T000000Z"
            assert kwargs["end_offset"] == "prefix/20261008T000000Z"
            return Iterator()

    storage.Client = Client
    cloud.storage = storage
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage)
    list_range, _ = publisher._gcs_callbacks("synthetic-project", "synthetic-bucket")
    assert list_range("prefix/", None, "prefix/20261007T000000Z", "prefix/20261008T000000Z") == {
        "items": [],
        "nextPageToken": None,
    }
