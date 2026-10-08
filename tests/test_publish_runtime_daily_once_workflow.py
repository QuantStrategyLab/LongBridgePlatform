from pathlib import Path
import subprocess

import pytest

PATH = Path(__file__).resolve().parents[1] / ".github/workflows/publish-runtime-daily-once.yml"


def test_one_shot_workflow_has_only_original_paper_identity_and_publisher():
    text = PATH.read_text()
    assert "workflow_dispatch:" in text and "schedule:" not in text and "matrix:" not in text
    assert "environment: longbridge-paper" in text
    assert "RUNTIME_TARGET_ENABLED: ${{ vars.RUNTIME_TARGET_ENABLED }}" in text
    assert "RUNTIME_HEARTBEAT_ACCOUNT_SCOPE: ${{ vars.ACCOUNT_REGION }}" in text
    assert "RUNTIME_DAILY_PROJECTION_GCS_PREFIX: ${{ secrets.RUNTIME_DAILY_PROJECTION_GCS_PREFIX }}" in text
    assert "vars.RUNTIME_HEARTBEAT_GCS_URIS || vars.EXECUTION_REPORT_GCS_URI" in text
    assert "EXECUTION_EVIDENCE_SYNC_TOKEN: ${{ secrets.EXECUTION_EVIDENCE_SYNC_TOKEN }}" in text
    assert "python -m scripts.publish_daily_runtime_projection --one-shot-paper" in text
    for forbidden in ("execution_report_heartbeat.py", "record_daily_account_snapshot", "gcloud scheduler", "gcloud run deploy", "gcloud run services update", "TELEGRAM", "allow_live", "/run", "/dry-run", "RUNTIME_TARGET_ENABLED: true"):
        assert forbidden not in text


@pytest.mark.parametrize("field,value", [
    ("RUNTIME_TARGET_ENABLED", ""), ("RUNTIME_TARGET_ENABLED", "maybe"),
    ("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE", "SG"), ("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE", "HK"),
    ("CLOUD_RUN_SERVICE", ""), ("CLOUD_RUN_REGION", ""),
    ("RUNTIME_DAILY_PROJECTION_GCS_PREFIX", ""),
])
def test_daily_selection_rejects_before_cloud_identity(field, value):
    text = PATH.read_text()
    block = text.split("      - name: Validate PAPER selection\n", 1)[1].split("        run: |\n", 1)[1].split("      - name:", 1)[0]
    script = "\n".join(line[10:] for line in block.splitlines())
    env = {"RUNTIME_TARGET_ENABLED": "false", "RUNTIME_HEARTBEAT_ACCOUNT_SCOPE": "PAPER", "CLOUD_RUN_SERVICE": "synthetic-paper", "CLOUD_RUN_REGION": "synthetic-region", "RUNTIME_HEARTBEAT_GCS_URIS": "gs://synthetic-existing/reports", "RUNTIME_DAILY_PROJECTION_GCS_PREFIX": "gs://synthetic-existing/runtime_daily"}
    env[field] = value
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 2 and result.stderr == ""
    assert result.stdout.strip() == '{"status":"selection_unavailable","qrs_ack_valid":false}'


def test_daily_selection_defers_report_root_until_serving_revision_readback():
    text = PATH.read_text()
    block = text.split("      - name: Validate PAPER selection\n", 1)[1].split("        run: |\n", 1)[1].split("      - name:", 1)[0]
    assert "RUNTIME_HEARTBEAT_GCS_URIS" not in block
    assert "EXECUTION_REPORT_GCS_URI" not in block


@pytest.mark.parametrize("runtime_enabled", ["true", "false"])
def test_daily_selection_accepts_both_canonical_runtime_states(runtime_enabled):
    text = PATH.read_text()
    block = text.split("      - name: Validate PAPER selection\n", 1)[1].split("        run: |\n", 1)[1].split("      - name:", 1)[0]
    script = "\n".join(line[10:] for line in block.splitlines())
    env = {
        "RUNTIME_TARGET_ENABLED": runtime_enabled,
        "RUNTIME_HEARTBEAT_ACCOUNT_SCOPE": "PAPER",
        "CLOUD_RUN_SERVICE": "synthetic-paper",
        "CLOUD_RUN_REGION": "synthetic-region",
        "RUNTIME_DAILY_PROJECTION_GCS_PREFIX": "gs://synthetic-existing/runtime_daily",
    }
    result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
