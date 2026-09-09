"""Synthetic cloud adapter tests: no credentials, broker calls or real resources."""

import copy
import json
import subprocess
import unittest

from scripts.stop_hk_runtime import StopError, execute_stop


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
        with self.assertRaisesRegex(StopError, "^stop_readback_unverified$"):
            execute_stop(cloud.request, cloud.environment, run=cloud.run)


if __name__ == "__main__":
    unittest.main()
