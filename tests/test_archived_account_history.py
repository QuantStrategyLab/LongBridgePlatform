import copy
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from scripts import publish_archived_account_history as archive

NOW = datetime(2026, 10, 26, tzinfo=UTC)
SOURCE = 'a' * 64
ENV = {'ACCOUNT_HISTORY_TARGET_ID': 'sample', 'ACCOUNT_HISTORY_EXPECTED_SCOPE': 'PAPER',
       'ARCHIVE_SOURCE_SHA256': hashlib.sha256(SOURCE.encode()).hexdigest(),
       'ARCHIVE_FROM': '2026-10-01', 'ARCHIVE_TO': '2026-10-21',
       'ACCOUNT_HISTORY_GCS_PREFIX': 'gs://synthetic-bucket/sample/account_snapshots',
       'ACCOUNT_FACTS_SYNC_URL': 'https://example.invalid/api/account-facts/sync',
       'ACCOUNT_FACTS_SYNC_TOKEN': 'synthetic-test-token'}


def fixture(end='2026-10-01T23:59:59+00:00'):
    payload = {'schema_version': 'longbridge_account_snapshot_history.v1',
               'snapshot_schema_version': 'longbridge_account_snapshot.v1',
               'account_scope': 'PAPER', 'target_id': 'sample', 'snapshot_atomic': False,
               'source_binding': {'kind': 'deployment_scope_token_version', 'status': 'bound', 'id': SOURCE},
               'observation_date': '2026-10-01', 'observed_started_at': '2026-10-01T23:59:00+00:00',
               'observed_finished_at': end, 'broker_reported_balances': [
                   {'currency': 'USD', 'net_assets': '123.12345678', 'total_cash': '-4.5000'}], 'cash': []}
    raw = json.dumps(payload).encode()
    stamp = datetime.fromisoformat(end).astimezone(UTC).strftime('%H%M%S%fZ.json')
    blob = SimpleNamespace(name=f'sample/account_snapshots/sample/{SOURCE}/2026-10-01/{stamp}',
                           generation='123', size=len(raw), raw=raw)
    return payload, raw, blob


@pytest.mark.parametrize('key,value', [('ARCHIVE_FROM', '2026-10-22'), ('ARCHIVE_FROM', '20261001'),
    ('ARCHIVE_FROM', '2025-01-01'), ('ARCHIVE_TO', '2026-10-26'), ('ARCHIVE_SOURCE_SHA256', 'unknown'),
    ('ACCOUNT_FACTS_SYNC_URL', 'https://elsewhere.invalid/path'),
    ('ACCOUNT_FACTS_SYNC_URL', 'https://example.invalid/api/account-facts/sync?redirect=1'),
    ('ACCOUNT_HISTORY_TARGET_ID', '../other'), ('ACCOUNT_FACTS_SYNC_TOKEN', '')])
def test_configuration_rejects_before_network(key, value):
    with pytest.raises(archive.ArchiveError, match='archive_config_invalid'):
        archive.configuration({**ENV, key: value}, NOW)


def test_selection_never_borrows_unrequested_source_and_bounds_listing(monkeypatch):
    cfg = archive.configuration(ENV, NOW)
    _, _, blob = fixture()
    other = copy.copy(blob)
    other.name = other.name.replace(SOURCE, 'b' * 64)
    assert archive.select_objects([other, blob], cfg) == [('2026-10-01', [blob])]
    with pytest.raises(archive.ArchiveError, match='archive_source_empty'):
        archive.select_objects([other], cfg)
    monkeypatch.setattr(archive, 'MAX_OBJECTS', 1)
    with pytest.raises(archive.ArchiveError, match='archive_listing_truncated'):
        archive.select_objects([other, blob], cfg)


def test_original_time_and_decimal_bytes_survive():
    cfg = archive.configuration(ENV, NOW)
    payload, raw, blob = fixture()
    assert archive.validate_bytes(raw, blob, '2026-10-01', cfg, NOW) == payload
    assert b'123.12345678' in raw and b'-4.5000' in raw
    for field, value in [('target_id', 'other'), ('account_scope', 'HK'),
                         ('observation_date', '2026-10-02'), ('observed_finished_at', '2027-01-01T00:00:00Z')]:
        changed = {**payload, field: value}
        changed_raw = json.dumps(changed).encode()
        changed_blob = copy.copy(blob)
        changed_blob.size = len(changed_raw)
        with pytest.raises(archive.ArchiveError, match='archive_object_invalid'):
            archive.validate_bytes(changed_raw, changed_blob, '2026-10-01', cfg, NOW)


def test_publish_exact_bytes_single_attempt_and_ack():
    cfg = archive.configuration(ENV, NOW)
    payload, raw, _ = fixture()
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_content(self, _):
            yield json.dumps({'ok': True, 'stored': True, 'unchanged': False, 'platform': 'longbridge',
                              'historical': True, 'latest_unchanged': True,
                              'target_id': 'sample', 'observation_date': payload['observation_date'],
                              'observed_finished_at': payload['observed_finished_at']}).encode()
    class Session:
        calls = 0
        def post(self, url, **kwargs):
            self.calls += 1
            assert url == 'https://example.invalid/api/account-facts/history/sync'
            assert kwargs['data'] is raw and kwargs['allow_redirects'] is False
            return Response()
    session = Session()
    assert archive.publish(raw, payload, cfg, session) is False and session.calls == 1
    class Unknown(Session):
        def post(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError('synthetic private body must not be logged')
    session = Unknown()
    with pytest.raises(archive.ArchiveError, match='archive_publish_unknown'):
        archive.publish(raw, payload, cfg, session)
    assert session.calls == 1


def test_rejected_response_is_not_followed_or_retried():
    cfg = archive.configuration(ENV, NOW)
    payload, raw, _ = fixture()
    class Response:
        status_code = 302
        def __enter__(self): return self
        def __exit__(self, *_): pass
    class Session:
        calls = 0
        def post(self, *args, **kwargs):
            self.calls += 1
            assert kwargs['allow_redirects'] is False
            return Response()
    session = Session()
    with pytest.raises(archive.ArchiveError, match='archive_publish_rejected'):
        archive.publish(raw, payload, cfg, session)
    assert session.calls == 1


@pytest.mark.parametrize('patch', [{'historical': False}, {'latest_unchanged': False}])
def test_ack_must_prove_history_only_contract(patch):
    cfg = archive.configuration(ENV, NOW)
    payload, raw, _ = fixture()
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_content(self, _):
            yield json.dumps({'ok': True, 'stored': True, 'unchanged': False, 'platform': 'longbridge',
                              'historical': True, 'latest_unchanged': True, **patch,
                              'target_id': 'sample', 'observation_date': payload['observation_date'],
                              'observed_finished_at': payload['observed_finished_at']}).encode()
    with pytest.raises(archive.ArchiveError, match='archive_ack_invalid'):
        archive.publish(raw, payload, cfg, SimpleNamespace(post=lambda *_args, **_kwargs: Response()))


@pytest.mark.parametrize('code,expected', [('account_facts_daily_conflict', 'account_facts_daily_conflict'),
                                         ('invalid_account_facts_money_scale', 'invalid_account_facts_money_scale'),
                                         ('PRIVATE-CREDENTIAL-SENTINEL', 'unclassified')])
def test_rejection_exposes_only_status_and_allowlisted_code(code, expected):
    cfg = archive.configuration(ENV, NOW)
    payload, raw, _ = fixture()
    class Response:
        status_code = 409
        headers = {'content-type': 'application/json; charset=utf-8'}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_content(self, _):
            yield json.dumps({'error': code, 'private_body': 'PRIVATE-CREDENTIAL-SENTINEL'}).encode()
    with pytest.raises(archive.ArchiveError) as caught:
        archive.publish(raw, payload, cfg, SimpleNamespace(post=lambda *_args, **_kwargs: Response()))
    assert caught.value.http_status == 409
    assert caught.value.receiver_code == expected
    assert caught.value.receiver_response_json is True
    assert 'PRIVATE-CREDENTIAL-SENTINEL' not in str(caught.value.__dict__)


def test_replay_uses_fixed_generations_and_handles_midnight(monkeypatch):
    cfg = archive.configuration(ENV, NOW)
    _, _, first = fixture()
    _, second_raw, second = fixture('2026-10-02T00:00:01+00:00')
    second.generation = '124'
    class Client:
        def list_blobs(self, *_args, **kwargs):
            assert kwargs['retry'] is None
            return [second, first]
        def bucket(self, _): return self
        def blob(self, name, generation):
            item = next(x for x in [first, second] if x.name == name)
            assert generation == int(item.generation)
            def download(**kwargs):
                assert kwargs['if_generation_match'] == generation and kwargs['retry'] is None
                return item.raw
            return SimpleNamespace(download_as_bytes=download)
    seen = []
    monkeypatch.setattr(archive, 'publish', lambda raw, *_: seen.append(raw) or False)
    result = archive.replay(cfg, Client(), None)
    assert seen == [second_raw] and result['inserted'] == 1
