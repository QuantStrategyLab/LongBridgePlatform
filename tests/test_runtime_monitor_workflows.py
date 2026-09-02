from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_execution_report_heartbeat_has_market_neutral_daily_schedule() -> None:
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text()

    assert 'cron: "20 22 * * *"' in workflow
    assert 'cron: "20 22 * * 1-5"' not in workflow
    assert "RUNTIME_HEARTBEAT_MARKET_AWARE:" in workflow
    assert "RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES:" in workflow
    assert "RUNTIME_HEARTBEAT_SCHEDULER_LOCATION:" in workflow
    assert "CLOUD_SCHEDULER_MAIN_TIME:" in workflow
    assert "pandas-market-calendars" in (ROOT / "pyproject.toml").read_text()
    assert "pandas-market-calendars" in (ROOT / "uv.lock").read_text()


def test_runtime_monitor_workflows_retry_gcp_authentication() -> None:
    for name in ("execution-report-heartbeat.yml", "runtime-guard.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text()

        assert workflow.count("google-github-actions/auth@v3") == 2
        assert "id: gcp_auth_primary" in workflow
        assert "continue-on-error: true" in workflow
        assert "steps.gcp_auth_primary.outcome == 'failure'" in workflow


def test_runtime_monitor_workflows_use_frozen_runtime_environment() -> None:
    setup_uv = "uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"
    workflows = {
        "execution-report-heartbeat.yml": (
            "uv run --no-sync python scripts/execution_report_heartbeat.py",
        ),
        "runtime-guard.yml": (
            "uv run --no-sync python scripts/cloud_run_runtime_guard.py",
        ),
        "runtime-target-lifecycle.yml": (
            "uv run --no-sync python scripts/cloud_run_runtime_guard.py",
            "uv run --no-sync python scripts/execution_report_heartbeat.py",
        ),
    }

    for name, commands in workflows.items():
        workflow = (ROOT / ".github/workflows" / name).read_text()

        assert "uses: actions/setup-python@" not in workflow
        assert workflow.count("uses: astral-sh/setup-uv@") == 1
        assert setup_uv in workflow
        assert "pip install" not in workflow
        assert workflow.count("uv sync --frozen --no-dev") == 1
        assert workflow.index(setup_uv) < workflow.index("uv sync --frozen --no-dev")
        for command in commands:
            assert command in workflow
            assert workflow.index("uv sync --frozen --no-dev") < workflow.index(command)

    lifecycle = (ROOT / ".github/workflows/runtime-target-lifecycle.yml").read_text()
    assert "traceback|importerror|modulenotfounderror" in lifecycle.lower()


def test_cloud_run_deployment_requires_manual_dispatch() -> None:
    workflow = (ROOT / ".github/workflows/sync-cloud-run-env.yml").read_text()

    assert "workflow_run:" not in workflow
    assert "workflow_dispatch:" in workflow
    assert "if: github.event_name == 'workflow_dispatch'" in workflow


def test_heartbeat_script_does_not_import_project_runtime_dependencies() -> None:
    script = (ROOT / "scripts/execution_report_heartbeat.py").read_text()

    assert "from runtime_config_support import" not in script
