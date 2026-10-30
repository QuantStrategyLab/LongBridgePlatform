"""Fixed-enum failure evidence must preserve admission and privacy boundaries."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_deployed_runtime_target_admission.py"
SPEC = importlib.util.spec_from_file_location("image_only_diagnostics_admission", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
admission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admission)

PRIVATE = "private-target/account-fixture/secret-fixture"
SHA = "a" * 40


class ImageOnlyAdmissionDiagnosticsTest(unittest.TestCase):
    def cli(self, output):
        return ["--project", "synthetic-project", "--region", "synthetic-region",
                "--service", PRIVATE, "--image-only-staging", "--plan-out", str(output)]

    def test_staging_failure_reports_only_enums_and_keeps_rejection(self):
        cases = [
            ("_validate_image_only_source", "source_validation", "image_source_not_admitted"),
            ("_run_quiet", "service_observation", "external_read_unavailable"),
            ("prepare_image_only_staging", "staging_validation", "candidate_target_binding_mismatch"),
            ("prepare_image_only_staging", "staging_validation", "candidate_serving_ues_mismatch"),
            ("prepare_image_only_staging", "staging_validation", "serving_identity_unavailable"),
            ("prepare_image_only_staging", "staging_validation", "source_declarations_invalid"),
            ("prepare_image_only_staging", "staging_validation", "template_mismatch"),
            ("prepare_image_only_staging", "staging_validation", "target_identity_invalid"),
            ("_write_plan", "plan_write", "admission_unavailable"),
        ]
        for function, phase, reason in cases:
            with self.subTest(function=function, reason=reason), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "plan.json"
                error = OSError(PRIVATE) if function == "_write_plan" else admission.AdmissionError(PRIVATE, reason=reason)
                stderr = io.StringIO()
                with patch.object(admission, "_validate_image_only_source") as validate, \
                     patch.object(admission, "_run_quiet", return_value="{}") as read, \
                     patch.object(admission, "prepare_image_only_staging", return_value={}) as prepare, \
                     patch.object(admission, "_write_plan") as write, \
                     patch.dict(admission.os.environ, {"SOURCE_COMMIT": SHA}, clear=True), redirect_stderr(stderr):
                    functions = {"_validate_image_only_source": validate, "_run_quiet": read,
                                 "prepare_image_only_staging": prepare, "_write_plan": write}
                    functions[function].side_effect = error
                    self.assertEqual(admission.main(self.cli(output)), 1)
                lines = stderr.getvalue().splitlines()
                self.assertEqual(lines[0], "Image-only staging admission failed.")
                self.assertEqual(json.loads(lines[1]), {
                    "schema": "longbridge.image-only-admission.v1", "status": "rejected",
                    "phase": phase, "reason": reason,
                })
                self.assertNotIn(PRIVATE, stderr.getvalue())
                self.assertNotIn(SHA, stderr.getvalue())
                self.assertFalse(output.exists())
                if function != "_write_plan":
                    write.assert_not_called()
                if function == "_validate_image_only_source":
                    read.assert_not_called()

    def test_unknown_or_malformed_reason_does_not_render_exception(self):
        class UnrenderableError(admission.AdmissionError):
            def __str__(self):
                raise AssertionError("Exception text must not be inspected")

        for reason in (PRIVATE, None, [PRIVATE], {"private": PRIVATE}):
            with self.subTest(reason_type=type(reason).__name__):
                error = UnrenderableError(PRIVATE)
                error.diagnostic_reason = reason
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    admission._report_staging_failure(error, phase="staging_validation")
                self.assertEqual(json.loads(stderr.getvalue())["reason"], "admission_unavailable")
                self.assertNotIn(PRIVATE, stderr.getvalue())

    def test_optional_diagnostic_failure_does_not_raise(self):
        with patch.object(admission.json, "dumps", side_effect=ValueError(PRIVATE)):
            admission._report_staging_failure(admission.AdmissionError(PRIVATE), phase="plan_write")

    def test_success_keeps_plan_and_call_sequence(self):
        calls = []
        plan = {"image_commit": SHA, "serving_traffic": [], "history_values": {}}
        def validate(**kwargs):
            calls.append("validate")
        def read(command):
            calls.append("read")
            self.assertEqual(command[:4], ["gcloud", "run", "services", "describe"])
            return "{}"
        def prepare(**kwargs):
            calls.append("prepare")
            self.assertIs(kwargs["run"], read)
            return plan
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.json"
            stderr = io.StringIO()
            with patch.object(admission, "_validate_image_only_source", validate), \
                 patch.object(admission, "_run_quiet", read), \
                 patch.object(admission, "prepare_image_only_staging", prepare), \
                 patch.dict(admission.os.environ, {"SOURCE_COMMIT": SHA}, clear=True), redirect_stderr(stderr):
                self.assertEqual(admission.main(self.cli(output)), 0)
            self.assertEqual(calls, ["validate", "read", "prepare"])
            self.assertEqual(json.loads(output.read_text()), plan)
            self.assertEqual(stderr.getvalue(), "")

    def test_actual_source_and_binding_rejections_have_fixed_reasons(self):
        with self.assertRaises(admission.AdmissionError) as caught:
            admission._locked_ues_revision("invalid lock contents")
        self.assertEqual(caught.exception.diagnostic_reason, "source_declarations_invalid")
        target = {"strategy_release": {"strategy_revision": "b" * 40}}
        service = {"spec": {"template": {"spec": {"containers": [{"env": [
            {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)}
        ]}]}}}}
        with self.assertRaises(admission.AdmissionError) as caught:
            admission._require_candidate_release_binding(
                service=PRIVATE, service_json=service, profile="russell_top50_leader_rotation", ues_revision=SHA
            )
        self.assertEqual(caught.exception.diagnostic_reason, "candidate_target_binding_mismatch")
        with self.assertRaises(admission.AdmissionError) as caught:
            admission._require_paper_target_identity(service=PRIVATE, service_json=service)
        self.assertEqual(caught.exception.diagnostic_reason, "target_identity_invalid")
        with self.assertRaises(admission.AdmissionError) as caught:
            admission.verify_template_matches_serving(service=PRIVATE, service_json=service, serving_revisions=[])
        self.assertEqual(caught.exception.diagnostic_reason, "template_mismatch")

    def test_readback_keeps_original_failure_output(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            plan.write_text("{}")
            stderr = io.StringIO()
            with patch.object(admission, "confirm_image_only_readback", side_effect=admission.AdmissionError(PRIVATE)), \
                 redirect_stderr(stderr):
                self.assertEqual(admission.main([
                    "--project", "synthetic-project", "--region", "synthetic-region",
                    "--service", PRIVATE, "--image-only-readback", "--plan", str(plan),
                ]), 1)
            self.assertEqual(stderr.getvalue(), "Image-only staging readback failed.\n")

    def test_malformed_binding_is_not_reported_as_compared_version_mismatch(self):
        for raw in ("{", "[]", json.dumps({"strategy_release": [PRIVATE]}),
                    json.dumps({"runtime_risk_limits": {"binding": [PRIVATE]}})):
            with self.subTest(raw_type=type(raw).__name__):
                service = {"spec": {"template": {"spec": {"containers": [{"env": [
                    {"name": "RUNTIME_TARGET_JSON", "value": raw}
                ]}]}}}}
                with self.assertRaises(admission.AdmissionError) as caught:
                    admission._require_candidate_release_binding(service=PRIVATE, service_json=service,
                        profile="russell_top50_leader_rotation", ues_revision=SHA)
                self.assertEqual(caught.exception.diagnostic_reason, "target_binding_invalid")
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    admission._report_staging_failure(caught.exception, phase="staging_validation")
                self.assertEqual(json.loads(stderr.getvalue())["reason"], "target_binding_invalid")
                self.assertNotIn(PRIVATE, stderr.getvalue())

    def test_real_staging_path_rejects_serving_pin_without_extra_calls(self):
        profile = "russell_top50_leader_rotation"
        target = {"platform_id": "longbridge", "service_name": "paper-service",
                  "account_scope": "PAPER", "account_selector": ["PAPER"],
                  "strategy_profile": profile, "execution_mode": "live", "dry_run_only": False}
        env = [{"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
               {"name": "STRATEGY_PROFILE", "value": profile},
               {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": "false"}]
        spec = {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env}]}
        service = {"metadata": {"annotations": {"run.googleapis.com/ingress": "internal"}},
                   "spec": {"template": {"spec": spec}},
                   "status": {"traffic": [{"revisionName": "serving-rev", "percent": 100}]}}
        dispatch = {"WORKFLOW_TARGET": "PAPER", "APPROVED_REF": "main", "GITHUB_SHA": SHA,
                    "GITHUB_WORKFLOW_SHA": SHA, "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
                    "GITHUB_REF_NAME": "main", "GITHUB_REF": "refs/heads/main",
                    "GITHUB_WORKFLOW_REF": "QuantStrategyLab/LongBridgePlatform/.github/workflows/sync-cloud-run-env.yml@refs/heads/main"}
        calls = []
        def run(command):
            calls.append(command)
            if command[:4] == ["gcloud", "run", "revisions", "describe"]:
                return json.dumps({"metadata": {"labels": {"commit-sha": "1" * 40}}, "spec": spec})
            self.assertEqual(command[:2], ["git", "show"])
            commit, name = command[2].split(":", 1)
            pin = "e" * 40 if commit == SHA else "f" * 40
            if name == "uv.lock":
                return '[[package]]\nname="us-equity-strategies"\nsource={git="https://example.invalid/ues?rev=' + pin + '#' + pin + '"}\n'
            if name == "pyproject.toml":
                return '[project]\ndependencies=["us-equity-strategies @ git+https://github.com/QuantStrategyLab/UsEquityStrategies.git@' + pin + '"]\n'
            self.assertEqual(name, "qsl.toml")
            return '[qsl.requires]\nus_equity_strategies="' + pin + '"\n'
        with patch.object(admission, "resolve_strategy_definition", return_value=SimpleNamespace(profile=profile)):
            with self.assertRaises(admission.AdmissionError) as caught:
                admission.prepare_image_only_staging(service="paper-service", project="synthetic-project",
                    region="synthetic-region", service_json=service, env=dispatch, image_commit=SHA, run=run)
        self.assertEqual(caught.exception.diagnostic_reason, "candidate_serving_ues_mismatch")
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(command[:2] == ["git", "show"] or command[:4] ==
                            ["gcloud", "run", "revisions", "describe"] for command in calls))


if __name__ == "__main__":
    unittest.main()
