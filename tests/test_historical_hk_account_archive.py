from __future__ import annotations

import ast
import hashlib
import json
import socket
import urllib.request  # preload SSL before the socket connection guard
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import inspect_historical_hk_account_archive as inspector
from scripts import record_daily_account_snapshot as snapshots

START = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
END = START + timedelta(minutes=5)
NOW = START + timedelta(days=2)
BINDING = "a" * 64
PREFIX = "longbridge/account_snapshots/hk/" + BINDING + "/"
BUCKET = "qsl-runtime-logs-shared"


@pytest.fixture(autouse=True)
def no_external_or_default_operations(monkeypatch):
    assert urllib.request.HTTPRedirectHandler

    def forbidden(*_args, **_kwargs):
        raise AssertionError("external/default operation forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in (
        "main",
        "record_daily_account_snapshot",
        "_authorized_session",
        "_open_store",
        "_scheduler_get",
        "_scheduler_run",
        "_publish_account_facts",
        "_config",
    ):
        if hasattr(snapshots, name):
            monkeypatch.setattr(snapshots, name, forbidden)


def history(start=START + timedelta(seconds=1), finish=START + timedelta(seconds=2)):
    return {
        "schema_version": snapshots.HISTORY_SCHEMA,
        "snapshot_schema_version": snapshots.SNAPSHOT_SCHEMA,
        "account_scope": "HK",
        "target_id": "hk",
        "source_binding": {
            "kind": snapshots.SOURCE_KIND,
            "status": "bound",
            "id": BINDING,
        },
        "observed_started_at": start.isoformat(),
        "observed_finished_at": finish.isoformat(),
        "snapshot_atomic": False,
        "observation_date": start.date().isoformat(),
        "broker_reported_balances": [
            {"currency": "HKD", "net_assets": "1234.56", "total_cash": "-3"}
        ],
        "cash": [
            {
                "currency": "HKD",
                "available_cash": "-1",
                "frozen_cash": "0",
                "settling_cash": "2",
            }
        ],
    }


def metadata(payload, *, generation=7, raw=None, name=None):
    raw = json.dumps(payload, separators=(",", ":")).encode() if raw is None else raw
    started = snapshots._aware(payload["observed_started_at"])
    finished = snapshots._aware(payload["observed_finished_at"])
    name = (
        name
        or f"{PREFIX}{started.date().isoformat()}/{snapshots._filename_for(finished)}"
    )
    return SimpleNamespace(name=name, generation=generation, size=len(raw)), raw


class FakeClient:
    def __init__(
        self, objects=(), *, next_page_token=None, download_error=None, clock=None
    ):
        self.objects = dict((row.name, (row, raw)) for row, raw in objects)
        self.calls = []
        self.next_page_token = next_page_token
        self.download_error = download_error
        self.clock = clock

    def list_blobs(self, bucket, **kwargs):
        self.calls.append(("list", bucket, kwargs))
        assert bucket == BUCKET
        assert kwargs["prefix"].startswith(PREFIX)
        assert kwargs["retry"] is None
        assert 0 < kwargs["timeout"] <= snapshots.GCS_TIMEOUT_SECONDS
        assert kwargs["page_size"] == snapshots.MAX_OBJECTS_PER_DAY + 1
        assert kwargs["max_results"] == snapshots.MAX_OBJECTS_PER_DAY + 1
        rows = [
            row
            for row, _raw in self.objects.values()
            if row.name.startswith(kwargs["prefix"])
        ]
        if self.clock:
            self.clock.advance()
        return SimpleNamespace(pages=iter([rows]), next_page_token=self.next_page_token)

    def bucket(self, bucket):
        self.calls.append(("bucket", bucket))
        assert bucket == BUCKET
        return self

    def blob(self, name, *, generation):
        self.calls.append(("blob", name, generation))
        assert name in self.objects
        row, raw = self.objects[name]
        assert generation == int(row.generation)
        owner = self

        class Blob:
            def download_as_bytes(self, **kwargs):
                owner.calls.append(("download", kwargs))
                assert kwargs["if_generation_match"] == generation
                assert kwargs["retry"] is None
                assert kwargs["start"] == 0
                assert kwargs["end"] == snapshots.MAX_OBJECT_BYTES - 1
                assert 0 < kwargs["timeout"] <= snapshots.GCS_TIMEOUT_SECONDS
                if owner.download_error:
                    raise owner.download_error
                if owner.clock:
                    owner.clock.advance()
                return raw

        return Blob()


def inspect(
    client, start=START, end=END, binding=BINDING, now=NOW, monotonic=lambda: 0
):
    return inspector.inspect_historical_hk_account_archive(
        archive_client=client,
        expected_source_binding_id=binding,
        window_start=start.isoformat() if isinstance(start, datetime) else start,
        window_end=end.isoformat() if isinstance(end, datetime) else end,
        inspected_at=now,
        monotonic=monotonic,
    )


def rejected(category, client, **kwargs):
    with pytest.raises(snapshots._Rejected) as raised:
        inspect(client, **kwargs)
    assert raised.value.category == category
    assert str(raised.value) == category


def test_old_hk_archive_is_historical_and_never_current_or_receiver_evidence():
    payload = history()
    row, raw = metadata(payload)
    client = FakeClient([(row, raw)])
    result = inspect(client)
    assert result["historical_window_match_count"] == 1
    assert result["same_window_archive_unique"] is True
    assert result["evidence_kind"] == "historical_hk_archive_inspection"
    for field in (
        "current_health_confirmed",
        "request_terminal_confirmed",
        "receiver_ack_confirmed",
        "native_identity_confirmed",
    ):
        assert result[field] is False
    assert (
        result["observations"][0]["archive_sha256"] == hashlib.sha256(raw).hexdigest()
    )
    assert result["observations"][0]["object_generation"] == 7
    assert result["observations"][0]["currently_stale"] is True
    assert not snapshots._is_fresh(payload, NOW)
    output = json.dumps(result)
    assert "1234.56" not in output and BINDING not in output and BUCKET not in output
    assert len([call for call in client.calls if call[0] == "download"]) == 1


def test_empty_complete_listing_is_zero_observed_matches_without_business_success():
    result = inspect(FakeClient())
    assert (
        result["scanned_object_count"] == result["historical_window_match_count"] == 0
    )
    assert result["same_window_archive_unique"] is False
    assert result["request_terminal_confirmed"] is False


def test_valid_outside_window_observation_is_not_a_same_request_match():
    start = START - timedelta(minutes=2)
    client = FakeClient([metadata(history(start, start + timedelta(seconds=1)))])
    result = inspect(client)
    assert result["scanned_object_count"] == 1
    assert result["historical_window_match_count"] == 0


def test_multiple_window_archives_preserve_ambiguity():
    first = metadata(history())
    second = metadata(
        history(START + timedelta(seconds=3), START + timedelta(seconds=4))
    )
    result = inspect(FakeClient([first, second]))
    assert result["historical_window_match_count"] == 2
    assert result["same_window_archive_unique"] is False


def test_cross_utc_day_window_only_lists_two_historical_dates():
    start = datetime(2026, 10, 2, 23, 58, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    payload = history(start + timedelta(minutes=1), start + timedelta(minutes=3))
    client = FakeClient([metadata(payload)])
    result = inspect(client, start=start, end=end)
    assert result["historical_window_match_count"] == 1
    prefixes = [call[2]["prefix"] for call in client.calls if call[0] == "list"]
    assert prefixes == [PREFIX + "2026-10-02/", PREFIX + "2026-10-03/"]


@pytest.mark.parametrize(
    "binding", [None, "", "b", "A" * 64, "../" + "a" * 61, [BINDING]]
)
def test_invalid_binding_rejected_before_client_calls(binding):
    client = FakeClient()
    rejected("historical_config_invalid", client, binding=binding)
    assert client.calls == []


@pytest.mark.parametrize(
    "start,end,now",
    [
        (START, START - timedelta(seconds=1), NOW),
        (START, END + timedelta(microseconds=1), NOW),
        (START, END, START),
        ("2026-10-02T12:00:00", END, NOW),
        (START, "invalid", NOW),
        (START, END, NOW.replace(tzinfo=None)),
    ],
)
def test_invalid_window_rejected_before_client_calls(start, end, now):
    client = FakeClient()
    rejected("historical_time_invalid", client, start=start, end=end, now=now)
    assert client.calls == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_scope", "SG"),
        ("target_id", "paper"),
        (
            "source_binding",
            {"kind": snapshots.SOURCE_KIND, "status": "bound", "id": "b" * 64},
        ),
        ("snapshot_atomic", True),
        ("observation_date", "2026-10-01"),
        ("schema_version", "unknown"),
        ("unexpected_private_account", "SECRET"),
        (
            "broker_reported_balances",
            [{"currency": "HKD", "net_assets": "NaN", "total_cash": "1"}],
        ),
        (
            "cash",
            [
                {
                    "currency": "HKD",
                    "available_cash": "Infinity",
                    "frozen_cash": "0",
                    "settling_cash": "0",
                }
            ],
        ),
    ],
)
def test_invalid_history_rejected_without_raw_output(field, value):
    payload = history()
    payload[field] = value
    rejected("historical_object_invalid", FakeClient([metadata(payload)]))


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", None),
        ("generation", True),
        ("generation", 0),
        ("generation", -1),
        ("generation", "007"),
        ("generation", 2**64),
        ("size", True),
        ("size", -1),
        ("size", snapshots.MAX_OBJECT_BYTES + 1),
        ("name", PREFIX + "2026-10-02/unexpected.json"),
        ("name", PREFIX + "2026-10-02/250000000000Z.json"),
        ("name", PREFIX + "2026-10-02/１２３０００００００００Z.json"),
    ],
)
def test_malformed_metadata_rejected_before_read(field, value):
    row, raw = metadata(history())
    setattr(row, field, value)
    client = FakeClient([(row, raw)])
    rejected("historical_listing_invalid", client)
    assert not any(call[0] == "download" for call in client.calls)


def test_unexpected_listing_prefix_rejected_before_read():
    row, raw = metadata(history())
    client = FakeClient([(row, raw)])

    def wrong_listing(_bucket, **_kwargs):
        wrong = deepcopy(row)
        wrong.name = "unapproved/" + wrong.name
        return SimpleNamespace(pages=iter([[wrong]]), next_page_token=None)

    client.list_blobs = wrong_listing
    rejected("historical_listing_invalid", client)
    assert client.calls == []


@pytest.mark.parametrize("token", ["more", False, 0])
def test_unknown_or_truncated_pagination_rejected_before_read(token):
    client = FakeClient([metadata(history())], next_page_token=token)
    rejected("historical_listing_truncated", client)
    assert not any(call[0] == "download" for call in client.calls)


def test_unknown_pagination_capability_rejected():
    client = FakeClient()
    client.list_blobs = lambda *_args, **_kwargs: []
    rejected("historical_listing_invalid", client)


def test_object_count_limit_rejected_before_any_download():
    objects = [
        metadata(
            history(
                START + timedelta(seconds=i),
                START + timedelta(seconds=i, microseconds=1),
            )
        )
        for i in range(65)
    ]
    client = FakeClient(objects)
    rejected("historical_listing_truncated", client)
    assert not any(call[0] == "download" for call in client.calls)


def test_total_two_day_count_limit_rejected_before_any_download():
    start = datetime(2026, 10, 2, 23, 58, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    objects = [
        metadata(
            history(
                start + timedelta(seconds=i * 4), start + timedelta(seconds=i * 4 + 1)
            )
        )
        for i in range(65)
    ]
    client = FakeClient(objects)
    rejected("historical_listing_truncated", client, start=start, end=end)
    assert not any(call[0] == "download" for call in client.calls)


@pytest.mark.parametrize(
    "error,category",
    [
        (RuntimeError("precondition failed PRIVATE"), "generation_changed"),
        (RuntimeError("PRIVATE native account"), "gcs_read_failed"),
    ],
)
def test_generation_race_or_unknown_read_has_no_retry_or_partial_result(
    error, category
):
    client = FakeClient([metadata(history())], download_error=error)
    rejected(category, client)
    assert len([call for call in client.calls if call[0] == "download"]) == 1


@pytest.mark.parametrize(
    "raw", [b"{}", b"[]", b"not json", b"", b"x" * (snapshots.MAX_OBJECT_BYTES + 1)]
)
def test_truncated_or_invalid_body_rejected(raw):
    row, valid = metadata(history())
    client = FakeClient([(row, valid)])
    client.objects[row.name] = (row, raw)
    rejected(
        "gcs_object_truncated" if len(raw) != row.size else "gcs_object_invalid", client
    )


class Clock:
    def __init__(self, increment):
        self.value = 0
        self.increment = increment

    def __call__(self):
        return self.value

    def advance(self):
        self.value += self.increment


@pytest.mark.parametrize(
    "increment",
    [inspector.MAX_INSPECTION_SECONDS, inspector.MAX_INSPECTION_SECONDS / 2],
)
def test_total_monotonic_deadline_applies_after_listing_and_download(increment):
    clock = Clock(increment)
    client = FakeClient([metadata(history())], clock=clock)
    rejected("historical_inspection_timeout", client, monotonic=clock)


def test_new_module_has_no_cli_factory_scheduler_or_publisher_calls():
    tree = ast.parse(Path(inspector.__file__).read_text())
    forbidden = {
        "main",
        "_open_store",
        "_authorized_session",
        "_config",
        "record_daily_account_snapshot",
        "_scheduler_run",
        "_publish_account_facts",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in forbidden
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "snapshots"
            ):
                assert name in {
                    "_aware",
                    "_utc_now",
                    "_read_candidate",
                    "_validate_history_object",
                    "_is_fresh",
                    "_Rejected",
                }
        if isinstance(node, ast.Name):
            assert node.id != "__name__"


def test_recent_historical_read_still_never_confirms_current_health():
    client = FakeClient([metadata(history())])
    result = inspect(client, now=END + timedelta(minutes=1))
    assert result["observations"][0]["currently_stale"] is False
    assert result["current_health_confirmed"] is False


@pytest.mark.parametrize("generation", ["7", 2**64 - 1])
def test_valid_sdk_generation_shapes_preserve_cas(generation):
    client = FakeClient([metadata(history(), generation=generation)])
    result = inspect(client)
    assert result["observations"][0]["object_generation"] == int(generation)


@pytest.mark.parametrize(
    "change",
    [
        lambda payload: payload["source_binding"].update(private_id="SECRET"),
        lambda payload: payload["cash"][0].update(private_id="SECRET"),
        lambda payload: payload["broker_reported_balances"].append(
            deepcopy(payload["broker_reported_balances"][0])
        ),
        lambda payload: payload.update(
            financing=[{"currency": "USD", "buy_power": "1"}]
        ),
        lambda payload: payload.update(
            financing=[{"currency": "HKD", "buy_power": "NaN"}]
        ),
        lambda payload: payload["cash"][0].update(available_cash=True),
        lambda payload: payload["cash"][0].update(available_cash=1),
        lambda payload: payload["cash"][0].update(currency="hkd"),
    ],
)
def test_unknown_private_or_invalid_finite_money_fields_reject_whole_inspection(change):
    payload = history()
    change(payload)
    rejected("historical_object_invalid", FakeClient([metadata(payload)]))


def test_valid_optional_financing_keeps_archive_contract_without_money_projection():
    payload = history()
    payload["financing"] = [
        {"currency": "HKD", "risk_level": "1", "buy_power": "987.65"}
    ]
    result = inspect(FakeClient([metadata(payload)]))
    assert result["historical_window_match_count"] == 1
    assert "987.65" not in json.dumps(result)


@pytest.mark.parametrize(
    "start,finish",
    [
        (START, START + timedelta(minutes=16)),
        (NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)),
        (START + timedelta(seconds=2), START + timedelta(seconds=1)),
    ],
)
def test_object_timestamps_do_not_relax_original_observation_contract(start, finish):
    row, raw = metadata(history(start, finish))
    # Ensure invalid observations are actually listed in the requested historical day.
    if start.date() != START.date():
        payload = history(start, finish)
        row.name = (
            PREFIX + START.date().isoformat() + "/" + snapshots._filename_for(finish)
        )
        raw = json.dumps(payload).encode()
        row.size = len(raw)
    rejected("historical_object_invalid", FakeClient([(row, raw)]))


def test_duplicate_listing_object_rejects_unknown_coverage_before_read():
    row, raw = metadata(history())
    client = FakeClient([(row, raw)])
    client.list_blobs = lambda *_args, **_kwargs: SimpleNamespace(
        pages=iter([[row, row]]), next_page_token=None
    )
    rejected("historical_listing_invalid", client)
    assert client.calls == []


def test_byte_budget_rejected_before_any_read(monkeypatch):
    row, raw = metadata(history())
    monkeypatch.setattr(inspector, "MAX_TOTAL_BYTES", len(raw) - 1)
    client = FakeClient([(row, raw)])
    rejected("historical_listing_truncated", client)
    assert not any(call[0] == "download" for call in client.calls)


def test_list_failure_has_fixed_category_and_no_retry():
    client = FakeClient()
    count = []

    def failed(*_args, **_kwargs):
        count.append(1)
        raise RuntimeError("PRIVATE source details")

    client.list_blobs = failed
    rejected("historical_listing_failed", client)
    assert count == [1]


def test_blob_handle_failure_has_fixed_category_and_no_retry():
    client = FakeClient([metadata(history())])
    count = []

    def failed(*_args, **_kwargs):
        count.append(1)
        raise RuntimeError("PRIVATE source details")

    client.bucket = failed
    rejected("historical_read_failed", client)
    assert count == [1]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_unknown_monotonic_deadline_rejects_before_client_calls(value):
    client = FakeClient()
    rejected("historical_inspection_timeout", client, monotonic=lambda: value)
    assert client.calls == []


def test_bounded_listing_does_not_materialize_unbounded_iterator():
    row, raw = metadata(history())
    client = FakeClient([(row, raw)])
    count = []

    def rows():
        for _ in range(10000):
            count.append(1)
            yield row

    client.list_blobs = lambda *_args, **_kwargs: SimpleNamespace(
        pages=iter([rows()]), next_page_token=None
    )
    rejected("historical_listing_truncated", client)
    assert len(count) == snapshots.MAX_OBJECTS_PER_DAY + 1


class NativeStorageStubTransport:
    """Real locked SDK HTTP boundary; no auth, provider factory or network."""

    is_mtls = False

    def __init__(
        self, items=(), *, next_page_token=None, list_status=200, get_status=206
    ):
        self.items = dict((row.name, (row, raw)) for row, raw in items)
        self.next_page_token = next_page_token
        self.list_status = list_status
        self.get_status = get_status
        self.calls = []

    def request(self, method, url, **kwargs):
        from urllib.parse import parse_qs, unquote, urlsplit

        requests = pytest.importorskip("requests")
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        assert parts.scheme == "https" and parts.hostname == "storage.googleapis.com"
        assert method == "GET"
        assert 0 < kwargs["timeout"] <= snapshots.GCS_TIMEOUT_SECONDS
        response = requests.Response()
        response.headers["content-type"] = "application/json"
        response.request = requests.Request(method, url).prepare()
        response.url = url
        response._content_consumed = True
        response.raw = SimpleNamespace(headers=response.headers)
        if parts.path == "/storage/v1/b/" + BUCKET + "/o":
            self.calls.append(("list", query))
            assert query["maxResults"] == [str(snapshots.MAX_OBJECTS_PER_DAY + 1)]
            assert query["fields"] == ["items(name,generation,size),nextPageToken"]
            prefix = query["prefix"][0]
            assert prefix == PREFIX + START.date().isoformat() + "/"
            value = {
                "items": [
                    {
                        "name": row.name,
                        "generation": str(row.generation),
                        "size": str(row.size),
                    }
                    for row, _ in self.items.values()
                ]
            }
            if self.next_page_token:
                value["nextPageToken"] = self.next_page_token
            response.status_code = self.list_status
        elif parts.path.startswith("/download/storage/v1/b/" + BUCKET + "/o/"):
            name = unquote(parts.path.split("/o/", 1)[1])
            self.calls.append(("download", query))
            row, raw = self.items[name]
            assert query["generation"] == [str(row.generation)]
            assert query["ifGenerationMatch"] == [str(row.generation)]
            assert kwargs["headers"]["range"] == "bytes=0-65535"
            response.status_code = self.get_status
            response.headers["content-range"] = f"bytes 0-{len(raw) - 1}/{len(raw)}"
            response.headers["content-length"] = str(len(raw))
            response.headers["x-goog-generation"] = str(row.generation)
            response._content = raw
            return response
        else:
            raise AssertionError("unexpected SDK request path")
        if self.list_status != 200:
            value = {
                "error": {"code": self.list_status, "message": "PRIVATE native body"}
            }
        response._content = json.dumps(value).encode()
        return response


def native_client(transport):
    auth = pytest.importorskip("google.auth")
    core = pytest.importorskip("google.api_core")
    storage = pytest.importorskip("google.cloud.storage")
    from google.auth.credentials import AnonymousCredentials

    assert auth.__version__ == "2.55.1"
    assert core.__version__ == "2.31.0"
    assert storage.__version__ == "3.12.0"
    return storage.Client(
        project="longbridgequant", credentials=AnonymousCredentials(), _http=transport
    )


def test_native_sdk_single_use_page_property_empty_listing_reaches_transport_once():
    transport = NativeStorageStubTransport()
    result = inspect(native_client(transport))
    assert result["scanned_object_count"] == 0
    assert [call[0] for call in transport.calls] == ["list"]


def test_native_sdk_valid_list_and_fixed_generation_range_download():
    transport = NativeStorageStubTransport([metadata(history())])
    result = inspect(native_client(transport))
    assert result["scanned_object_count"] == 1
    assert result["observations"][0]["object_generation"] == 7
    assert [call[0] for call in transport.calls] == ["list", "download"]
    assert "1234.56" not in json.dumps(result) and BINDING not in json.dumps(result)


def test_native_sdk_truncated_listing_rejects_without_second_page_or_get():
    transport = NativeStorageStubTransport(next_page_token="PRIVATE")
    rejected("historical_listing_truncated", native_client(transport))
    assert [call[0] for call in transport.calls] == ["list"]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_native_sdk_http_failure_is_explicit_and_never_retried(status):
    transport = NativeStorageStubTransport(list_status=status)
    with pytest.raises(snapshots._Rejected) as caught:
        inspect(native_client(transport))
    assert caught.value.category == "historical_listing_failed"
    assert caught.value.__context__.code == status
    assert str(caught.value) == "historical_listing_failed"
    assert [call[0] for call in transport.calls] == ["list"]
