"""Synthetic cloud adapter tests: no credentials, broker calls or real resources."""

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.stop_hk_runtime import StopError, execute_stop


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


class Cloud:
    def __init__(self):
        self.identity = {"platform_id": "longbridge", "deployment_selector": "synthetic-hk",
                         "account_selector": ["synthetic-account"], "account_scope": "HK",
                         "service_name": "longbridge-quant-hk-service"}
        self.target = {**self.identity, "strategy_profile": "synthetic-strategy"}
        self.request = {"target_id": "longbridge/hk", "runtime_target": self.identity,
                        "github": {"repository": "QuantStrategyLab/LongBridgePlatform",
                                   "variable_scope": "environment", "environment": "longbridge-hk"}}
        self.environment = {"RUNTIME_TARGET_ENABLED": "false", "RUNTIME_TARGET_JSON": json.dumps(self.target)}
        self.container = {"image": "synthetic-image@sha256:" + "a" * 64, "env": [
            {"name": "RUNTIME_TARGET_ENABLED", "value": "true"},
            {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(self.target)},
            {"name": "UNCHANGED", "value": "synthetic-only"}]}
        self.service = {"metadata": {"name": "longbridge-quant-hk-service"},
                        "spec": {"template": {"spec": {"containers": [self.container]}}},
                        "status": {"url": "https://synthetic.example", "latestReadyRevisionName": "synthetic-old",
                                   "latestCreatedRevisionName": "synthetic-old",
                                   "traffic": [{"revisionName": "synthetic-old", "percent": 100}]}}
        prefix = "projects/longbridgequant/locations/asia-east2/jobs/"
        self.jobs = [{"name": prefix + "synthetic-main", "state": "ENABLED",
                      "httpTarget": {"uri": "https://synthetic.example/run"}},
                     {"name": prefix + "synthetic-probe", "state": "PAUSED",
                      "httpTarget": {"uri": "https://synthetic.example/probe"}},
                     {"name": prefix + "synthetic-other", "state": "ENABLED",
                      "httpTarget": {"uri": "https://other.example/run"}}]
        self.calls = []
        self.fail_pause = False
        self.fail_update = False
        self.corrupt_after_update = False

    def run(self, command, **kwargs):
        self.calls.append(command)
        assert kwargs["capture_output"] and kwargs["timeout"] <= 60
        args = command[1:]
        if args[:3] == ["run", "services", "describe"]:
            payload = self.service
        elif args[:3] == ["run", "revisions", "describe"]:
            payload = {"spec": {"containers": [self.container]}}
        elif args[:3] == ["scheduler", "jobs", "list"]:
            payload = self.jobs
        elif args[:3] == ["run", "services", "update"]:
            if self.fail_update:
                raise subprocess.TimeoutExpired(command, 45, output="synthetic-private-error")
            self.container["env"][0]["value"] = "false"
            if self.corrupt_after_update:
                self.container["env"][2]["value"] = "changed"
            payload = self.service
        elif args[:3] == ["scheduler", "jobs", "pause"]:
            if self.fail_pause:
                return subprocess.CompletedProcess(command, 1, "", "synthetic-private-error")
            next(job for job in self.jobs if job["name"] == args[3])["state"] = "PAUSED"
            payload = {}
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    def writes(self):
        return [call for call in self.calls if "update" in call or "pause" in call]


class StopHkRuntimeTests(unittest.TestCase):
    def test_success_disables_only_bound_service_and_pauses_its_jobs(self):
        cloud = Cloud()
        result = execute_stop(cloud.request, cloud.environment, run=cloud.run)
        self.assertTrue(result["platform_applied"])
        self.assertEqual(result["scheduler_state"], "paused")
        self.assertEqual(result["in_flight_state"], "unknown")
        self.assertFalse(result["retirement_complete"])
        self.assertEqual(len(cloud.writes()), 2)
        self.assertIn("--update-env-vars=RUNTIME_TARGET_ENABLED=false", cloud.writes()[0])
        self.assertEqual(cloud.jobs[-1]["state"], "ENABLED")

    def test_old_request_does_not_write_a_correlated_result(self):
        cloud = Cloud()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            result = execute_stop(cloud.request, cloud.environment, run=cloud.run, result_path=path)
            self.assertTrue(result["platform_applied"])
            self.assertFalse(path.exists())

    def test_correlated_success_writes_only_the_verified_result(self):
        cloud = Cloud()
        cloud.identity["account_selector"] = ["乙", "甲"]
        cloud.target = {**cloud.identity, "strategy_profile": "synthetic-strategy"}
        cloud.request["runtime_target"] = cloud.identity
        cloud.environment["RUNTIME_TARGET_JSON"] = json.dumps(cloud.target, ensure_ascii=False)
        cloud.container["env"][1]["value"] = cloud.environment["RUNTIME_TARGET_JSON"]
        cloud.request["correlation"] = {
            "request_id": "00112233-4455-6677-8899-aabbccddeeff",
            "source_revision": 7,
            "source_identity_sha256": "ab" * 32,
        }
        cloud.environment.update({
            "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "4821",
            "GITHUB_SHA": "c" * 40,
        })
        moment = datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            result = execute_stop(
                cloud.request, cloud.environment, run=cloud.run, result_path=path, now=moment,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            raw = path.read_bytes()
        self.assertTrue(result["platform_applied"])
        self.assertNotIn("乙".encode(), raw)
        prefix = "projects/longbridgequant/locations/asia-east2/jobs/"
        jobs = [
            {"name": prefix + "synthetic-main", "state": "PAUSED", "uri": "https://synthetic.example/run"},
            {"name": prefix + "synthetic-probe", "state": "PAUSED", "uri": "https://synthetic.example/probe"},
        ]
        self.assertEqual(saved["schema_version"], "qsl_hk_stop_result.v1")
        self.assertEqual(saved["request_id"], "00112233-4455-6677-8899-aabbccddeeff")
        self.assertEqual(saved["source_revision"], 7)
        self.assertEqual(saved["source_identity_sha256"], "ab" * 32)
        self.assertEqual(saved["runtime_identity_sha256"], _digest(cloud.identity))
        self.assertNotEqual(saved["runtime_identity_sha256"], _digest(cloud.target))
        self.assertEqual(saved["readback"]["revision_name"], "synthetic-old")
        self.assertEqual(saved["readback"]["scheduler_count"], 2)
        self.assertEqual(saved["readback"]["scheduler_set_sha256"], _digest(jobs))
        self.assertTrue(saved["readback"]["complete"])
        self.assertFalse(saved["readback"]["runtime_enabled"])
        self.assertEqual(saved["observed_at"], "2026-09-28T06:00:00Z")
        self.assertEqual(saved["producer"]["run_id"], "4821")
        self.assertEqual(saved["producer"]["run_attempt"], 1)
        self.assertEqual(saved["in_flight_state"], "unknown")
        self.assertFalse(saved["retirement_complete"])
        self.assertNotIn("strategy_profile", json.dumps(saved))
        self.assertNotIn("synthetic-private", json.dumps(saved))

    def test_invalid_correlation_or_other_target_does_not_contact_cloud(self):
        valid = {
            "request_id": "00112233-4455-6677-8899-aabbccddeeff",
            "source_revision": 7,
            "source_identity_sha256": "ab" * 32,
        }
        mutations = [
            lambda c: c.request.update(target_id="longbridge/sg", correlation=valid),
            lambda c: c.request.update(correlation={**valid, "extra": 1}),
            lambda c: c.request.update(correlation={**valid, "source_revision": -1}),
            lambda c: c.request.update(correlation={**valid, "source_revision": True}),
            lambda c: c.request.update(correlation={**valid, "source_revision": 2**53}),
            lambda c: c.request.update(correlation={**valid, "request_id": "00112233-4455-6677-8899-AABBCCDDEEFF"}),
            lambda c: c.request.update(correlation={**valid, "source_identity_sha256": "AB" * 32}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                cloud = Cloud()
                mutation(cloud)
                with self.assertRaises(StopError):
                    execute_stop(cloud.request, cloud.environment, run=cloud.run, result_path=Path("result.json"))
                self.assertEqual(cloud.calls, [])

    def test_already_stopped_is_verified_without_writes(self):
        cloud = Cloud()
        cloud.container["env"][0]["value"] = "false"
        cloud.jobs[0]["state"] = "PAUSED"
        self.assertTrue(execute_stop(cloud.request, cloud.environment, run=cloud.run)["platform_applied"])
        self.assertEqual(cloud.writes(), [])

    def test_bad_request_or_enabled_desire_does_not_contact_cloud(self):
        for mutation in [lambda c: c.request.update(target_id="longbridge/sg"),
                         lambda c: c.environment.update(RUNTIME_TARGET_ENABLED="true"),
                         lambda c: c.request.update(enabled=True),
                         lambda c: c.request["github"].update(environment="longbridge-sg"),
                         lambda c: c.request["runtime_target"].update(account_selector=["other"]),
                         lambda c: c.environment.update(RUNTIME_TARGET_JSON="null")]:
            with self.subTest(mutation=mutation):
                cloud = Cloud()
                mutation(cloud)
                with self.assertRaises(StopError):
                    execute_stop(cloud.request, cloud.environment, run=cloud.run)
                self.assertEqual(cloud.calls, [])

    def test_ambiguous_or_mismatched_cloud_prevents_writes(self):
        for mutation in [lambda c: c.service["status"].update(traffic=[]),
                         lambda c: c.service["status"].update(latestCreatedRevisionName="pending"),
                         lambda c: c.container["env"][1].update(value='{"service_name":"other"}'),
                         lambda c: c.jobs[0].update(state="UNKNOWN"),
                         lambda c: c.jobs.clear(),
                         lambda c: c.container["env"].append(copy.deepcopy(c.container["env"][0]))]:
            with self.subTest(mutation=mutation):
                cloud = Cloud()
                mutation(cloud)
                with self.assertRaises(StopError):
                    execute_stop(cloud.request, cloud.environment, run=cloud.run)
                self.assertEqual(cloud.writes(), [])

    def test_unknown_update_is_not_retried_or_followed_by_other_writes(self):
        cloud = Cloud()
        cloud.fail_update = True
        with self.assertRaisesRegex(StopError, "^stop_write_unverified$"):
            execute_stop(cloud.request, cloud.environment, run=cloud.run)
        self.assertEqual(len(cloud.writes()), 1)

    def test_pause_failure_is_sanitized_and_stops(self):
        cloud = Cloud()
        cloud.fail_pause = True
        with self.assertRaisesRegex(StopError, "^stop_write_unverified$"):
            execute_stop(cloud.request, cloud.environment, run=cloud.run)
        self.assertEqual(len(cloud.writes()), 2)

    def test_unrelated_configuration_change_fails_readback(self):
        cloud = Cloud()
        cloud.corrupt_after_update = True
        cloud.request["correlation"] = {
            "request_id": "00112233-4455-6677-8899-aabbccddeeff",
            "source_revision": 0,
            "source_identity_sha256": "ab" * 32,
        }
        cloud.environment.update({
            "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "4821",
            "GITHUB_SHA": "c" * 40,
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(StopError, "^stop_readback_unverified$"):
                execute_stop(cloud.request, cloud.environment, run=cloud.run, result_path=path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
