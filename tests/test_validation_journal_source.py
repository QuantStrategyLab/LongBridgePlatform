import unittest
from types import SimpleNamespace

from application.validation_cycle_observation import require_matching_journal_source


class ValidationJournalSourceTests(unittest.TestCase):
    def test_requires_matching_cloud_prefixes(self):
        cases = [
            (None, 'gs://bucket/reports'),
            ('gs://bucket/reports', None),
            ('', ''),
            ('/tmp/journal', '/tmp/journal'),
            ('gs://bucket/a', 'gs://bucket/b'),
            ('gs://other/a', 'gs://bucket/a'),
            ('gs://', 'gs://'),
            ('gs://bucket/reports?x=1', 'gs://bucket/reports?x=1'),
            ('gs://user@bucket/reports', 'gs://user@bucket/reports'),
        ]
        for marker_uri, report_uri in cases:
            with self.subTest(marker_uri=marker_uri, report_uri=report_uri):
                config = SimpleNamespace(execution_state_store=SimpleNamespace(cloud_prefix_uri=marker_uri))
                reporting = SimpleNamespace(report_gcs_prefix_uri=report_uri)
                with self.assertRaisesRegex(RuntimeError, '^validation_journal_unavailable$'):
                    require_matching_journal_source(config, reporting)

    def test_accepts_same_prefix_with_trailing_slash(self):
        config = SimpleNamespace(execution_state_store=SimpleNamespace(cloud_prefix_uri='gs://bucket/reports/'))
        reporting = SimpleNamespace(report_gcs_prefix_uri='gs://bucket/reports')
        require_matching_journal_source(config, reporting)
