from pathlib import Path


WORKFLOW_PATH = Path(".github/workflows/build-hk-low-vol-snapshot-artifacts.yml")


def test_hk_low_vol_snapshot_artifact_workflow_uses_longbridge_wif_and_snapshot_repo():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "Build HK Low-Vol Snapshot Artifacts" in workflow
    assert "environment: longbridge-hk" in workflow
    assert "GCP_WORKLOAD_IDENTITY_PROVIDER: projects/252919773759/locations/global/workloadIdentityPools/github-actions/providers/github-main" in workflow
    assert "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT: longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com" in workflow
    assert "SNAPSHOT_REPOSITORY: QuantStrategyLab/HkEquitySnapshotPipelines" in workflow
    assert "python -m pip install -e '.[longbridge]'" in workflow
    assert 'gcloud secrets versions access latest --project="${LONGBRIDGE_SECRET_PROJECT_ID}" --secret="${LONGBRIDGE_APP_KEY_SECRET_NAME}"' in workflow
    assert "scripts/build_low_vol_dividend_longbridge_factor_snapshot.py" in workflow
    assert "hkeq-build-low-vol-dividend-quality-snapshot" in workflow
    assert "hkeq-validate-snapshot-artifact-pack" in workflow
    assert "scripts/publish_hk_snapshot_artifacts.py" in workflow


def test_hk_low_vol_snapshot_artifact_workflow_blocks_research_defaults_publish():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "allow_research_defaults=true is research smoke only and cannot be published to GCS." in workflow
    assert 'if [ "${ALLOW_RESEARCH_DEFAULTS}" = "true" ] && [ "${EXECUTE_PUBLISH}" = "true" ]; then' in workflow
    assert "Evidence boundary: validated LongBridge-generated CSVs can be runtime artifact inputs when allow_research_defaults=false" in workflow


def test_hk_low_vol_snapshot_artifact_workflow_keeps_generation_diagnostics():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "LongBridge generation summary:" in workflow
    assert "failed_symbols_preview" in workflow
    assert "LongBridge factor snapshot generation failed; see generation_summary.json" in workflow
    assert "if: always() && steps.build.outputs.generated_input_name != ''" in workflow
    assert "if-no-files-found: ignore" in workflow
