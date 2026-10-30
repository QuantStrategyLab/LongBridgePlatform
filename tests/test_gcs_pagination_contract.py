"""Offline contract test for the pinned Google Cloud Storage page iterator."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from application.validation_cycle_observation import strict_prior_report_exists
from quant_platform_kit.cloud.gcp_provider import GcpObjectStore


class GcsPaginationContractTests(unittest.TestCase):
    def test_real_sdk_uses_bounded_lazy_pages_without_network(self):
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import storage

        transport = Mock()
        transport.request.side_effect = AssertionError("network access is forbidden")
        client = storage.Client(project="synthetic-test", credentials=AnonymousCredentials(), _http=transport)
        objects = GcpObjectStore(project_id="synthetic-test")
        objects._client = client
        marker_store = SimpleNamespace(cloud_prefix_uri="gs://synthetic-bucket/root", _object_store=lambda: objects)
        prefix = "root/longbridge/strategy/PAPER/"
        calls = []

        def api_request(*args, **kwargs):
            calls.append(kwargs)
            self.assertEqual(kwargs["method"], "GET")
            self.assertEqual(kwargs["path"], "/b/synthetic-bucket/o")
            params = kwargs["query_params"]
            self.assertEqual(params["prefix"], prefix)
            if len(calls) == 1:
                self.assertNotIn("pageToken", params)
                self.assertEqual(int(params["maxResults"]), 3)
                return {"items": [{"name": prefix + "1.txt"}, {"name": prefix + "2.txt"}],
                        "nextPageToken": "next-page"}
            if len(calls) == 2:
                self.assertEqual(params["pageToken"], "next-page")
                self.assertEqual(int(params["maxResults"]), 1)
                return {"items": [{"name": prefix + "3.txt"}], "nextPageToken": "must-not-request"}
            self.fail("scanner requested a third page")

        with patch.object(client._connection, "api_request", side_effect=api_request), \
             patch("application.validation_cycle_observation.STRICT_REPORT_SCAN_LIMIT", 2):
            with self.assertRaisesRegex(RuntimeError, "^validation_journal_read_failed$"):
                strict_prior_report_exists(marker_store, platform="longbridge", strategy_profile="strategy",
                                           account_scope="PAPER", signal_date="2026-01-31",
                                           effective_date="2026-02-02", dry_run_only=True)
        self.assertEqual(len(calls), 2)
        transport.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
