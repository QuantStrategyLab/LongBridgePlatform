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
    assert "needs: sync" in cleanup_section
    assert "environment: longbridge-sg" in cleanup_section
    assert 'replacement_jobs=("${service_name}-probe-scheduler" "${service_name}-precheck-scheduler")' in cleanup_section
    assert "if: steps.replacements.outputs.ready == 'true'" in cleanup_section
