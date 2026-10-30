import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from application.validation_cycle_observation import strict_prior_report_exists
from quant_platform_kit.cloud.gcp_provider import GcpObjectStore


class FakeGcp(GcpObjectStore):
    def __init__(self, objects, *, list_error=None):
        self.objects = objects
        self.list_error = list_error
        self.prefix = None
        self.reads = []

    def list(self, prefix):
        self.prefix = prefix
        if self.list_error:
            raise self.list_error
        return list(self.objects)

    def read_text(self, uri):
        self.reads.append(uri)
        value = self.objects[uri]
        if isinstance(value, Exception):
            raise value
        return value


def report(*, signal='2026-01-31', effective='2026-02-02', action=False):
    return json.dumps({'platform': 'longbridge', 'strategy_profile': 'strategy', 'account_scope': 'PAPER',
                       'dry_run': True, 'status': 'ok',
                       'summary': {'signal_date': signal, 'effective_date': effective, 'action_done': action,
                                   'order_events_count': 0, 'orders_previewed_count': 0, 'orders_skipped_count': 0}})


class StrictReportTests(unittest.TestCase):
    def query(self, cloud, **changes):
        store = SimpleNamespace(cloud_prefix_uri='gs://bucket/root', _object_store=lambda: cloud)
        args = dict(platform='longbridge', strategy_profile='strategy', account_scope='PAPER',
                    signal_date='2026-01-31', effective_date='2026-02-02', dry_run_only=True)
        args.update(changes)
        return strict_prior_report_exists(store, **args)

    def test_scans_all_months_and_more_than_100_reports(self):
        prefix = 'gs://bucket/root/longbridge/strategy/PAPER/'
        objects = {f'{prefix}2026-01/{i:03}.json': report(signal='2025-01-01') for i in range(100)}
        objects[prefix + '2026-02/100.json'] = report()
        cloud = FakeGcp(objects)
        self.assertTrue(self.query(cloud))
        self.assertEqual(cloud.prefix, prefix)
        self.assertEqual(len(cloud.reads), 101)
        objects = {prefix + 'unknown-month/run.json': report()}
        self.assertTrue(self.query(FakeGcp(objects)))

    def test_not_found_requires_all_candidates_read_and_valid(self):
        prefix = 'gs://bucket/root/longbridge/strategy/PAPER/'
        cloud = FakeGcp({prefix + '2025-01/1.json': report(signal='2025-01-01'),
                         prefix + '2026-02/2.json': report(effective='2026-02-03')})
        self.assertFalse(self.query(cloud))
        self.assertEqual(len(cloud.reads), 2)
        self.assertFalse(self.query(FakeGcp({})))

    def test_errors_and_unsupported_source_fail_closed(self):
        prefix = 'gs://bucket/root/longbridge/strategy/PAPER/'
        for cloud in (FakeGcp({}, list_error=OSError('secret')),
                      FakeGcp({prefix+'broken.json': OSError('secret')}),
                      FakeGcp({prefix+'broken.json': '{'}),
                      FakeGcp({prefix+'broken.json': '[]'}),
                      FakeGcp({prefix+'broken.json': '{}'}),
                      FakeGcp({prefix+'broken.json': json.dumps({
                          'platform': 'longbridge', 'strategy_profile': 'strategy',
                          'account_scope': 'PAPER', 'dry_run': True, 'status': 'ok', 'summary': {}})}),
                      object()):
            with self.subTest(cloud=cloud), self.assertRaisesRegex(RuntimeError, '^validation_journal_read_failed$'):
                self.query(cloud)
        with self.assertRaisesRegex(RuntimeError, '^validation_journal_read_failed$'):
            self.query(FakeGcp({}), account_scope='')

    def test_scan_limit_is_unknown_not_negative(self):
        prefix = 'gs://bucket/root/longbridge/strategy/PAPER/'
        cloud = FakeGcp({f'{prefix}{i}.json': report(signal='2025-01-01') for i in range(3)})
        with patch('application.validation_cycle_observation.STRICT_REPORT_SCAN_LIMIT', 2):
            with self.assertRaisesRegex(RuntimeError, '^validation_journal_read_failed$'):
                self.query(cloud)

    def test_gcp_listing_later_page_failure_is_unknown(self):
        cloud = GcpObjectStore()

        def blobs(*, prefix):
            yield SimpleNamespace(name=prefix + '2026-01/one.json')
            raise OSError('later page failed')

        cloud._client = SimpleNamespace(bucket=lambda _bucket: SimpleNamespace(list_blobs=blobs))
        with self.assertRaisesRegex(RuntimeError, '^validation_journal_read_failed$'):
            self.query(cloud)

    def test_pinned_matcher_accepts_signal_snapshot_and_successful_no_action(self):
        prefix = 'gs://bucket/root/longbridge/strategy/PAPER/'
        payload = json.loads(report())
        payload['summary'] = {'action_done': False}
        payload['diagnostics'] = {'signal_snapshot': {
            'signal_as_of': '2026-01-31', 'effective_date': '2026-02-02'}}
        cloud = FakeGcp({prefix + '2026-03/late.json': json.dumps(payload)})
        self.assertTrue(self.query(cloud))
        payload['dry_run'] = False
        self.assertFalse(self.query(FakeGcp({prefix + '2026-03/late.json': json.dumps(payload)})))

    def test_indeterminate_result_and_malformed_counts_fail_closed(self):
        prefix = 'gs://bucket/root/longbridge/strategy/PAPER/'
        base = json.loads(report())
        dates = {'signal_date': '2026-01-31', 'effective_date': '2026-02-02'}
        cases = []
        for status in ('bogus', 'pending', 'error', 'skipped'):
            cases.append({**base, 'status': status, 'summary': dates})
        for status in ('error', 'skipped'):
            cases.append({**base, 'status': status, 'summary': {
                **dates, 'action_done': False, 'order_events_count': 0,
                'orders_previewed_count': 0, 'orders_skipped_count': 0}})
        for value in (True, -1, 0.5, float('nan'), '2', None):
            for key in ('order_events_count', 'orders_previewed_count', 'orders_skipped_count', 'orders_pending_count'):
                cases.append({**base, 'summary': {**dates, 'action_done': False, key: value}})
        for value in ('false', 0, None):
            cases.append({**base, 'summary': {**dates, 'action_done': value}})
        cases.append({**base, 'summary': dates})
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaisesRegex(RuntimeError, '^validation_journal_read_failed$'):
                self.query(FakeGcp({prefix + '2026-02/test.json': json.dumps(payload)}))

    def test_valid_counts_and_no_action_match_pinned_reader(self):
        prefix = 'gs://bucket/root/longbridge/strategy/PAPER/'
        base = json.loads(report())
        dates = {'signal_date': '2026-01-31', 'effective_date': '2026-02-02'}
        for status, result in (('ok', {'action_done': False}),
                               ('ok', {'orders_previewed_count': 1}),
                               ('error', {'order_events_count': 1})):
            payload = {**base, 'status': status, 'summary': {**dates, **result}}
            with self.subTest(status=status, result=result):
                self.assertTrue(self.query(FakeGcp({prefix + '2026-02/test.json': json.dumps(payload)})))


if __name__ == '__main__':
    unittest.main()
