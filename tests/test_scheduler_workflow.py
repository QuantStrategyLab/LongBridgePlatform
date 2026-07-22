from pathlib import Path


def test_replacement_probe_and_precheck_exist_before_shared_dispatcher_cleanup() -> None:
    workflow = Path(".github/workflows/sync-cloud-run-env.yml").read_text(encoding="utf-8")

    probe = workflow.index('probe_job_name="${CLOUD_RUN_SERVICE}-probe-scheduler"')
    precheck = workflow.index('precheck_job_name="${CLOUD_RUN_SERVICE}-precheck-scheduler"')
    cleanup_job = workflow.index("cleanup-shared-monitor:")
    cleanup = workflow.index('gcloud scheduler jobs delete "${monitor_job_name}"')

    assert probe < cleanup_job < cleanup
    assert precheck < cleanup_job < cleanup

    cleanup_section = workflow[cleanup_job:]
    sync_section = workflow[:cleanup_job]
    assert "needs: sync" in cleanup_section
    assert "environment: longbridge-sg" in cleanup_section
    assert 'replacement_jobs=("${service_name}-probe-scheduler" "${service_name}-precheck-scheduler")' in cleanup_section
    assert "if: steps.replacements.outputs.ready == 'true'" in cleanup_section
    assert "MONITOR_DISPATCH_TARGETS_JSON" not in sync_section
    assert "LONGBRIDGE_MONITOR_DISPATCH_TARGETS_JSON" not in sync_section


def test_replacement_verifier_falls_back_to_global_scheduler_location() -> None:
    workflow = Path(".github/workflows/sync-cloud-run-env.yml").read_text(encoding="utf-8")
    cleanup_section = workflow[workflow.index("cleanup-shared-monitor:") :]

    assert 'or os.environ.get("CLOUD_SCHEDULER_LOCATION")' in cleanup_section
    assert 'or os.environ.get("CLOUD_RUN_REGION")' in cleanup_section


def test_existing_scheduler_cron_rejects_ambiguous_day_fields() -> None:
    workflow = Path(".github/workflows/sync-cloud-run-env.yml").read_text(encoding="utf-8")

    assert 'if current_fields[2] != "*" and current_fields[4] != "*":' in workflow
    assert "cannot constrain both day-of-month and day-of-week" in workflow
