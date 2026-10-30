import json
import subprocess
import unittest

from scripts.paper_request_readback import inspect


class ValidationReadbackTests(unittest.TestCase):
    def test_trace_matched_cycle_exposes_only_allowlisted_facts(self):
        trace = "projects/longbridgequant/traces/" + "a" * 32
        revision = "longbridge-quant-paper-service-00001-abc"
        payload = {"schema": "longbridge.validation-cycle-observation.v1", "adapter_instrumented": True,
                   "adapter_submit_attempt_count": 0, "cycle_entered": True, "cycle_returned": True,
                   "execution_receipt_present": True, "report_status": "ok", "broker_submit_count": None,
                   "private": "never-output"}

        def run(args, **kwargs):
            if args[1:3] == ["run", "revisions"]:
                body = {"metadata": {"name": revision, "labels": {"serving.knative.dev/service": "longbridge-quant-paper-service", "commit-sha": "b" * 40}}}
            elif 'jsonPayload.event=' in args[3]:
                body = [{"jsonPayload": payload}]
            else:
                body = [{"trace": trace, "timestamp": "2026-10-30T05:25:17Z", "resource": {"labels": {"revision_name": revision}},
                         "httpRequest": {"requestMethod": "POST", "requestUrl": "https://private.example/dry-run", "status": 200}}]
            self.assertFalse(kwargs.get("shell", False))
            return subprocess.CompletedProcess(args, 0, json.dumps(body), "")

        result = inspect("2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run=run)
        cycle = result["requests"][0]["validation_cycle_observation"]
        self.assertEqual(cycle["adapter_submit_attempt_count"], 0)
        self.assertTrue(cycle["cycle_returned"])
        self.assertEqual(cycle["trace_correlation"], "unverified")
        self.assertIsNone(cycle["broker_submit_count"])
        self.assertNotIn("never-output", json.dumps(result))
        self.assertNotIn(trace, json.dumps(result))

    def test_ambiguous_and_invalid_payloads_remain_unknown(self):
        from scripts.paper_request_readback import _cycle_observation
        trace = "projects/longbridgequant/traces/" + "a" * 32
        revision = "longbridge-quant-paper-service-00001-abc"
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, json.dumps([{}, {}]), "")
        self.assertEqual(_cycle_observation(trace, revision, "2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run)["status"], "unknown")
        self.assertEqual(_cycle_observation("invalid", revision, "2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
