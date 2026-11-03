"""Offline checks for PAPER WIF masking before runner disclosure."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest

WORKFLOW = Path(".github/workflows/sync-cloud-run-env.yml")
PROVIDER = "projects/123456789012/locations/global/workloadIdentityPools/fake-pool/providers/fake-provider"
ACCOUNT = "fake-agent@fake-project.iam.gserviceaccount.com"
PROVIDER_MASKS = ["123456789012", "fake-pool", "fake-provider", PROVIDER]


def masked(text, masks):
    for value in sorted(masks, key=len, reverse=True):
        text = text.replace(value, "***")
    return text


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
    assert "GCP_WORKLOAD_IDENTITY_PROVIDER: ${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER }}" in first
    assert f"GCP_WORKLOAD_IDENTITY_PROVIDER: ${{{{ {inverse} && vars.GCP_WORKLOAD_IDENTITY_PROVIDER || '' }}}}" in config
    for key in ("GCP_PROJECT_ID",
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
             "GCP_WORKLOAD_IDENTITY_PROVIDER": PROVIDER,
             "GITHUB_ENV": str(exported), "AUTH_MARKER": str(marker)},
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert not result.stderr
    if identity.endswith(".com"):
        assert result.returncode == 0
        assert result.stdout.splitlines() == [f"::add-mask::{value}" for value in ["fake-agent", "fake-project", *PROVIDER_MASKS]]
        assert exported.read_text() == (f"GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT={identity}\n"
                                        f"GCP_WORKLOAD_IDENTITY_PROVIDER={PROVIDER}\n")
        assert marker.exists()
    else:
        assert result.returncode != 0
        assert not exported.exists()
        assert not marker.exists()
        assert result.stdout == "::error::Required Secret GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT is missing or malformed\n"
        assert "fake-agent" not in result.stdout
        assert "fake-project" not in result.stdout


@pytest.mark.parametrize("job", ["image-only-no-traffic", "sync"])
def test_next_step_environment_and_auth_input_mask_provider_components(tmp_path, job):
    deployment = job_section(job)
    first = deployment.split("    steps:\n", 1)[1].split("\n      - ", 1)[0]
    script = textwrap.dedent(first.split("        run: |\n", 1)[1])
    exported = tmp_path / "env"
    # Automatic masks exist only for values actually bound through secrets.*.
    secret_values = []
    first_environment = {}
    for key in ("GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT", "GCP_WORKLOAD_IDENTITY_PROVIDER"):
        expression = first.split(f"          {key}: ", 1)[1].splitlines()[0]
        value = (ACCOUNT if key.endswith("SERVICE_ACCOUNT") else PROVIDER) if "secrets." in expression else ""
        first_environment[key] = value
        if "secrets." in expression:
            secret_values.append(value)
    # The runner logs env before executing the mask script.
    first_log = masked(str(first_environment), secret_values)
    assert ACCOUNT not in first_log
    assert PROVIDER not in first_log
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, **first_environment, "GITHUB_ENV": str(exported)},
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0
    assert not result.stderr
    emitted_masks = [line.removeprefix("::add-mask::") for line in result.stdout.splitlines()
                     if line.startswith("::add-mask::")]
    # Step-local blank overrides expire: model the inherited provider at the
    # NEXT step, then overlay GITHUB_ENV exactly as the runner does.
    next_environment = {"GCP_WORKLOAD_IDENTITY_PROVIDER": PROVIDER}
    next_environment.update(line.split("=", 1) for line in exported.read_text().splitlines())
    assert "workload_identity_provider: ${{ env.GCP_WORKLOAD_IDENTITY_PROVIDER }}" in deployment
    provider = next_environment["GCP_WORKLOAD_IDENTITY_PROVIDER"]
    next_log = f"env: {next_environment}\nwith: workload_identity_provider: {provider}\n"
    # Separate component logging also needs masks; a full Secret mask alone
    # cannot hide the project number, pool and provider in diagnostics.
    next_log += "components: " + " ".join(PROVIDER_MASKS[:3])
    visible = masked(next_log, [*secret_values, *emitted_masks])
    for value in PROVIDER_MASKS:
        assert value not in visible
    assert set(PROVIDER_MASKS).issubset(emitted_masks)


@pytest.mark.parametrize("job", ["image-only-no-traffic", "sync"])
@pytest.mark.parametrize("provider", [
    pytest.param("", id="missing"),
    pytest.param("malformed", id="malformed"),
    pytest.param(PROVIDER + "\n", id="newline"),
    pytest.param(PROVIDER + " ", id="whitespace"),
    pytest.param(PROVIDER.replace("123456789012", "not-a-number"), id="project-number"),
    pytest.param(PROVIDER.replace("fake-pool", ""), id="empty-pool"),
    pytest.param(PROVIDER.replace("fake-provider", "::error::fake"), id="workflow-command"),
    pytest.param(PROVIDER.replace("fake-provider", "$(false)"), id="shell-command"),
    pytest.param(PROVIDER + "\rvalue", id="carriage-return"),
])
def test_invalid_provider_blocks_export_and_next_step(tmp_path, job, provider):
    first = job_section(job).split("    steps:\n", 1)[1].split("\n      - ", 1)[0]
    script = textwrap.dedent(first.split("        run: |\n", 1)[1])
    exported = tmp_path / "env"
    marker = tmp_path / "next-step"
    result = subprocess.run(
        ["bash", "-c", script + '\nprintf reached > "$NEXT_MARKER"'],
        env={**os.environ, "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT": ACCOUNT,
             "GCP_WORKLOAD_IDENTITY_PROVIDER": provider, "GITHUB_ENV": str(exported),
             "NEXT_MARKER": str(marker)},
        capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode != 0
    assert not result.stderr
    assert result.stdout == "::error::Required Secret GCP_WORKLOAD_IDENTITY_PROVIDER is missing or malformed\n"
    assert not exported.exists()
    assert not marker.exists()
