from __future__ import annotations

import json
import socket
import urllib.request
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import inspect_hk_producer_metadata as metadata

REV1 = "longbridge-quant-hk-service-00001-one"
REV2 = "longbridge-quant-hk-service-00002-two"


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    assert urllib.request.HTTPRedirectHandler

    def fail(*args, **kwargs):
        raise AssertionError("external network/default operations forbidden")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    monkeypatch.setattr(socket, "create_connection", fail)
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, fail)


def service(revisions=((REV1, 100),)):
    rows = [
        {
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
            "revision": rev,
            "percent": percent,
        }
        for rev, percent in revisions
    ]
    return {
        "name": metadata.SERVICE_RESOURCE,
        "uid": "opaque-uid",
        "etag": "opaque-etag",
        "generation": "7",
        "observedGeneration": "7",
        "reconciling": False,
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"},
        "traffic": rows,
        "trafficStatuses": deepcopy(rows),
    }


def revision(name=REV1):
    return {
        "name": metadata.SERVICE_RESOURCE + "/revisions/" + name,
        "service": metadata.SERVICE_RESOURCE,
        "uid": "revision-uid",
        "etag": "revision-etag",
        "generation": "1",
        "observedGeneration": "1",
        "reconciling": False,
        "serviceAccount": metadata.RUNTIME_WRITER,
        "containers": [
            {
                "image": (
                    "asia-east2-docker.pkg.dev/longbridgequant/"
                    "cloud-run-source-deploy/app@sha256:"
                )
                + "a" * 64,
                "env": [
                    {"name": "ACCOUNT_HISTORY_RECORDING_ENABLED"},
                    {"name": "PRIVATE_OTHER_NAME"},
                ],
            }
        ],
    }


def scheduler():
    return {
        "name": metadata.SCHEDULER_RESOURCE,
        "state": "PAUSED",
        "lastAttemptTime": "2026-10-02T10:00:00Z",
        "status": {"code": 0},
        "userUpdateTime": "2026-10-02T11:00:00Z",
    }


class Response:
    def __init__(self, body, url, *, status=200, redirect=False):
        self.raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.url = url
        self.status_code = status
        self.is_redirect = redirect
        self.history = []
        self.closed = False
        self.body_reads = 0
        self.decoded_bytes_read = 0
        self.headers = {"Content-Type": "application/json"}

    def iter_content(self, chunk_size):
        self.body_reads += 1
        for start in range(0, len(self.raw), chunk_size):
            chunk = self.raw[start : start + chunk_size]
            self.decoded_bytes_read += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


class Session:
    def __init__(
        self,
        *,
        first=None,
        recheck=None,
        revision_overrides=None,
        scheduler_value=None,
        status=200,
    ):
        self.first = service() if first is None else first
        self.recheck = deepcopy(self.first) if recheck is None else recheck
        self.revision_overrides = revision_overrides or {}
        self.scheduler_value = (
            scheduler() if scheduler_value is None else scheduler_value
        )
        self.status = status
        self.calls = []
        self.responses = []

    def request(self, method, url, **kwargs):
        assert (
            method == "GET"
            and kwargs["allow_redirects"] is False
            and kwargs["stream"] is True
        )
        assert 0 < kwargs["timeout"] <= 15
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        assert set(query) == {"fields", "prettyPrint"}
        assert query["prettyPrint"] == ["false"]
        self.calls.append((parts.netloc, parts.path, query["fields"][0]))
        if parts.path == "/v2/" + metadata.SERVICE_RESOURCE:
            body = (
                self.first
                if sum(call[1] == parts.path for call in self.calls) == 1
                else self.recheck
            )
            assert query["fields"] == [metadata.SERVICE_FIELDS]
        elif "/revisions/" in parts.path:
            name = parts.path.rsplit("/", 1)[-1]
            body = self.revision_overrides.get(name, revision(name))
            assert query["fields"] == [metadata.REVISION_FIELDS]
        else:
            assert parts.path == "/v1/" + metadata.SCHEDULER_RESOURCE
            body = self.scheduler_value
            assert query["fields"] == [metadata.SCHEDULER_FIELDS]
        response = Response(body, url, status=self.status)
        self.responses.append(response)
        return response


def inspect(session, **kwargs):
    return metadata.inspect_hk_producer_metadata(
        session=session, monotonic=lambda: 0, **kwargs
    )


def test_names_only_current_serving_metadata_and_fixed_scheduler():
    session = Session()
    result = inspect(session)
    assert len(session.calls) == 4
    assert result["scheduler"]["state"] == "PAUSED"
    row = result["serving_revisions"][0]
    assert row["containers"][0]["immutable_image_digest"] == "a" * 64
    assert (
        row["containers"][0]["archive_flag_presence"][
            "ACCOUNT_HISTORY_RECORDING_ENABLED"
        ]
        is True
    )
    assert row["runtime_writer_matches_configured"] is True
    assert result["archive_flag_values_confirmed"] is False
    assert "PRIVATE_OTHER_NAME" not in json.dumps(result)
    assert all(response.closed for response in session.responses)


def test_two_positive_revisions_are_bounded_to_five_gets():
    session = Session(first=service(((REV1, 75), (REV2, 25))))
    result = inspect(session)
    assert len(result["serving_revisions"]) == 2 and len(session.calls) == 5


def test_changed_traffic_race_recheck_rejects_snapshot():
    session = Session(recheck=service(((REV2, 100),)))
    with pytest.raises(metadata.MetadataRejected, match="metadata_service_changed"):
        inspect(session)
    assert len(session.calls) == 4


def test_denial_stops_before_any_follow_up_or_body_read():
    session = Session(status=403)
    with pytest.raises(metadata.MetadataRejected) as caught:
        inspect(session)
    assert caught.value.category == "metadata_http_denied"
    assert caught.value.http_status == 403
    assert len(session.calls) == 1 and session.responses[0].body_reads == 0


@pytest.mark.parametrize("percent", [-1, 101, True, "100", None, float("nan"), [100]])
def test_malformed_percent_fails_before_revision_reads(percent):
    body = service()
    body["trafficStatuses"][0]["percent"] = percent
    session = Session(first=body)
    with pytest.raises(
        metadata.MetadataRejected,
        match="metadata_schema_invalid"
        if isinstance(percent, float)
        else "metadata_traffic_invalid",
    ):
        inspect(session)
    assert len(session.calls) == 1


def test_more_than_two_serving_revisions_is_unknown_not_partial():
    session = Session(
        first=service(
            ((REV1, 50), (REV2, 25), ("longbridge-quant-hk-service-00003-three", 25))
        )
    )
    with pytest.raises(metadata.MetadataRejected, match="metadata_traffic_invalid"):
        inspect(session)
    assert len(session.calls) == 1


@pytest.mark.parametrize("value", [None, [], 0])
def test_malformed_revision_reference_has_zero_revision_reads(value):
    body = service()
    body["trafficStatuses"][0]["revision"] = value
    session = Session(first=body)
    with pytest.raises(metadata.MetadataRejected, match="metadata_traffic_invalid"):
        inspect(session)
    assert len(session.calls) == 1


@pytest.mark.parametrize("change", ["generation", "uid", "etag"])
def test_source_identity_race_recheck_is_fail_closed(change):
    body = service()
    body[change] = "8" if change == "generation" else "changed"
    if change == "generation":
        body["observedGeneration"] = "8"
    session = Session(recheck=body)
    with pytest.raises(metadata.MetadataRejected, match="metadata_service_changed"):
        inspect(session)
    assert len(session.calls) == 4


@pytest.mark.parametrize(
    "key,value",
    [
        ("reconciling", True),
        ("observedGeneration", "6"),
        ("terminalCondition", {"state": "CONDITION_FAILED"}),
    ],
)
def test_unstable_service_never_follows_source_references(key, value):
    body = service()
    body[key] = value
    session = Session(first=body)
    with pytest.raises(metadata.MetadataRejected, match="metadata_service_unstable"):
        inspect(session)
    assert len(session.calls) == 1


def test_default_false_proto_omission_does_not_require_output_defaults():
    first = service()
    del first["reconciling"]
    rev = revision()
    del rev["reconciling"]
    session = Session(first=first, revision_overrides={REV1: rev})
    assert inspect(session)["serving_configuration_consistent"] is True


def test_current_template_is_never_requested_or_used_as_serving_source():
    session = Session()
    inspect(session)
    for _host, _path, mask in session.calls:
        assert "template" not in mask and "latestCreatedRevision" not in mask
    assert any(REV1 in path for _host, path, _mask in session.calls)


def test_unknown_service_resource_stops_without_follow_up():
    body = service()
    body["name"] = "projects/other/locations/asia-east2/services/other"
    session = Session(first=body)
    with pytest.raises(metadata.MetadataRejected, match="metadata_resource_invalid"):
        inspect(session)
    assert len(session.calls) == 1


def test_foreign_revision_resource_is_not_followed():
    body = service()
    body["trafficStatuses"][0]["revision"] = (
        "projects/other/locations/asia-east2/services/other/revisions/private"
    )
    session = Session(first=body)
    with pytest.raises(metadata.MetadataRejected, match="metadata_resource_invalid"):
        inspect(session)
    assert len(session.calls) == 1


@pytest.mark.parametrize("which", ["name", "service"])
def test_revision_mismatch_stops_before_scheduler(which):
    body = revision()
    body[which] = "projects/other/private"
    session = Session(revision_overrides={REV1: body})
    with pytest.raises(metadata.MetadataRejected, match="metadata_resource_invalid"):
        inspect(session)
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"value": "PRIVATE_SECRET"},
        {"valueSource": {"secretKeyRef": {"secret": "PRIVATE"}}},
    ],
)
def test_env_value_or_secret_reference_is_rejected_without_output(extra):
    body = revision()
    body["containers"][0]["env"][0].update(extra)
    session = Session(revision_overrides={REV1: body})
    with pytest.raises(
        metadata.MetadataRejected, match="metadata_schema_invalid"
    ) as caught:
        inspect(session)
    assert "PRIVATE" not in str(caught.value)
    assert len(session.calls) == 2


def test_all_env_names_are_not_dumped_and_value_stays_unknown():
    body = revision()
    body["containers"][0]["env"] = [{"name": "PRIVATE_UNUSED"}]
    session = Session(revision_overrides={REV1: body})
    result = inspect(session)
    row = result["serving_revisions"][0]["containers"][0]
    assert all(value is False for value in row["archive_flag_presence"].values())
    assert row["archive_flag_values_confirmed"] is False
    assert "PRIVATE_UNUSED" not in json.dumps(result)


def test_tagged_image_only_returns_reference_hash_and_unknown_source_sha():
    body = revision()
    body["containers"][0]["image"] = "private.registry.invalid/private:tag"
    session = Session(revision_overrides={REV1: body})
    row = inspect(session)["serving_revisions"][0]["containers"][0]
    assert row["immutable_image_digest"] is None and row["source_commit_sha"] is None
    assert len(row["image_reference_sha256"]) == 64
    assert "private.registry" not in json.dumps(row)


def test_writer_match_is_config_only_not_storage_permission():
    body = revision()
    body["serviceAccount"] = "unknown-private-principal@example.invalid"
    result = inspect(Session(revision_overrides={REV1: body}))
    assert result["serving_revisions"][0]["runtime_writer_matches_configured"] is False
    assert result["runtime_writer_storage_permission_confirmed"] is False
    assert "unknown-private-principal" not in json.dumps(result)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_numeric_http_failures_no_retry_or_response_body(status):
    session = Session(status=status)
    with pytest.raises(metadata.MetadataRejected) as caught:
        inspect(session)
    assert caught.value.http_status == status
    assert len(session.calls) == 1 and session.responses[0].body_reads == 0
    assert session.responses[0].closed


@pytest.mark.parametrize("change", ["redirect", "url", "history"])
def test_redirect_and_unexpected_endpoint_is_rejected(change):
    session = Session()
    original = session.request

    def request(*args, **kwargs):
        response = original(*args, **kwargs)
        if change == "redirect":
            response.is_redirect = True
        elif change == "url":
            response.url = "https://other.invalid/private"
        else:
            response.history = [object()]
        return response

    session.request = request
    with pytest.raises(metadata.MetadataRejected, match="metadata_redirect_rejected"):
        inspect(session)
    assert len(session.calls) == 1 and session.responses[0].body_reads == 0


@pytest.mark.parametrize(
    "body,category",
    [
        (b"x" * 65537, "metadata_byte_limit"),
        (b"[]", "metadata_schema_invalid"),
        (b'{"name":"one","name":"two"}', "metadata_schema_invalid"),
        (b'{"nextPageToken":"PRIVATE"}', "metadata_pagination_rejected"),
        (b'{"number":NaN}', "metadata_schema_invalid"),
    ],
)
def test_bounded_stream_and_unknown_coverage_are_not_accepted(body, category):
    session = Session(first=body)
    with pytest.raises(metadata.MetadataRejected, match=category):
        inspect(session)
    assert len(session.calls) == 1 and session.responses[0].closed


def test_total_response_byte_budget_is_independent_of_per_response(monkeypatch):
    monkeypatch.setattr(metadata, "MAX_TOTAL_BYTES", 10)
    session = Session()
    with pytest.raises(metadata.MetadataRejected, match="metadata_byte_limit"):
        inspect(session)
    assert len(session.calls) == 1


@pytest.mark.parametrize("limit", [10, 65536])
def test_stream_overflow_reads_only_one_decoded_sentinel_byte(monkeypatch, limit):
    monkeypatch.setattr(metadata, "MAX_TOTAL_BYTES", limit)
    session = Session(first=b"x" * (limit + 4096))
    with pytest.raises(metadata.MetadataRejected, match="metadata_byte_limit"):
        inspect(session)
    assert session.responses[0].decoded_bytes_read == limit + 1
    assert len(session.calls) == 1 and session.responses[0].closed


def test_output_limit_is_fail_closed(monkeypatch):
    monkeypatch.setattr(metadata, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(metadata.MetadataRejected, match="metadata_output_limit"):
        inspect(Session())


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "clock"])
def test_unknown_clock_has_zero_requests(value):
    session = Session()
    with pytest.raises(metadata.MetadataRejected, match="metadata_deadline"):
        metadata.inspect_hk_producer_metadata(session=session, monotonic=lambda: value)
    assert session.calls == []


def test_total_deadline_after_first_reply_stops_before_revision():
    values = iter([0, 0, 181])
    session = Session()
    with pytest.raises(metadata.MetadataRejected, match="metadata_deadline"):
        metadata.inspect_hk_producer_metadata(
            session=session, monotonic=lambda: next(values)
        )
    assert len(session.calls) == 1


def test_wrong_scheduler_identity_does_not_perform_service_recheck():
    body = scheduler()
    body["name"] = "projects/other/jobs/other"
    session = Session(scheduler_value=body)
    with pytest.raises(metadata.MetadataRejected, match="metadata_resource_invalid"):
        inspect(session)
    assert len(session.calls) == 3


def test_metadata_is_never_current_health_receipt_or_broker_identity():
    result = inspect(Session())
    for key in (
        "metadata_snapshot_atomic",
        "archive_flag_values_confirmed",
        "runtime_writer_storage_permission_confirmed",
        "current_health_confirmed",
        "request_terminal_confirmed",
        "receiver_ack_confirmed",
        "native_identity_confirmed",
    ):
        assert result[key] is False
    assert result["scheduler"]["historical_oct2_invocation_confirmed"] is False


@pytest.mark.parametrize("side", ["desired", "actual"])
def test_total_traffic_percent_must_equal_100(side):
    body = service()
    body["traffic" if side == "desired" else "trafficStatuses"][0]["percent"] = 99
    session = Session(first=body)
    with pytest.raises(metadata.MetadataRejected, match="metadata_traffic_invalid"):
        inspect(session)
    assert len(session.calls) == 1


def test_latest_allocation_uses_resolved_actual_revision_without_template():
    body = service(((REV1, 75), (REV2, 25)))
    body["traffic"][0] = {
        "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST",
        "percent": 75,
    }
    body["trafficStatuses"][0]["type"] = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    assert len(inspect(Session(first=body))["serving_revisions"]) == 2


@pytest.mark.parametrize("kind", ["value", "key", "duplicate"])
def test_unexpected_or_duplicate_env_names_are_rejected(kind):
    body = revision()
    if kind == "duplicate":
        body["containers"][0]["env"].append(
            {"name": "ACCOUNT_HISTORY_RECORDING_ENABLED"}
        )
    elif kind == "value":
        body["containers"][0]["env"][0]["name"] = None
    else:
        body["containers"][0]["env"][0]["unexpected"] = "PRIVATE"
    with pytest.raises(metadata.MetadataRejected, match="metadata_schema_invalid"):
        inspect(Session(revision_overrides={REV1: body}))


def test_request_counter_prevents_a_sixth_call():
    budget = metadata._Budget(lambda: 0)
    budget.calls = 5
    session = Session()
    with pytest.raises(metadata.MetadataRejected, match="metadata_request_limit"):
        budget.get(session, metadata.SERVICE_RESOURCE, metadata.SERVICE_FIELDS)
    assert session.calls == []


def test_transport_exception_does_not_trigger_followup_or_expose_message():
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("PRIVATE TOKEN URL")

    with pytest.raises(
        metadata.MetadataRejected, match="metadata_transport_failed"
    ) as caught:
        inspect(SimpleNamespace(request=request))
    assert calls == [1] and "PRIVATE" not in str(caught.value)


def embedded_scope(label):
    import ast
    import textwrap
    from pathlib import Path

    text = Path(".github/workflows/inspect-hk-producer-metadata.yml").read_text()
    raw = text.split("          # BEGIN_" + label + "\n", 1)[1].split(
        "          # END_" + label, 1
    )[0]
    tree = ast.parse(textwrap.dedent(raw))
    tree.body = [node for node in tree.body if not isinstance(node, ast.If)]
    namespace = {"__name__": "synthetic_metadata_contract"}
    exec(compile(tree, label, "exec"), namespace)
    return namespace


def preflight_env(tmp_path):

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"expected_sha": "a" * 40}}))
    return {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": (
            "QuantStrategyLab/LongBridgePlatform/.github/workflows/"
            "inspect-hk-producer-metadata.yml@refs/heads/main"
        ),
        "GITHUB_SHA": "a" * 40,
        "GITHUB_WORKFLOW_SHA": "a" * 40,
        "GITHUB_EVENT_PATH": str(event),
        "GCP_WORKLOAD_IDENTITY_PROVIDER": (
            "projects/252919773759/locations/global/workloadIdentityPools/"
            "github-actions/providers/github-main"
        ),
        "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT": metadata.OBSERVER,
    }


def test_workflow_is_manual_fixed_observer_and_pre_auth_source_gated():
    from pathlib import Path

    text = Path(".github/workflows/inspect-hk-producer-metadata.yml").read_text()
    trigger = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in trigger and trigger.count("required: true") == 1
    for forbidden in ("push:", "schedule:", "pull_request:", "workflow_run:"):
        assert forbidden not in trigger
    for forbidden in (
        "secrets.",
        "vars.",
        "gcloud",
        "upload-artifact",
        "write-all",
        "credentials_json:",
    ):
        assert forbidden not in text
    assert "environment: longbridge-hk" in text
    assert "max_refresh_attempts=0" in text
    assert (
        "cloud-platform" in text
        and "google.auth.default" not in text
        and "load_credentials_from_file" not in text
    )
    assert "175s" in text and "--kill-after=5s" in text
    assert (
        text.index("id: preflight")
        < text.index("uses: actions/checkout@")
        < text.index("id: checked-source")
        < text.index("uses: google-github-actions/auth@")
    )
    for line in text.splitlines():
        if "uses:" in line:
            assert len(line.split("@", 1)[1].strip()) == 40


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_REF", "refs/heads/other"),
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_SHA", "b" * 40),
        ("GITHUB_WORKFLOW_SHA", "b" * 40),
        ("GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT", "other"),
        ("GITHUB_WORKFLOW_REF", "other"),
    ],
)
def test_unsupported_workflow_source_has_zero_metadata_or_auth_calls(
    tmp_path, key, value
):
    from datetime import datetime, timezone

    p = embedded_scope("METADATA_PREFLIGHT")
    values = preflight_env(tmp_path)
    values[key] = value
    calls = []
    with pytest.raises(p["PreflightError"]):
        p["validate_pre_auth"](
            values,
            datetime.now(timezone.utc),
            lambda path: calls.append(path),
            checkout_sha="a" * 40,
        )
    assert calls == []


def test_actual_checkout_and_current_main_must_both_match_expected(tmp_path):
    from datetime import datetime, timezone

    p = embedded_scope("METADATA_PREFLIGHT")
    values = preflight_env(tmp_path)
    calls = []
    with pytest.raises(p["PreflightError"], match="preauth_checkout_invalid"):
        p["validate_pre_auth"](
            values,
            datetime.now(timezone.utc),
            lambda path: calls.append(path),
            checkout_sha="b" * 40,
        )
    assert calls == []
    with pytest.raises(p["PreflightError"], match="preauth_main_changed"):
        p["validate_pre_auth"](
            values,
            datetime.now(timezone.utc),
            lambda path: {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": "b" * 40},
            },
            checkout_sha="a" * 40,
        )


def test_workflow_credential_file_missing_or_mismatched_never_uses_adc():
    p = embedded_scope("METADATA_LAUNCH")
    calls = []
    with pytest.raises(
        p["MetadataLaunchError"], match="metadata_credentials_binding_invalid"
    ):
        p["make_metadata_session"](
            {},
            lambda *args, **kwargs: calls.append("session"),
            lambda *args, **kwargs: calls.append("credentials"),
        )
    assert calls == []


def wif_values(tmp_path, *, info_change=None):
    p = embedded_scope("METADATA_LAUNCH")
    info = {
        "type": "external_account",
        "audience": "//iam.googleapis.com/" + p["PROVIDER"],
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
        + p["SERVICE_ACCOUNT"]
        + ":generateAccessToken",
        "credential_source": {
            "url": "https://token.actions.githubusercontent.com/oidc?api-version=2.0&audience=https%3A%2F%2Fiam.googleapis.com%2F"
            + p["PROVIDER"].replace("/", "%2F"),
            "headers": {"Authorization": "Bearer synthetic-not-real"},
            "format": {"type": "json", "subject_token_field_name": "value"},
        },
    }
    if info_change:
        info.update(info_change)
    path = tmp_path / "official-wif.json"
    path.write_text(json.dumps(info))
    values = {
        "HK_APPROVED_CREDENTIALS_FILE": str(path),
        "GOOGLE_APPLICATION_CREDENTIALS": str(path),
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.githubusercontent.com/oidc?api-version=2.0",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-not-real",
    }
    return p, values


def test_native_constructor_has_zero_refresh_project_discovery_or_network(
    monkeypatch, tmp_path
):
    auth = pytest.importorskip("google.auth")
    external = pytest.importorskip("google.auth.external_account")
    identity = pytest.importorskip("google.auth.identity_pool")
    transport = pytest.importorskip("google.auth.transport.requests")
    assert auth.__version__ == "2.55.1"
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("constructor performed a forbidden external/default call")

    monkeypatch.setattr(auth, "default", forbidden)
    monkeypatch.setattr(external.Credentials, "get_project_id", forbidden)
    monkeypatch.setattr(external.Credentials, "refresh", forbidden)
    monkeypatch.setattr(identity.Credentials, "refresh", forbidden)
    p, values = wif_values(tmp_path)
    session = p["make_metadata_session"](
        values, transport.AuthorizedSession, identity.Credentials.from_info
    )
    try:
        assert session._max_refresh_attempts == 0 and session._refresh_timeout == 15
        assert session.credentials.token is None
        assert hasattr(session.credentials, "_rab_manager")
        assert session.credentials.scopes == [
            "https://www.googleapis.com/auth/cloud-platform"
        ]
        assert calls == []
    finally:
        session.close()


@pytest.mark.parametrize(
    "change",
    [
        {"audience": "other"},
        {"token_url": "https://other.invalid"},
        {"service_account_impersonation_url": "https://other.invalid/private"},
        {"credential_source": {"file": "/PRIVATE"}},
    ],
)
def test_wrong_wif_identity_or_source_has_zero_constructor_calls(tmp_path, change):
    p, values = wif_values(tmp_path, info_change=change)
    calls = []
    with pytest.raises(
        p["MetadataLaunchError"], match="metadata_credentials_binding_invalid"
    ):
        p["make_metadata_session"](
            values,
            lambda *args, **kwargs: calls.append("session"),
            lambda *args, **kwargs: calls.append("credentials"),
        )
    assert calls == []


def test_native_authorized_session_uses_stub_http_interface():
    import io

    requests = pytest.importorskip("requests")
    transport = pytest.importorskip("google.auth.transport.requests")
    credentials = pytest.importorskip("google.auth.credentials")
    stub = Session()

    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            response = stub.request(
                request.method,
                request.url,
                timeout=kwargs["timeout"],
                allow_redirects=False,
                stream=True,
            )
            native = requests.Response()
            native.status_code = response.status_code
            native.url = request.url
            native.request = request
            native.headers["Content-Type"] = "application/json"
            native.raw = io.BytesIO(response.raw)
            return native

        def close(self):
            pass

    session = transport.AuthorizedSession(
        credentials.AnonymousCredentials(), max_refresh_attempts=0, refresh_timeout=15
    )
    session.mount("https://run.googleapis.com/", Adapter())
    session.mount("https://cloudscheduler.googleapis.com/", Adapter())
    try:
        result = inspect(session)
        assert result["metadata_get_count"] == 4 and len(stub.calls) == 4
    finally:
        session.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"inputs": {"expected_sha": "a" * 40, "service": "PRIVATE"}},
        {"inputs": None},
        {"inputs": {"expected_sha": "main"}},
    ],
)
def test_malformed_or_extra_dispatch_input_has_zero_metadata_reads(tmp_path, payload):
    from datetime import datetime, timezone
    from pathlib import Path

    p = embedded_scope("METADATA_PREFLIGHT")
    values = preflight_env(tmp_path)
    Path(values["GITHUB_EVENT_PATH"]).write_text(json.dumps(payload))
    calls = []
    with pytest.raises(p["PreflightError"]):
        p["validate_pre_auth"](
            values, datetime.now(timezone.utc), lambda path: calls.append(path)
        )
    assert calls == []


def test_all_new_files_have_no_default_operations_or_broker_calls():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("scripts/inspect_hk_producer_metadata.py").read_text())
    assert not any(
        isinstance(node, ast.Import)
        and any(
            alias.name in {"os", "google.auth", "subprocess"} for alias in node.names
        )
        for node in ast.walk(tree)
    )
    assert "def main" not in Path("scripts/inspect_hk_producer_metadata.py").read_text()
    text = Path(".github/workflows/inspect-hk-producer-metadata.yml").read_text()
    for forbidden in (
        "/probe",
        "/run",
        "account_balance",
        "jobs:run",
        "jobs:resume",
        "jobs:pause",
        "get_project_id(",
    ):
        assert forbidden not in text


@pytest.mark.parametrize(
    "status", ["ENABLED", "PAUSED", "DISABLED", "UPDATE_FAILED", "STATE_UNSPECIFIED"]
)
def test_scheduler_state_is_metadata_only_without_an_invocation_claim(status):
    body = scheduler()
    body["state"] = status
    result = inspect(Session(scheduler_value=body))
    assert result["scheduler"]["state"] == status
    assert result["scheduler"]["historical_oct2_invocation_confirmed"] is False


@pytest.mark.parametrize(
    "case,stage,slot,requests,numeric,suffix,short_parent",
    [
        ("service", "service_initial", "service_name", 1, True, True, False),
        ("traffic", "service_initial", "traffic_revision", 1, True, True, False),
        ("revision", "revision_1", "revision_name", 2, True, True, False),
        ("parent", "revision_1", "revision_parent", 2, False, False, True),
        ("scheduler", "scheduler", "scheduler_name", 3, True, True, False),
        ("recheck", "service_recheck", "service_name", 4, True, True, False),
    ],
)
def test_resource_aliases_remain_rejected_with_fixed_stage_and_shape(
    case, stage, slot, requests, numeric, suffix, short_parent
):
    first, last, rev, job = service(), service(), revision(), scheduler()

    def alias(value):
        return value.replace("projects/longbridgequant/", "projects/123456789/")

    if case == "service":
        first["name"] = alias(first["name"])
    elif case == "traffic":
        first["trafficStatuses"][0]["revision"] = alias(rev["name"])
    elif case == "revision":
        rev["name"] = alias(rev["name"])
    elif case == "parent":
        rev["service"] = "longbridge-quant-hk-service"
    elif case == "scheduler":
        job["name"] = alias(job["name"])
    else:
        last["name"] = alias(last["name"])
    session = Session(
        first=first,
        recheck=last,
        revision_overrides={REV1: rev},
        scheduler_value=job,
    )
    with pytest.raises(
        metadata.MetadataRejected, match="metadata_resource_invalid"
    ) as caught:
        inspect(session)
    error = caught.value
    assert error.stage == stage and error.resource_slot == slot
    assert error.resource_shape == {
        "configured_project_id_matches": False,
        "project_segment_is_numeric": numeric,
        "fixed_location_and_resource_suffix_matches": suffix,
        "short_expected_service_parent_matches": short_parent,
    }
    assert len(session.calls) == requests
    assert "123456789" not in str(error)


@pytest.mark.parametrize("name", ["PRIVATE TOKEN", None, [], "x" * 513])
def test_malformed_resource_context_is_only_fixed_booleans(name):
    first = service()
    first["name"] = name
    session = Session(first=first)
    with pytest.raises(metadata.MetadataRejected) as caught:
        inspect(session)
    error = caught.value
    assert error.stage == "service_initial"
    assert error.resource_slot == "service_name"
    assert all(
        type(value) is bool and value is False
        for value in error.resource_shape.values()
    )
    assert len(session.calls) == 1 and "PRIVATE" not in str(error)


def test_http_denial_has_planned_stage_but_no_resource_claim():
    session = Session(status=403)
    with pytest.raises(metadata.MetadataRejected) as caught:
        inspect(session)
    error = caught.value
    assert error.stage == "service_initial" and error.http_status == 403
    assert error.resource_slot is None and error.resource_shape is None
    assert len(session.calls) == 1 and session.responses[0].body_reads == 0


@pytest.mark.parametrize(
    "malformed", [False, "stage", "slot", "shape", "shape_int", "shape_list"]
)
def test_launcher_emits_only_allowlisted_reader_context(monkeypatch, capsys, malformed):
    pytest.importorskip("google.auth.transport.requests")
    p = embedded_scope("METADATA_LAUNCH")
    error = metadata.MetadataRejected("metadata_resource_invalid")
    error.stage = "service_initial"
    error.resource_slot = "service_name"
    error.resource_shape = {
        "configured_project_id_matches": False,
        "project_segment_is_numeric": True,
        "fixed_location_and_resource_suffix_matches": True,
        "short_expected_service_parent_matches": False,
    }
    if malformed == "stage":
        error.stage = ["PRIVATE STAGE"]
    elif malformed == "slot":
        error.resource_slot = "PRIVATE SLOT"
    elif malformed == "shape":
        error.resource_shape["raw_resource"] = "PRIVATE RESOURCE"
    elif malformed == "shape_int":
        error.resource_shape["project_segment_is_numeric"] = 1
    elif malformed == "shape_list":
        error.resource_shape = ["PRIVATE RESOURCE"]

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(metadata, "inspect_hk_producer_metadata", fail)
    p["make_metadata_session"] = lambda *args: SimpleNamespace(close=lambda: None)
    assert p["main"]() == 1
    raw = capsys.readouterr().out
    value = json.loads(raw.split("hk_producer_metadata_error:", 1)[1])
    assert set(value) == {
        "category",
        "http_status",
        "stage",
        "resource_slot",
        "resource_shape",
    }
    assert (
        value["category"] == "metadata_resource_invalid"
        and value["http_status"] is None
    )
    assert value["stage"] == (None if malformed == "stage" else "service_initial")
    assert value["resource_slot"] == (None if malformed == "slot" else "service_name")
    assert value["resource_shape"] == (
        None
        if malformed in {"shape", "shape_int", "shape_list", "slot"}
        else error.resource_shape
    )
    assert "PRIVATE" not in raw


@pytest.mark.parametrize(
    "at,stage",
    [
        (1, "service_initial"),
        (2, "revision_1"),
        (3, "revision_2"),
        (4, "scheduler"),
        (5, "service_recheck"),
    ],
)
def test_all_planned_stages_stop_on_denial_without_followup(at, stage):
    session = Session(first=service(((REV1, 75), (REV2, 25))))
    original = session.request

    def request(*args, **kwargs):
        response = original(*args, **kwargs)
        if len(session.calls) == at:
            response.status_code = 403
        return response

    session.request = request
    with pytest.raises(metadata.MetadataRejected) as caught:
        inspect(session)
    assert caught.value.category == "metadata_http_denied"
    assert caught.value.stage == stage and caught.value.http_status == 403
    assert caught.value.resource_slot is None and caught.value.resource_shape is None
    assert len(session.calls) == at and session.responses[-1].body_reads == 0


def test_second_revision_mismatch_retains_exact_stage_and_no_followup():
    wrong = revision(REV2)
    wrong["name"] = wrong["name"].replace(
        "projects/longbridgequant/", "projects/123456789/"
    )
    session = Session(
        first=service(((REV1, 75), (REV2, 25))), revision_overrides={REV2: wrong}
    )
    with pytest.raises(metadata.MetadataRejected) as caught:
        inspect(session)
    assert caught.value.stage == "revision_2"
    assert caught.value.resource_slot == "revision_name"
    assert caught.value.resource_shape["project_segment_is_numeric"] is True
    assert len(session.calls) == 3
