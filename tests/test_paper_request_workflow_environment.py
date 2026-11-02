"""Exercise workflow environment wiring without conftest's global identities."""

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/inspect-paper-dry-run-request.yml"
CHILD = """
import json
import runpy
import subprocess
import sys

reader = runpy.run_path("scripts/paper_request_readback.py")
calls = []
def run(args, **kwargs):
    calls.append(args)
    return subprocess.CompletedProcess(args, 0, "[]", "")

try:
    result = reader["inspect"](
        "2026-11-02T01:00:00Z", "2026-11-02T01:01:00Z", run=run
    )
except ValueError as exc:
    assert str(exc) == "config_invalid"
    assert not calls
    sys.exit(2)
assert result["status"] == "no_match"
assert len(calls) == 1
assert "--project=synthetic-reader-project" in calls[0]
print(json.dumps({"status": result["status"]}))
"""


def workflow_environment(project):
    # Read only the reader step, so setup-gcloud inputs cannot satisfy this test.
    step = WORKFLOW.read_text().split("      - name: Inspect bounded request logs\n", 1)[1]
    step = step.split("      - ", 1)[0]
    assert "run: python scripts/paper_request_readback.py" in step
    expressions = {
        "${{ vars.GCP_PROJECT_ID }}": project,
        "${{ inputs.start_utc }}": "2026-11-02T01:00:00Z",
        "${{ inputs.end_utc }}": "2026-11-02T01:01:00Z",
    }
    env = {}
    for key, value in re.findall(r"^          ([A-Z_]+): (.+)$", step, re.M):
        resolved = expressions[value]
        if resolved is not None:
            env[key] = resolved
    return env


def test_workflow_passes_protected_project_to_reader():
    result = subprocess.run(
        [sys.executable, "-c", CHILD], cwd=ROOT,
        env=workflow_environment("synthetic-reader-project"),
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"status": "no_match"}


@pytest.mark.parametrize("project", [None, "", "INVALID"])
def test_workflow_reader_rejects_missing_or_invalid_project(project):
    result = subprocess.run(
        [sys.executable, "-c", CHILD], cwd=ROOT,
        env=workflow_environment(project),
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 2, result.stderr
    assert result.stdout == ""
