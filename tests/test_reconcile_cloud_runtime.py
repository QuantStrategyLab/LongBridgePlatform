from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts import reconcile_cloud_runtime as reconcile


class ReconcileCloudRuntimeTest(unittest.TestCase):
    def test_scheduler_locations_include_cross_region_sources_and_dedupe(self) -> None:
        env = {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {"targets": [{"region": "europe-west1"}]}
            ),
            "SYNC_PLAN_JSON": json.dumps({"targets": [{"region": "asia-northeast1"}]}),
            "CLOUD_SCHEDULER_LEGACY_LOCATIONS": "asia-south1,asia-east1",
        }

        locations = reconcile._scheduler_locations(
            region="asia-east1",
            scheduler_location="asia-east2",
            targets=[reconcile.RuntimeTarget(service_name="longbridge-quant-paper-service", region="asia-east1")],
            env=env,
        )

        self.assertEqual(
            locations,
            [
                "asia-east2",
                "asia-east1",
                "europe-west1",
                "asia-northeast1",
                "asia-south1",
            ],
        )

    def test_ensure_latest_traffic_updates_to_latest_ready_revision_and_checks_commit(self) -> None:
        calls: list[tuple[tuple[str, ...], bool, bool]] = []
        describe_calls = 0

        def fake_run(args, *, json_output=False, dry_run=False):
            nonlocal describe_calls
            calls.append((tuple(args), json_output, dry_run))
            if args[1:4] == ["run", "services", "describe"]:
                describe_calls += 1
                if describe_calls == 1:
                    return {
                        "status": {
                            "latestReadyRevisionName": "longbridge-quant-paper-service-00002",
                            "traffic": [
                                {
                                    "revisionName": "longbridge-quant-paper-service-00001",
                                    "percent": 100,
                                }
                            ],
                        }
                    }
                return {
                    "status": {
                        "latestReadyRevisionName": "longbridge-quant-paper-service-00002",
                        "traffic": [
                            {
                                "revisionName": "longbridge-quant-paper-service-00002",
                                "percent": 100,
                                "latestRevision": True,
                            }
                        ],
                    }
                }
            if args[1:4] == ["run", "revisions", "describe"]:
                self.assertEqual(args[4], "longbridge-quant-paper-service-00002")
                return {
                    "metadata": {
                        "labels": {
                            "commit-sha": "abc123",
                        }
                    }
                }
            if args[1:4] == ["run", "services", "update-traffic"]:
                return {}
            self.fail(f"unexpected command: {args!r}")

        with patch.object(reconcile, "_run", side_effect=fake_run):
            reconcile.ensure_latest_traffic(
                project="longbridgequant",
                region="asia-east1",
                targets=[reconcile.RuntimeTarget(service_name="longbridge-quant-paper-service")],
                expected_commit="abc123",
                dry_run=False,
            )

        self.assertTrue(
            any(
                args[1:4] == ("run", "services", "update-traffic")
                for args, _, _ in calls
            )
        )
        self.assertEqual(describe_calls, 2)

    def test_ensure_latest_traffic_requires_latest_ready_revision(self) -> None:
        def fake_run(args, *, json_output=False, dry_run=False):
            if args[1:4] == ["run", "services", "describe"]:
                return {
                    "status": {
                        "latestCreatedRevisionName": "longbridge-quant-paper-service-00003",
                        "traffic": [],
                    }
                }
            self.fail(f"unexpected command: {args!r}")

        with patch.object(reconcile, "_run", side_effect=fake_run):
            with self.assertRaises(reconcile.ReconcileError):
                reconcile.ensure_latest_traffic(
                    project="longbridgequant",
                    region="asia-east1",
                    targets=[reconcile.RuntimeTarget(service_name="longbridge-quant-paper-service")],
                    expected_commit="abc123",
                    dry_run=False,
                )

    def test_delete_legacy_schedulers_targets_only_whitelisted_jobs(self) -> None:
        env = {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {"targets": [{"region": "europe-west1"}]}
            ),
            "SYNC_PLAN_JSON": json.dumps({"targets": [{"region": "asia-northeast1"}]}),
            "CLOUD_SCHEDULER_LEGACY_LOCATIONS": "asia-south1",
        }
        delete_commands: list[tuple[str, str]] = []

        def fake_run_optional(args, *, dry_run=False):
            return True

        def fake_run(args, *, json_output=False, dry_run=False):
            if args[1:4] == ["scheduler", "jobs", "delete"]:
                delete_commands.append((args[4], args[6].split("=", 1)[1]))
                return {}
            self.fail(f"unexpected command: {args!r}")

        with patch.object(reconcile, "_run_optional", side_effect=fake_run_optional), patch.object(
            reconcile, "_run", side_effect=fake_run
        ):
            reconcile.delete_legacy_schedulers(
                platform="longbridge",
                project="longbridgequant",
                region="asia-east1",
                scheduler_location="asia-east2",
                targets=[
                    reconcile.RuntimeTarget(
                        service_name="longbridge-quant-paper-service",
                        region="asia-east1",
                    )
                ],
                env=env,
                dry_run=False,
            )

        self.assertEqual(
            delete_commands,
            [
                ("longbridge-quant-paper-probe-scheduler", "asia-east2"),
                ("longbridge-quant-paper-probe-scheduler", "asia-east1"),
                ("longbridge-quant-paper-probe-scheduler", "europe-west1"),
                ("longbridge-quant-paper-probe-scheduler", "asia-northeast1"),
                ("longbridge-quant-paper-probe-scheduler", "asia-south1"),
                ("longbridge-quant-paper-precheck-scheduler", "asia-east2"),
                ("longbridge-quant-paper-precheck-scheduler", "asia-east1"),
                ("longbridge-quant-paper-precheck-scheduler", "europe-west1"),
                ("longbridge-quant-paper-precheck-scheduler", "asia-northeast1"),
                ("longbridge-quant-paper-precheck-scheduler", "asia-south1"),
                ("lb-paper-backup-execution", "asia-east2"),
                ("lb-paper-backup-execution", "asia-east1"),
                ("lb-paper-backup-execution", "europe-west1"),
                ("lb-paper-backup-execution", "asia-northeast1"),
                ("lb-paper-backup-execution", "asia-south1"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
