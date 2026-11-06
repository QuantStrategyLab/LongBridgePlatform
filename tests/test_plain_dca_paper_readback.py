"""Synthetic protected/cloud metadata; no network, credentials or broker."""
import json
from copy import deepcopy
from datetime import UTC, datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from scripts.inspect_plain_dca_paper import ReadbackError, container_env, main, observe


class PlainDcaReadbackTests(TestCase):
    def test_workflow_uses_dedicated_protected_readback_contract(self):
        from pathlib import Path
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/inspect-paper-serving-metadata.yml").read_text()
        self.assertIn("EXPECTED_PHYSICAL_ACCOUNT_ID: ${{ secrets.PAPER_READBACK_PHYSICAL_ACCOUNT_ID }}", workflow)
        self.assertIn("WORKFLOW_ENVIRONMENT: longbridge-paper", workflow)
        self.assertNotIn("    environment:", workflow)
        self.assertNotIn("environment: longbridge-sg", workflow)

    def setUp(self):
        self.target = {"platform_id": "longbridge", "service_name": "fake-paper-service",
                       "execution_environment": "paper", "execution_mode": "live",
                       "dry_run_only": False, "account_scope": "PAPER",
                       "account_selector": ["PAPER"], "deployment_selector": "PAPER"}
        self.env = {"WORKFLOW_ENVIRONMENT": "longbridge-paper", "CLOUD_RUN_SERVICE": "fake-paper-service",
                    "CLOUD_RUN_REGION": "fake-region", "GCP_PROJECT_ID": "fake-project",
                    "EXPECTED_PHYSICAL_ACCOUNT_ID": "fake-paper-account",
                    "EXPECTED_RUNTIME_TARGET_JSON": json.dumps(self.target),
                    "EXPECTED_RUNTIME_TARGET_ENABLED": "false", "EXPECTED_DRY_RUN_ONLY": "false"}
        values = {"RUNTIME_TARGET_JSON": json.dumps(self.target), "RUNTIME_TARGET_ENABLED": "false",
                  "LONGBRIDGE_DRY_RUN_ONLY": "false", "LONGBRIDGE_PHYSICAL_ACCOUNT_ID": "fake-paper-account",
                  "LONGBRIDGE_DURABLE_EXECUTION_COMMAND_LIVE_ENABLED": "false"}
        spec = {"containers": [{"env": [{"name": k, "value": v} for k, v in values.items()]}]}
        self.service = {"metadata": {"name": "fake-paper-service"}, "spec": {"template": {"spec": spec}},
                        "status": {"traffic": [{"revisionName": "fake-paper-service-r1", "percent": 100}]}}
        self.revision = {"metadata": {"name": "fake-paper-service-r1"}, "spec": deepcopy(spec)}
        from types import SimpleNamespace
        paper = SimpleNamespace(id="paper", mode="paper", environment="longbridge-paper",
                                service="fake-paper-service", region="fake-region")
        self.manifest = patch("scripts.inspect_plain_dca_paper.load_runtime_target_manifest",
                              return_value=SimpleNamespace(targets=[paper]))
        self.manifest.start()
        self.addCleanup(self.manifest.stop)
        self.calls = []

    def load(self, command):
        self.calls.append(command)
        return deepcopy(self.service if command[2] == "services" else self.revision)

    def test_real_reader_shape_stays_partial_without_authoritative_safety(self):
        result = observe(env=self.env, load=self.load)
        self.assertTrue(result["configured_paper_identity_verified"])
        self.assertFalse(result["live_command_enabled"])
        self.assertIsNone(result["kelly_enabled"])
        self.assertIsNone(result["stop_loss_enabled"])
        self.assertFalse(result["paper_start_ready"])
        self.assertFalse(result["broker_identity_independently_verified"])
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all(cmd[3] == "describe" for cmd in self.calls))
        self.assertNotIn("fake-", json.dumps(result))
        self.assertLessEqual(datetime.fromisoformat(result["observed_finished_at"]), datetime.now(UTC))

    def test_sg_and_missing_protected_fields_stop_before_cloud(self):
        for patch_values in ({"WORKFLOW_ENVIRONMENT": "longbridge-sg"}, {"CLOUD_RUN_SERVICE": "fake-sg"},
                             {"EXPECTED_PHYSICAL_ACCOUNT_ID": ""}, {"EXPECTED_RUNTIME_TARGET_ENABLED": ""},
                             {"EXPECTED_DRY_RUN_ONLY": ""}):
            with self.subTest(patch_values=patch_values), self.assertRaises(ReadbackError):
                observe(env={**self.env, **patch_values}, load=self.load)
            self.assertEqual(self.calls, [])

    def test_serving_account_or_enabled_target_mismatch_blocks(self):
        for key, value in (("LONGBRIDGE_PHYSICAL_ACCOUNT_ID", "fake-other"), ("RUNTIME_TARGET_ENABLED", "true")):
            original = deepcopy(self.revision)
            for entry in self.revision["spec"]["containers"][0]["env"]:
                if entry["name"] == key:
                    entry["value"] = value
            with self.assertRaises(ReadbackError):
                observe(env=self.env, load=self.load)
            self.revision = original

    def test_duplicate_keys_and_unresolved_reference_cannot_prove_identity(self):
        spec = self.revision["spec"]
        entries = spec["containers"][0]["env"]
        entries.append(deepcopy(entries[0]))
        with self.assertRaises(ReadbackError):
            container_env(spec)
        entries.pop()
        entries[0] = {"name": "RUNTIME_TARGET_JSON", "valueFrom": {"secretKeyRef": {"name": "fake-secret"}}}
        with self.assertRaises(ReadbackError):
            observe(env=self.env, load=self.load)

    def test_unknown_live_switch_is_not_off(self):
        self.revision["spec"]["containers"][0]["env"].pop()
        self.assertIsNone(observe(env=self.env, load=self.load)["live_command_enabled"])

    def test_qsl_overrides_cannot_hide_enabled_or_unresolved_configuration(self):
        for key in ("QSL_RUNTIME_TARGET_ENABLED", "QSL_LONGBRIDGE_DRY_RUN_ONLY"):
            for value in ("true", None, ""):
                entries = self.revision["spec"]["containers"][0]["env"]
                entries.append({"name": key, "value": value})
                with self.subTest(key=key, value=value), self.assertRaises(ReadbackError):
                    observe(env=self.env, load=self.load)
                entries.pop()
        entries = self.revision["spec"]["containers"][0]["env"]
        entries.append({"name": "QSL_RUNTIME_TARGET_ENABLED", "value": "false"})
        self.assertFalse(observe(env=self.env, load=self.load)["runtime_target_enabled"])

    def test_service_or_revision_drift_blocks(self):
        for family in ("services", "revisions"):
            counts = {"services": 0, "revisions": 0}
            def drift(cmd, family=family, counts=counts):
                counts[cmd[2]] += 1
                result = self.load(cmd)
                if cmd[2] == family and counts[family] > 1:
                    result["metadata"]["fake-drift"] = True
                return result
            with self.assertRaises(ReadbackError):
                observe(env=self.env, load=drift)

    def test_failure_diagnostic_does_not_echo_cloud_exception(self):
        import io
        output = io.StringIO()
        with patch("scripts.inspect_plain_dca_paper.observe", side_effect=RuntimeError("fake-private-value")), patch("sys.stdout", output):
            self.assertEqual(main(), 1)
        self.assertNotIn("fake-private-value", output.getvalue())
        self.assertFalse(json.loads(output.getvalue())["paper_start_ready"])
