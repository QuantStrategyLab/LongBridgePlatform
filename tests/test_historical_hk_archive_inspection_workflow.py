from __future__ import annotations

import ast
import hashlib
import json
import socket
import tempfile
import urllib.request  # preload SSL before socket guard
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

WORKFLOW = Path(".github/workflows/inspect-historical-hk-account-archive.yml")
SHA = "b" * 40
BINDING = "a" * 64
BINDING_HASH = hashlib.sha256(BINDING.encode()).hexdigest()
NOW = datetime(2026, 10, 4, 19, tzinfo=timezone.utc)
TEST_ROOT = None


def block(name):
    text = WORKFLOW.read_text()
    raw = text.split("# BEGIN_" + name + "\n", 1)[1].split(
        "          # END_" + name, 1
    )[0]
    return "\n".join(
        line[10:] if line.startswith(" " * 10) else line for line in raw.splitlines()
    )


def scope(name):
    assert urllib.request.HTTPRedirectHandler
    tree = ast.parse(block(name))
    tree.body = [node for node in tree.body if not isinstance(node, ast.If)]
    result = {"__name__": "synthetic_hk_contract"}
    exec(compile(tree, str(WORKFLOW) + ":" + name, "exec"), result)
    result["BINDING_SHA256"] = BINDING_HASH
    return result


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    global TEST_ROOT
    TEST_ROOT = tmp_path

    def fail(*_args, **_kwargs):
        raise AssertionError("network forbidden in contract tests")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)
    monkeypatch.setattr(socket.socket, "sendto", fail)
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def event_inputs():
    return {
        "expected_sha": SHA,
        "inspection_date": "2026-10-02",
        "expected_source_binding_id": BINDING,
    }


def env():
    with tempfile.NamedTemporaryFile(dir=TEST_ROOT, delete=False) as event:
        event.write(json.dumps({"inputs": event_inputs()}).encode())
    return {
        "GITHUB_EVENT_PATH": event.name,
        "HK_CONFIG_BINDING_MATCH": "true",
        "HK_CONFIG_PREFIX_MATCH": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": (
            "QuantStrategyLab/LongBridgePlatform/.github/workflows/"
            "inspect-historical-hk-account-archive.yml@refs/heads/main"
        ),
        "GITHUB_SHA": SHA,
        "GITHUB_WORKFLOW_SHA": SHA,
        "GCP_WORKLOAD_IDENTITY_PROVIDER": (
            "projects/252919773759/locations/global/workloadIdentityPools/"
            "github-actions/providers/github-main"
        ),
        "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT": (
            "longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com"
        ),
        "HK_CHECKED_OUT_SHA": SHA,
    }


def fetcher(program, calls, **overrides):
    values = {
        program["MAIN_PATH"]: {
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": SHA},
        },
    }
    values.update(overrides)

    def fetch(path):
        calls.append(path)
        value = values[path]
        if isinstance(value, Exception):
            raise value
        return deepcopy(value)

    return fetch


def history_result():
    return {
        "evidence_kind": "historical_hk_archive_inspection",
        "target": "hk",
        "current_health_confirmed": False,
        "request_terminal_confirmed": False,
        "receiver_ack_confirmed": False,
        "native_identity_confirmed": False,
        "scanned_object_count": 1,
        "historical_window_match_count": 0,
        "observations": [
            {
                "observed_started_at": "2026-10-02T14:00:00+00:00",
                "observed_finished_at": "2026-10-02T14:00:02+00:00",
                "object_generation": 7,
                "archive_sha256": "c" * 64,
                "balance_currency_rows": 1,
                "cash_currency_rows": 1,
                "currently_stale": True,
                "historical_window_match": False,
                "private_money": "9876.54",
                "private_native_account": "PRIVATE",
            }
        ],
        "private_source_binding": BINDING,
    }


def test_manual_single_environment_fixed_permissions_no_mutating_or_extra_endpoints():
    text = WORKFLOW.read_text()
    trigger = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in trigger
    for name in ("push:", "pull_request:", "workflow_run:", "schedule:"):
        assert name not in trigger
    assert trigger.count("required: true") == 3
    assert "expected_sha:" in trigger and "inspection_date:" in trigger
    assert "environment: longbridge-hk" in text
    assert "contents: read" in text and "id-token: write" in text
    for forbidden in (
        "permissions: write-all",
        "secrets.",
        "credentials_json:",
        "gcloud",
        "upload-artifact",
        "/probe",
        "scheduler",
        "account-facts/sync",
    ):
        assert forbidden not in text
    assert "persist-credentials: false" in text
    assert "uv sync --frozen --no-dev" in text
    assert "expected_source_binding_id:" in trigger
    assert "inputs.expected_source_binding_id" in text
    assert "HK_ARCHIVE_SOURCE_BINDING_ID=" not in text
    assert "/environments/" not in text and "/variables/" not in text
    for line in text.splitlines():
        if "vars.ACCOUNT_HISTORY" in line:
            assert " == " in line and (
                "HK_CONFIG_BINDING_MATCH:" in line or "HK_CONFIG_PREFIX_MATCH:" in line
            )
        if "inputs.expected_source_binding_id" in line:
            assert "HK_CONFIG_BINDING_MATCH:" in line
    assert "run-name:" not in text
    for line in text.splitlines():
        if "uses:" in line:
            ref = line.split("uses:", 1)[1].strip().split()[0].rsplit("@", 1)[1]
            assert len(ref) == 40 and all(char in "0123456789abcdef" for char in ref)


def test_all_pre_auth_gates_precede_wif_and_client_launcher():
    text = WORKFLOW.read_text()
    pre = text.index("id: preflight")
    checkout = text.index("uses: actions/checkout@")
    source = text.index("id: checked-source")
    auth = text.index("uses: google-github-actions/auth@")
    launch = text.index("# BEGIN_INVENTORY")
    assert pre < checkout < source < auth < launch
    assert text.count("steps.preflight.outputs.validated == 'true'") == 2
    assert text.count("steps.checked-source.outputs.validated == 'true'") == 2
    assert "github.ref == 'refs/heads/main'" in text


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_REF", "refs/tags/v1"),
        ("GITHUB_REPOSITORY", "other/LongBridgePlatform"),
        ("GITHUB_WORKFLOW_REF", "other/workflow@refs/heads/main"),
        ("GITHUB_SHA", "c" * 40),
        ("GITHUB_WORKFLOW_SHA", "c" * 40),
        ("expected_sha", "main"),
        ("expected_sha", "A" * 40),
        ("GCP_WORKLOAD_IDENTITY_PROVIDER", "other"),
        ("GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT", "other"),
        ("inspection_date", "2026-10-04"),
        ("inspection_date", "2026-10-05"),
        ("inspection_date", "2026-2-02"),
        ("inspection_date", "2026-02-30"),
        ("inspection_date", "2026-10-02\nPRIVATE"),
    ],
)
def test_unsupported_source_or_inputs_fail_before_metadata_or_auth(key, value):
    p = scope("PREFLIGHT")
    values = env()
    if key in event_inputs():
        event = Path(values["GITHUB_EVENT_PATH"])
        record = json.loads(event.read_text())
        record["inputs"][key] = value
        event.write_text(json.dumps(record))
    else:
        values[key] = value
    calls = []
    auth_calls = []
    with pytest.raises(p["PreflightError"]):
        p["validate_pre_auth"](values, NOW, fetcher(p, calls), checkout_sha=SHA)
        auth_calls.append("WIF")
    assert calls == [] and auth_calls == []


def test_actual_checkout_mismatch_fails_before_any_metadata_or_auth():
    p = scope("PREFLIGHT")
    calls = []
    with pytest.raises(p["PreflightError"], match="preauth_checkout_invalid"):
        p["validate_pre_auth"](env(), NOW, fetcher(p, calls), checkout_sha="c" * 40)
    assert calls == []


def test_actual_main_readback_is_not_inferred_from_github_sha():
    p = scope("PREFLIGHT")
    calls = []
    fetch = fetcher(
        p,
        calls,
        **{
            p["MAIN_PATH"]: {
                "ref": "refs/heads/main",
                "object": {"type": "commit", "sha": "c" * 40},
            }
        },
    )
    with pytest.raises(p["PreflightError"], match="preauth_main_changed"):
        p["validate_pre_auth"](env(), NOW, fetch, checkout_sha=SHA)
    assert calls == [p["MAIN_PATH"]]


@pytest.mark.parametrize("value", ["b" * 64, "A" * 64, BINDING + "\n", None])
def test_event_binding_hash_format_fail_before_metadata(value):
    p = scope("PREFLIGHT")
    values = env()
    event = Path(values["GITHUB_EVENT_PATH"])
    record = json.loads(event.read_text())
    record["inputs"]["expected_source_binding_id"] = value
    event.write_text(json.dumps(record))
    calls = []
    with pytest.raises(p["PreflightError"]):
        p["validate_pre_auth"](values, NOW, fetcher(p, calls), checkout_sha=SHA)
    assert calls == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("HK_CONFIG_BINDING_MATCH", "false"),
        ("HK_CONFIG_PREFIX_MATCH", "false"),
        ("HK_CONFIG_BINDING_MATCH", ""),
        ("HK_CONFIG_PREFIX_MATCH", "TRUE"),
    ],
)
def test_false_or_unknown_configuration_booleans_fail_before_metadata(key, value):
    p = scope("PREFLIGHT")
    values = env()
    values[key] = value
    calls = []
    with pytest.raises(p["PreflightError"], match="preauth_config_invalid"):
        p["validate_pre_auth"](values, NOW, fetcher(p, calls), checkout_sha=SHA)
    assert calls == []


def test_preflight_only_reads_main_metadata_not_environment_variables():
    p = scope("PREFLIGHT")
    calls = []
    assert p["validate_pre_auth"](env(), NOW, fetcher(p, calls), checkout_sha=SHA) == (
        SHA,
        "2026-10-02",
        BINDING,
    )
    assert calls == [p["MAIN_PATH"]]


@pytest.mark.parametrize(
    "raw",
    [
        b"x" * 65537,
        b"[]",
        b"not-json",
        b'{"inputs":null}',
        b'{"inputs":{"expected_sha":"PRIVATE"}}',
        b'{"inputs":{},"inputs":{}}',
    ],
    ids=[
        "oversize",
        "array",
        "malformed",
        "null-inputs",
        "missing-inputs",
        "duplicate-key",
    ],
)
def test_event_file_malformed_unknown_or_oversize_fails_private_before_metadata(
    raw, capsys
):
    p = scope("PREFLIGHT")
    values = env()
    Path(values["GITHUB_EVENT_PATH"]).write_bytes(raw)
    calls = []
    with pytest.raises(p["PreflightError"], match="preauth_event_invalid"):
        p["validate_pre_auth"](values, NOW, fetcher(p, calls), checkout_sha=SHA)
    assert calls == []
    assert capsys.readouterr().out == ""


def test_unknown_extra_dispatch_input_is_rejected_before_metadata():
    p = scope("PREFLIGHT")
    values = env()
    record = {"inputs": event_inputs() | {"bucket": "PRIVATE"}}
    Path(values["GITHUB_EVENT_PATH"]).write_text(json.dumps(record))
    calls = []
    with pytest.raises(p["PreflightError"], match="preauth_event_invalid"):
        p["validate_pre_auth"](values, NOW, fetcher(p, calls), checkout_sha=SHA)
    assert calls == []


@pytest.mark.parametrize(
    "body,status,url",
    [
        (b"x" * 65537, 200, "same"),
        (b"[]", 200, "same"),
        (b"not-json", 200, "same"),
        (b"{}", 302, "same"),
        (b"{}", 200, "https://other.test/redirect"),
    ],
    ids=["oversize", "array", "not-json", "redirect-status", "redirect-url"],
)
def test_private_metadata_get_rejects_oversize_invalid_and_redirect(body, status, url):
    p = scope("PREFLIGHT")
    calls = []
    request_urls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, limit):
            assert limit == 65537
            return body

        def geturl(self):
            return request_urls[0] if url == "same" else url

    response = Response()
    response.status = status

    def open_request(request, *, timeout):
        calls.append(1)
        request_urls.append(request.full_url)
        assert timeout == 10 and request.get_method() == "GET"
        return response

    p["build_opener"] = lambda handler: SimpleNamespace(open=open_request)
    assert p["NoRedirect"]().redirect_request(None) is None
    with pytest.raises(p["PreflightError"], match="preauth_metadata_unavailable"):
        p["read_json"](p["MAIN_PATH"], "synthetic-token")
    assert calls == [1]


def test_metadata_http_failure_is_fixed_private_and_not_retried():
    p = scope("PREFLIGHT")
    calls = []

    def denied(*_args, **_kwargs):
        calls.append(1)
        raise HTTPError("https://api.github.com", 403, "PRIVATE", {}, None)

    p["build_opener"] = lambda handler: SimpleNamespace(open=denied)
    with pytest.raises(p["PreflightError"], match="preauth_metadata_denied"):
        p["read_json"](p["MAIN_PATH"], "synthetic-token")
    assert calls == [1]


def test_launcher_observations_are_actual_inventory_not_arbitrary_five_minute_match():
    p = scope("INVENTORY")
    clients = []
    inspections = []
    client = object()

    def factory():
        clients.append(client)
        return client

    def inspect(**kwargs):
        inspections.append(kwargs)
        return history_result()

    result = json.loads(p["launch_inventory"](env(), NOW, factory, inspect))
    assert clients == [client] and len(inspections) == 1
    assert inspections[0]["archive_client"] is client
    assert inspections[0]["window_start"] == "2026-10-02T00:00:00Z"
    assert result["query_date_basis"] == "operator_selected"
    assert result["scanned_object_count"] == 1
    assert (
        result["observations"][0]["observed_started_at"] == "2026-10-02T14:00:00+00:00"
    )
    assert "historical_window_match_count" not in result
    assert (
        "9876.54" not in json.dumps(result)
        and "PRIVATE" not in json.dumps(result)
        and BINDING not in json.dumps(result)
    )
    for key in (
        "current_health_confirmed",
        "request_terminal_confirmed",
        "receiver_ack_confirmed",
        "native_identity_confirmed",
    ):
        assert result[key] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("HK_CHECKED_OUT_SHA", "c" * 40),
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_SHA", "c" * 40),
        ("GITHUB_WORKFLOW_SHA", "c" * 40),
        ("expected_source_binding_id", "b" * 64),
        ("inspection_date", "2026-10-04"),
        ("inspection_date", "invalid"),
    ],
)
def test_launcher_rejects_unsupported_state_before_client_creation(key, value):
    p = scope("INVENTORY")
    values = env()
    if key in event_inputs():
        event = Path(values["GITHUB_EVENT_PATH"])
        record = json.loads(event.read_text())
        record["inputs"][key] = value
        event.write_text(json.dumps(record))
    else:
        values[key] = value
    calls = []
    with pytest.raises(p["InventoryError"], match="inventory_inputs_invalid"):
        p["launch_inventory"](
            values,
            NOW,
            lambda: calls.append("client"),
            lambda **_kwargs: calls.append("read"),
        )
    assert calls == []


def test_client_read_failure_is_fixed_no_raw_error_and_no_retry():
    p = scope("INVENTORY")
    calls = []

    def read(**_kwargs):
        calls.append(1)
        raise RuntimeError("PRIVATE bucket or account")

    with pytest.raises(p["InventoryError"], match="inventory_read_failed"):
        p["launch_inventory"](env(), NOW, object, read)
    assert calls == [1]


@pytest.mark.parametrize(
    "key,value",
    [
        ("current_health_confirmed", True),
        ("receiver_ack_confirmed", True),
        ("request_terminal_confirmed", True),
        ("native_identity_confirmed", True),
        ("scanned_object_count", True),
        ("scanned_object_count", 2),
        ("target", "sg"),
    ],
)
def test_projection_rejects_unknown_or_overclaimed_success(key, value):
    p = scope("INVENTORY")
    result = history_result()
    result[key] = value
    with pytest.raises(p["InventoryError"], match="inventory_projection_invalid"):
        p["project_inventory"](result, "2026-10-02")


@pytest.mark.parametrize(
    "key,value",
    [
        ("object_generation", True),
        ("object_generation", 0),
        ("object_generation", 2**64),
        ("archive_sha256", "PRIVATE"),
        ("currently_stale", 1),
        ("observed_started_at", "2026-10-01T00:00:00Z"),
        ("observed_started_at", "2026-10-02T14:00:00"),
        ("observed_finished_at", "2026-10-02T13:00:00Z"),
        ("cash_currency_rows", -1),
    ],
)
def test_projection_rejects_invalid_metadata_without_echoing_it(key, value):
    p = scope("INVENTORY")
    result = history_result()
    result["observations"][0][key] = value
    with pytest.raises(p["InventoryError"], match="inventory_projection_invalid"):
        p["project_inventory"](result, "2026-10-02")


def test_complete_empty_inventory_is_not_business_recovery():
    p = scope("INVENTORY")
    result = history_result()
    result.update(observations=[], scanned_object_count=0)
    projected = json.loads(p["project_inventory"](result, "2026-10-02"))
    assert projected["configured_source_date_listing_complete"] is True
    assert projected["listed_prefix_objects_validated"] is True
    assert projected["scanned_object_count"] == 0
    assert projected["archive_snapshot_atomic"] is False
    assert projected["historical_account_coverage_confirmed"] is False
    assert "inventory_complete" not in projected
    assert projected["request_terminal_confirmed"] is False


def test_object_limit_and_serialized_output_limit():
    p = scope("INVENTORY")
    result = history_result()
    result.update(observations=result["observations"] * 65, scanned_object_count=65)
    with pytest.raises(p["InventoryError"], match="inventory_projection_invalid"):
        p["project_inventory"](result, "2026-10-02")
    p["MAX_OUTPUT_BYTES"] = 10
    with pytest.raises(p["InventoryError"], match="inventory_output_limit"):
        p["project_inventory"](history_result(), "2026-10-02")


def test_launcher_integrates_existing_reader_for_valid_outside_selector_archive():
    import test_historical_hk_account_archive as existing

    from scripts.inspect_historical_hk_account_archive import (
        inspect_historical_hk_account_archive,
    )

    p = scope("INVENTORY")
    start = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
    finished = datetime(2026, 10, 2, 14, 0, 2, tzinfo=timezone.utc)
    client = existing.FakeClient([existing.metadata(existing.history(start, finished))])
    projected = json.loads(
        p["launch_inventory"](
            env(), NOW, lambda: client, inspect_historical_hk_account_archive
        )
    )
    assert projected["scanned_object_count"] == 1
    assert projected["observations"][0]["observed_started_at"] == start.isoformat()
    assert projected["request_terminal_confirmed"] is False
    assert "1234.56" not in json.dumps(projected)
    assert len([call for call in client.calls if call[0] == "download"]) == 1


@pytest.mark.parametrize(
    "env_value",
    [
        {},
        {"HK_APPROVED_CREDENTIALS_FILE": "approved"},
        {
            "HK_APPROVED_CREDENTIALS_FILE": "approved",
            "GOOGLE_APPLICATION_CREDENTIALS": "other",
        },
    ],
)
def test_credentials_factory_never_falls_back_to_default_adc(env_value):
    p = scope("INVENTORY")
    calls = []
    with pytest.raises(
        p["InventoryError"], match="inventory_credentials_binding_invalid"
    ):
        p["make_storage_client"](
            env_value,
            lambda **kwargs: calls.append("client"),
            lambda *args, **kwargs: calls.append("loader"),
        )
    assert calls == []


def wif_info():
    return {
        "type": "external_account",
        "audience": "//iam.googleapis.com/" + env()["GCP_WORKLOAD_IDENTITY_PROVIDER"],
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com:generateAccessToken",
        "credential_source": {
            "url": "https://token.actions.githubusercontent.com/oidc?api-version=2.0&audience=https%3A%2F%2Fiam.googleapis.com%2Fprojects%2F252919773759%2Flocations%2Fglobal%2FworkloadIdentityPools%2Fgithub-actions%2Fproviders%2Fgithub-main",
            "headers": {"Authorization": "Bearer synthetic-not-real"},
            "format": {"type": "json", "subject_token_field_name": "value"},
        },
    }


def wif_env(tmp_path, info=None):
    path = tmp_path / "official.json"
    path.write_text(json.dumps(wif_info() if info is None else info))
    return {
        "HK_APPROVED_CREDENTIALS_FILE": str(path),
        "GOOGLE_APPLICATION_CREDENTIALS": str(path),
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.githubusercontent.com/oidc?api-version=2.0",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-not-real",
    }


def test_storage_factory_constructs_only_validated_wif_info_readonly_scope(tmp_path):
    p = scope("INVENTORY")
    calls = []
    credential = object()
    client = object()

    def constructor(info, *, scopes):
        assert info == wif_info()
        calls.append(("construct", scopes))
        return credential

    def factory(**kwargs):
        calls.append(("client", kwargs))
        return client

    assert p["make_storage_client"](wif_env(tmp_path), factory, constructor) is client
    assert calls == [
        ("construct", ["https://www.googleapis.com/auth/devstorage.read_only"]),
        ("client", {"project": "longbridgequant", "credentials": credential}),
    ]
    assert "load_credentials_from_file" not in block("INVENTORY")
    assert "google.auth.default" not in block("INVENTORY")
    assert "identity_pool.Credentials.from_info" in block("INVENTORY")


@pytest.mark.parametrize(
    "key,value",
    [
        ("type", "service_account"),
        ("audience", "//iam.googleapis.com/other"),
        ("token_url", "https://other.invalid/token"),
        ("subject_token_type", "other"),
        (
            "service_account_impersonation_url",
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/other:generateAccessToken",
        ),
        ("universe_domain", "other.invalid"),
    ],
)
def test_wrong_credential_identity_or_endpoint_fails_before_constructor(
    tmp_path, key, value
):
    p = scope("INVENTORY")
    info = wif_info()
    info[key] = value
    calls = []
    with pytest.raises(
        p["InventoryError"], match="inventory_credentials_binding_invalid"
    ):
        p["make_storage_client"](
            wif_env(tmp_path, info),
            lambda **kw: calls.append("client"),
            lambda *a, **kw: calls.append("construct"),
        )
    assert calls == []


@pytest.mark.parametrize(
    "source",
    [
        {"file": "/PRIVATE"},
        {"executable": {"command": "PRIVATE"}},
        {"url": "https://other.invalid/oidc"},
        {"url": "http://token.actions.githubusercontent.com/oidc"},
    ],
)
def test_arbitrary_credential_sources_fail_before_constructor(tmp_path, source):
    p = scope("INVENTORY")
    info = wif_info()
    info["credential_source"] = source
    calls = []
    with pytest.raises(
        p["InventoryError"], match="inventory_credentials_binding_invalid"
    ):
        p["make_storage_client"](
            wif_env(tmp_path, info),
            lambda **kw: calls.append("client"),
            lambda *a, **kw: calls.append("construct"),
        )
    assert calls == []


@pytest.mark.parametrize(
    "kind",
    [
        "wrong-header",
        "extra-header",
        "wrong-audience-query",
        "extra-source",
        "wrong-format",
        "fragment",
        "duplicate-query",
    ],
)
def test_github_oidc_source_exact_binding_rejects_private_changes(tmp_path, kind):
    p = scope("INVENTORY")
    info = wif_info()
    source = info["credential_source"]
    if kind == "wrong-header":
        source["headers"]["Authorization"] = "Bearer PRIVATE"
    elif kind == "extra-header":
        source["headers"]["Other"] = "PRIVATE"
    elif kind == "wrong-audience-query":
        source["url"] = source["url"].replace("github-main", "other")
    elif kind == "extra-source":
        source["file"] = "/PRIVATE"
    elif kind == "wrong-format":
        source["format"]["subject_token_field_name"] = "PRIVATE"
    elif kind == "fragment":
        source["url"] += "#PRIVATE"
    else:
        source["url"] += "&audience=PRIVATE"
    calls = []
    with pytest.raises(
        p["InventoryError"], match="inventory_credentials_binding_invalid"
    ):
        p["make_storage_client"](
            wif_env(tmp_path, info),
            lambda **kw: calls.append("client"),
            lambda *a, **kw: calls.append("construct"),
        )
    assert calls == []


def test_real_locked_constructor_and_explicit_project_never_refresh_or_discover(
    monkeypatch, tmp_path
):
    auth = pytest.importorskip("google.auth")
    identity_pool = pytest.importorskip("google.auth.identity_pool")
    external_account = pytest.importorskip("google.auth.external_account")
    storage = pytest.importorskip("google.cloud.storage")
    assert auth.__version__ == "2.55.1"
    assert storage.__version__ == "3.12.0"
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("forbidden")
        raise AssertionError("construction performed network or project discovery")

    monkeypatch.setattr(auth, "default", forbidden)
    monkeypatch.setattr(external_account.Credentials, "get_project_id", forbidden)
    monkeypatch.setattr(external_account.Credentials, "refresh", forbidden)
    monkeypatch.setattr(identity_pool.Credentials, "refresh", forbidden)
    p = scope("INVENTORY")
    client = p["make_storage_client"](
        wif_env(tmp_path), storage.Client, identity_pool.Credentials.from_info
    )
    assert client.project == "longbridgequant"
    assert isinstance(client._credentials, identity_pool.Credentials)
    assert client._credentials.token is None
    assert client._credentials._impersonated_credentials is None
    assert hasattr(client._credentials, "_rab_manager")
    assert calls == []


def test_preflight_masks_binding_without_exporting_raw_binding_to_env(tmp_path, capsys):
    p = scope("PREFLIGHT")
    values = env()
    values.update(
        GITHUB_ENV=str(tmp_path / "env"),
        GITHUB_OUTPUT=str(tmp_path / "output"),
        GH_TOKEN="synthetic-token",
    )
    calls = []
    fetch = fetcher(p, calls)
    p["read_json"] = lambda path, token: fetch(path)
    p["subprocess"] = SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=SHA + "\n")
    )
    assert p["main"]("after_checkout", values, NOW) == 0
    assert capsys.readouterr().out == "::add-mask::" + BINDING + "\n"
    assert (tmp_path / "output").read_text() == "validated=true\n"
    assert "HK_CHECKED_OUT_SHA=" + SHA in (tmp_path / "env").read_text()
    assert BINDING not in (tmp_path / "env").read_text()
    assert "HK_ARCHIVE_SOURCE_BINDING_ID" not in (tmp_path / "env").read_text()


def test_preflight_main_unknown_error_never_reflects_private_detail(tmp_path, capsys):
    p = scope("PREFLIGHT")
    values = env()
    values.update(
        GITHUB_ENV=str(tmp_path / "env"), GITHUB_OUTPUT=str(tmp_path / "output")
    )

    def unknown(*args, **kwargs):
        raise p["PreflightError"]("PRIVATE_NATIVE_DETAIL")

    p["validate_pre_auth"] = unknown
    assert p["main"]("before_checkout", values, NOW) == 1
    assert (
        capsys.readouterr().out
        == "::add-mask::"
        + BINDING
        + "\n"
        + "hk_archive_inventory_error:preauth_unavailable\n"
    )
    assert not (tmp_path / "output").exists()


def test_invalid_branch_main_does_not_even_inspect_checkout_or_metadata(capsys):
    p = scope("PREFLIGHT")
    values = env()
    values["GITHUB_REF"] = "refs/heads/feature"
    calls = []
    p["read_json"] = lambda *args: calls.append("metadata")
    p["subprocess"] = SimpleNamespace(
        run=lambda *args, **kwargs: calls.append("checkout")
    )
    assert p["main"]("after_checkout", values, NOW) == 1
    assert calls == []
    assert (
        capsys.readouterr().out == "hk_archive_inventory_error:preauth_source_invalid\n"
    )


@pytest.mark.parametrize(
    "category",
    [
        "historical_listing_failed",
        "gcs_read_failed",
        "generation_changed",
        "historical_listing_truncated",
        "historical_object_invalid",
    ],
)
def test_allowlisted_reader_categories_are_preserved_without_raw_details(category):
    from scripts import record_daily_account_snapshot as snapshots

    p = scope("INVENTORY")

    def inspect(**kwargs):
        raise snapshots._Rejected(category)

    with pytest.raises(p["InventoryError"], match="reader_" + category):
        p["launch_inventory"](env(), NOW, object, inspect)


@pytest.mark.parametrize(
    "category,expected",
    [
        ("historical_listing_failed", "reader_listing_http_denied"),
        ("gcs_read_failed", "reader_get_http_denied"),
    ],
)
def test_native_http_denial_is_distinct_from_unknown_without_claiming_iam(
    category, expected
):
    from http import HTTPStatus

    from scripts import record_daily_account_snapshot as snapshots

    p = scope("INVENTORY")

    class NativeHttpDenial(Exception):
        code = HTTPStatus.FORBIDDEN

    def inspect(**kwargs):
        try:
            raise NativeHttpDenial("PRIVATE response body")
        except NativeHttpDenial:
            raise snapshots._Rejected(category) from None

    with pytest.raises(p["InventoryError"], match=expected):
        p["launch_inventory"](env(), NOW, object, inspect)
    assert "iam" not in expected


@pytest.mark.parametrize("category", ["PRIVATE_ACCOUNT", None, ["gcs_read_failed"]])
def test_unknown_reader_category_is_not_reflected(category):
    p = scope("INVENTORY")

    class Unknown(Exception):
        pass

    failure = Unknown("PRIVATE amount or token")
    failure.category = category

    def inspect(**kwargs):
        raise failure

    with pytest.raises(p["InventoryError"], match="inventory_read_failed"):
        p["launch_inventory"](env(), NOW, object, inspect)


def test_listing_completion_never_claims_atomic_or_full_day_account_coverage():
    p = scope("INVENTORY")
    result = json.loads(p["project_inventory"](history_result(), "2026-10-02"))
    assert result["configured_source_date_listing_complete"] is True
    assert result["listed_prefix_objects_validated"] is True
    assert result["archive_snapshot_atomic"] is False
    assert result["historical_account_coverage_confirmed"] is False
    assert "inventory_complete" not in result


@pytest.mark.parametrize("key", ["HK_CONFIG_BINDING_MATCH", "HK_CONFIG_PREFIX_MATCH"])
def test_launcher_false_config_consistency_has_zero_factory_calls(key):
    p = scope("INVENTORY")
    values = env()
    values[key] = "false"
    calls = []
    with pytest.raises(p["InventoryError"], match="inventory_inputs_invalid"):
        p["launch_inventory"](
            values,
            NOW,
            lambda: calls.append("client"),
            lambda **kw: calls.append("read"),
        )
    assert calls == []


@pytest.mark.parametrize(
    "raw",
    [b"[]", b"not-json", b'{"inputs":{},"inputs":{}}'],
    ids=["array", "malformed", "duplicate-key"],
)
def test_launcher_malformed_event_has_zero_factory_calls(raw):
    p = scope("INVENTORY")
    values = env()
    Path(values["GITHUB_EVENT_PATH"]).write_bytes(raw)
    calls = []
    with pytest.raises(p["InventoryError"], match="inventory_inputs_invalid"):
        p["launch_inventory"](
            values,
            NOW,
            lambda: calls.append("client"),
            lambda **kw: calls.append("read"),
        )
    assert calls == []


@pytest.mark.parametrize(
    "raw",
    [
        b"x" * 65537,
        b"[]",
        b"not-json",
        b'{"type":"external_account","type":"external_account"}',
    ],
    ids=["oversize", "array", "malformed", "duplicate-key"],
)
def test_invalid_credential_file_has_zero_constructor_or_provider_calls(tmp_path, raw):
    p = scope("INVENTORY")
    values = wif_env(tmp_path)
    Path(values["HK_APPROVED_CREDENTIALS_FILE"]).write_bytes(raw)
    calls = []
    with pytest.raises(
        p["InventoryError"], match="inventory_credentials_binding_invalid"
    ):
        p["make_storage_client"](
            values,
            lambda **kw: calls.append("client"),
            lambda *a, **kw: calls.append("construct"),
        )
    assert calls == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("ACTIONS_ID_TOKEN_REQUEST_TOKEN", ""),
        ("ACTIONS_ID_TOKEN_REQUEST_URL", "https://other.invalid/oidc?api-version=2.0"),
        (
            "ACTIONS_ID_TOKEN_REQUEST_URL",
            "http://token.actions.githubusercontent.com/oidc?api-version=2.0",
        ),
        (
            "ACTIONS_ID_TOKEN_REQUEST_URL",
            "https://token.actions.githubusercontent.com:443/oidc?api-version=2.0",
        ),
        (
            "ACTIONS_ID_TOKEN_REQUEST_URL",
            "https://user@token.actions.githubusercontent.com/oidc?api-version=2.0",
        ),
    ],
)
def test_changed_runner_oidc_source_cannot_expand_credentials(tmp_path, key, value):
    p = scope("INVENTORY")
    values = wif_env(tmp_path)
    values[key] = value
    calls = []
    with pytest.raises(
        p["InventoryError"], match="inventory_credentials_binding_invalid"
    ):
        p["make_storage_client"](
            values,
            lambda **kw: calls.append("client"),
            lambda *a, **kw: calls.append("construct"),
        )
    assert calls == []


def test_native_regional_security_endpoint_and_headers_remain_intact(tmp_path):
    from datetime import timedelta

    auth = pytest.importorskip("google.auth")
    identity_pool = pytest.importorskip("google.auth.identity_pool")
    assert auth.__version__ == "2.55.1"
    p = scope("INVENTORY")
    credential = p["make_storage_client"](
        wif_env(tmp_path),
        lambda **kw: kw["credentials"],
        identity_pool.Credentials.from_info,
    )
    # Native construction only. Never invoke token refresh or lookup requests.
    impersonated = credential._initialize_impersonated_credentials()
    assert impersonated._build_regional_access_boundary_lookup_url() == (
        "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
        "longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com/allowedLocations"
    )
    assert impersonated._is_regional_access_boundary_lookup_required() is True
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    impersonated._rab_manager.set_initial_regional_access_boundary(
        "synthetic-regions", expiry
    )
    headers = {}
    impersonated.apply(headers, token="synthetic-not-real")
    assert headers["x-allowed-locations"] == "synthetic-regions"
    assert "_rab_manager =" not in block("INVENTORY")
    assert "_set_blocking_regional_access_boundary_lookup" not in block("INVENTORY")
    assert "def refresh" not in block("INVENTORY")


def test_inventory_outer_timeout_includes_native_sdk_and_five_second_grace():
    text = WORKFLOW.read_text()
    assert (
        "PYTHONPATH=. timeout --signal=TERM --kill-after=5s 175s "
        "uv run --no-sync python -" in text
    )
    assert "timeout-minutes: 8" in text
    assert 175 + 5 == 180


def test_outer_timeout_kills_only_synthetic_nonterminating_process_group():
    import shutil
    import subprocess
    import time

    timeout = shutil.which("timeout")
    if timeout is None:
        pytest.skip("GNU timeout is unavailable on this local test host")
    started = time.monotonic()
    result = subprocess.run(
        [
            timeout,
            "--signal=TERM",
            "--kill-after=0.1s",
            "0.1s",
            "bash",
            "-c",
            'trap "" TERM; while :; do :; done',
        ],
        capture_output=True,
        timeout=3,
    )
    assert result.returncode in {124, 137, -9}
    assert time.monotonic() - started < 3


@pytest.mark.parametrize(
    "cause,expected_family,expected_status",
    [
        (TypeError("PRIVATE token URL"), "local_argument", None),
        (ValueError("PRIVATE account value"), "local_value", None),
        (RuntimeError("PRIVATE body"), "unknown", None),
        (None, "unknown", None),
    ],
)
def test_fixed_immediate_cause_projection_never_reads_exception_message(
    cause, expected_family, expected_status
):
    p = scope("INVENTORY")
    result = p["project_reader_cause"](cause)
    assert result == {
        "cause_family": expected_family,
        "http_status": expected_status,
        "cause_depth": 0 if cause is None else 1,
    }
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize(
    "kind,expected_family,expected_status",
    [
        ("refresh", "auth_refresh", None),
        ("transport", "transport", None),
        ("gcs_response", "gcs_response", 403),
    ],
)
def test_native_exception_family_projection_is_fixed_without_body_or_url(
    kind, expected_family, expected_status
):
    auth_errors = pytest.importorskip("google.auth.exceptions")
    api_errors = pytest.importorskip("google.api_core.exceptions")
    causes = {
        "refresh": auth_errors.RefreshError("PRIVATE credential body"),
        "transport": auth_errors.TransportError("PRIVATE endpoint URL"),
        "gcs_response": api_errors.Forbidden("PRIVATE account body"),
    }
    p = scope("INVENTORY")
    result = p["project_reader_cause"](causes[kind])
    assert result == {
        "cause_family": expected_family,
        "http_status": expected_status,
        "cause_depth": 1,
    }
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("status", [True, 99, 600, "403", float("nan"), [403]])
def test_unknown_http_metadata_cannot_be_printed_as_status(status):
    p = scope("INVENTORY")
    error = RuntimeError("PRIVATE")
    error.code = status
    assert p["project_reader_cause"](error)["http_status"] is None


def test_reader_error_retains_safe_immediate_auth_refresh_family():
    auth_errors = pytest.importorskip("google.auth.exceptions")
    from scripts import record_daily_account_snapshot as snapshots

    p = scope("INVENTORY")

    def inspect(**kwargs):
        try:
            raise auth_errors.RefreshError("PRIVATE auth response")
        except auth_errors.RefreshError:
            raise snapshots._Rejected("historical_listing_failed") from None

    with pytest.raises(p["InventoryError"]) as caught:
        p["launch_inventory"](env(), NOW, object, inspect)
    assert str(caught.value) == "reader_historical_listing_failed"
    assert caught.value.reader_cause == {
        "cause_family": "auth_refresh",
        "http_status": None,
        "cause_depth": 1,
    }


def test_main_only_prints_fixed_reader_cause_fields(capsys):
    pytest.importorskip("google.cloud.storage")
    p = scope("INVENTORY")

    def failed(*args, **kwargs):
        raise p["InventoryError"](
            "reader_historical_listing_failed",
            reader_cause={
                "cause_family": "local_value",
                "http_status": None,
                "cause_depth": 1,
            },
        )

    p["launch_inventory"] = failed
    assert p["main"]() == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "hk_archive_inventory_error:reader_historical_listing_failed"
    assert json.loads(lines[1].split(":", 1)[1]) == {
        "cause_family": "local_value",
        "http_status": None,
        "cause_depth": 1,
    }


def test_main_never_prints_unvalidated_reader_cause_fields(capsys):
    pytest.importorskip("google.cloud.storage")
    p = scope("INVENTORY")

    def failed(*args, **kwargs):
        raise p["InventoryError"](
            "reader_historical_listing_failed",
            reader_cause={
                "cause_family": "PRIVATE",
                "http_status": "PRIVATE",
                "cause_depth": 1,
                "body": "PRIVATE",
            },
        )

    p["launch_inventory"] = failed
    assert p["main"]() == 1
    assert (
        capsys.readouterr().out
        == "hk_archive_inventory_error:reader_historical_listing_failed\n"
    )
