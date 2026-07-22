from pathlib import Path


def test_replacement_probe_and_precheck_exist_before_shared_dispatcher_cleanup() -> None:
    workflow = Path(".github/workflows/sync-cloud-run-env.yml").read_text(encoding="utf-8")

    probe = workflow.index('probe_job_name="${CLOUD_RUN_SERVICE}-probe-scheduler"')
    precheck = workflow.index('precheck_job_name="${CLOUD_RUN_SERVICE}-precheck-scheduler"')
    cleanup = workflow.index('gcloud scheduler jobs delete "${monitor_job_name}"')

    assert probe < cleanup
    assert precheck < cleanup
