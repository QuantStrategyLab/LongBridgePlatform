"""Offline checks for PAPER WIF masking before runner disclosure."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest

WORKFLOW = Path(".github/workflows/sync-cloud-run-env.yml")


def job_section(name):
    return WORKFLOW.read_text().split(f"\n  {name}:\n", 1)[1].split("\n  resolve-matrix:\n" if name == "image-only-no-traffic" else "\n  cleanup-shared-monitor:\n", 1)[0]


@pytest.mark.parametrize("job,condition", [
    ("image-only-no-traffic", "inputs.target == 'PAPER'"),
    ("sync", "matrix.target.environment == 'longbridge-paper'"),
])
def test_paper_binding_never_expands_variable_identity_before_masks(job, condition):
    workflow = WORKFLOW.read_text()
    assert "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT:" not in workflow.split("\njobs:", 1)[0]
    section = job_section(job)
    config, steps = section.split("    steps:\n", 1)
    inverse = condition.replace(" == ", " != ")
    assert f"GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT: ${{{{ {inverse} && vars.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT || '' }}}}" in config
    first = steps.split("\n      - ", 1)[0]
    assert "name: Mask WIF identity components" in first
    assert f"if: {condition}" in first
    assert "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT: ${{ secrets.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT }}" in first
    assert "vars.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT" not in first
    # The runner prints inherited env before executing run. These workflow-level
    # identities can contain the same project/account components as the Secret.
    for key in ("GCP_PROJECT_ID", "GCP_WORKLOAD_IDENTITY_PROVIDER",
                "GCP_RUNTIME_SERVICE_ACCOUNT", "GCP_SCHEDULER_SERVICE_ACCOUNT"):
        assert f'          {key}: ""' in first
    assert first.index("::add-mask::") < first.index('>> "$GITHUB_ENV"')
    assert "service_account: ${{ env.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT }}" in section


@pytest.mark.parametrize("job", ["image-only-no-traffic", "sync"])
@pytest.mark.parametrize("identity", [
    pytest.param("", id="missing"),
    pytest.param("malformed", id="malformed"),
    pytest.param("fake-agent@fake-project.iam.gserviceaccount.com\n", id="newline"),
    pytest.param("fake-agent@fake-project.iam.gserviceaccount.com", id="valid"),
])
def test_paper_mask_step_blocks_invalid_secret_and_masks_before_export(tmp_path, job, identity):
    first = job_section(job).split("    steps:\n", 1)[1].split("\n      - ", 1)[0]
    assert "name: Mask WIF identity components" in first
    script = textwrap.dedent(first.split("        run: |\n", 1)[1])
    exported = tmp_path / "env"
    marker = tmp_path / "auth-reached"
    result = subprocess.run(
        ["bash", "-c", script + '\nprintf reached > "$AUTH_MARKER"'],
        env={**os.environ, "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT": identity,
             "GITHUB_ENV": str(exported), "AUTH_MARKER": str(marker)},
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert not result.stderr
    if identity.endswith(".com"):
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["::add-mask::fake-agent", "::add-mask::fake-project"]
        assert exported.read_text() == f"GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT={identity}\n"
        assert marker.exists()
    else:
        assert result.returncode != 0
        assert not exported.exists()
        assert not marker.exists()
        assert result.stdout == "::error::Required Secret GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT is missing or malformed\n"
        assert "fake-agent" not in result.stdout
        assert "fake-project" not in result.stdout
