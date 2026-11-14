import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/invoke-cloud-run.yml"


def _script(step: str) -> str:
    section = WORKFLOW.read_text().split(f"      - name: {step}\n", 1)[1]
    block = section.split("        run: |\n", 1)[1].split("      - name:", 1)[0]
    return textwrap.dedent(block)


def _validate(tmp_path: Path, path: str, **overrides: str) -> tuple[int, str]:
    output = tmp_path / "workflow-output"
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_OUTPUT": str(output),
        "REQUEST_ENVIRONMENT": "longbridge-paper",
        "REQUEST_PATH": path,
        "ALLOW_LIVE_EXECUTION": "false",
        "ALLOW_PAUSED_DRY_RUN": "false",
        "CLOUD_RUN_REGION": "synthetic-region",
        "CLOUD_RUN_SERVICE": "synthetic-service",
        **overrides,
    }
    result = subprocess.run(
        ["bash", "-c", _script("Validate inputs")],
        check=False, env=env, cwd=tmp_path, capture_output=True, text=True,
    )
    return result.returncode, output.read_text() if output.exists() else ""


@pytest.mark.parametrize("route", ["/dry-run", "/probe", "/backfill", "/reconcile", "/paper-command-consumer", "/monitor-dispatch"])
def test_supported_post_routes(tmp_path, route):
    assert _validate(tmp_path, route) == (0, f"path={route}\n")


@pytest.mark.parametrize("route", ["", "dry-run"])
def test_preserves_default_and_no_leading_slash(tmp_path, route):
    assert _validate(tmp_path, route, ALLOW_PAUSED_DRY_RUN="true") == (0, "path=/dry-run\n")


def test_run_requires_explicit_confirmation(tmp_path):
    assert _validate(tmp_path, "run") == (1, "")
    assert _validate(tmp_path, "run", ALLOW_PAUSED_DRY_RUN="true", ALLOW_LIVE_EXECUTION="true") == (1, "")
    assert _validate(tmp_path, "run", ALLOW_LIVE_EXECUTION="true") == (0, "path=/run\n")


@pytest.mark.parametrize("route", ["/dry-run/../run", "/%72un", "//run", "/run?x=1", "/probe\npath=/run", '$(touch executed)', '"; touch executed; #'])
def test_rejects_nonliteral_routes_without_executing_text(tmp_path, route):
    assert _validate(tmp_path, route) == (1, "")
    assert not (tmp_path / "executed").exists()


def test_dispatch_data_is_never_interpolated_into_shell_source():
    scripts = re.findall(r"        run: \|\n((?:\n|          [^\n]*\n)+)", WORKFLOW.read_text())
    assert len(scripts) == 4
    assert all("${{" not in script for script in scripts)
