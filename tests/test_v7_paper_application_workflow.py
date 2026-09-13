from pathlib import Path
import json
import os
import subprocess
import sys


WORKFLOW = Path(".github/workflows/apply-paper-candidate.yml").read_text(encoding="utf-8")


def test_workflow_has_only_bounded_application_dispatch_and_main_source_gate() -> None:
    assert "workflow_dispatch:" in WORKFLOW
    assert "application_id:" in WORKFLOW
    assert "source_commit" in WORKFLOW
    assert "git merge-base --is-ancestor" in WORKFLOW
    assert "git checkout --detach \"$source_commit\"" in WORKFLOW
    assert "fetch-depth: 0" in WORKFLOW
    assert "--build-arg \"V7_PAPER_SOURCE_COMMIT=${SOURCE_COMMIT}\"" in WORKFLOW


def test_workflow_preserves_paper_pause_and_real_loaded_readback() -> None:
    assert "--no-traffic" in WORKFLOW
    assert "gcloud run services update-traffic" in WORKFLOW
    assert "--to-revisions \"$revision=100\"" in WORKFLOW
    assert "scheduler jobs pause" in WORKFLOW
    assert "v7_paper_application_loaded" in WORKFLOW
    assert "scripts/parse_v7_loaded_log.py" in WORKFLOW
    assert "any(item.get(\"tag\") for item in traffic)" in WORKFLOW


def test_workflow_uses_nested_claims_and_marks_post_claim_failures_uncertain() -> None:
    assert "scripts.v7_paper_application_http claim" in WORKFLOW
    assert "scripts.v7_paper_application_http result" in WORKFLOW
    assert "status uncertain" in WORKFLOW
    assert "failure() && steps.claim.outcome == 'success'" in WORKFLOW
    assert "--token" not in WORKFLOW
    assert "gcloud builds submit" not in WORKFLOW


def test_fake_gcloud_logging_chain_accepts_json_text_and_missing_receipts(tmp_path: Path) -> None:
    application_id = "61743a15-6dcc-4ebf-8ecb-454660014af7"
    revision = "paper-v7-00001"
    readback = {
        "application_id": application_id,
        "revision_name": revision,
        "runtime_target_enabled": False,
    }
    fake_gcloud = tmp_path / "gcloud"
    fake_gcloud.write_text("#!/bin/sh\ncat \"$FAKE_LOG\"\n", encoding="utf-8")
    fake_gcloud.chmod(0o700)
    for entry, expected in (
        ({"jsonPayload": {"event": "v7_paper_application_loaded", "status": "applied_paused", "readback": readback}}, True),
        ({"textPayload": json.dumps({"event": "v7_paper_application_loaded", "status": "applied_paused", "readback": readback})}, True),
        ({"jsonPayload": {"event": "other", "status": "applied_paused", "readback": readback}}, False),
    ):
        source_logs = tmp_path / "source-logs.json"
        source_logs.write_text(json.dumps([entry]), encoding="utf-8")
        logs = tmp_path / "logs.json"
        output = tmp_path / "readback.json"
        command = (
            "gcloud logging read 'fake-filter' --format=json > logs.json "
            f"&& {sys.executable} scripts/parse_v7_loaded_log.py "
            f"--logs-file logs.json --application-id {application_id} "
            f"--revision {revision} --readback-out {output}"
        )
        result = subprocess.run(
            ["sh", "-c", command],
            cwd=Path(__file__).parents[1],
            env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "FAKE_LOG": str(source_logs)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) is expected
        if expected:
            assert json.loads(output.read_text(encoding="utf-8")) == readback
