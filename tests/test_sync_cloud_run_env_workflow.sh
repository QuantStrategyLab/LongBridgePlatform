#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
workflow_file="$repo_dir/.github/workflows/sync-cloud-run-env.yml"

grep -Fq 'GCP_WORKLOAD_IDENTITY_PROVIDER: projects/252919773759/locations/global/workloadIdentityPools/github-actions/providers/github-main' "$workflow_file"
grep -Fq 'GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT: longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com' "$workflow_file"
grep -Fq 'permissions:' "$workflow_file"
grep -Fq 'id-token: write' "$workflow_file"
grep -Fq 'workload_identity_provider: ${{ env.GCP_WORKLOAD_IDENTITY_PROVIDER }}' "$workflow_file"
grep -Fq 'service_account: ${{ env.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT }}' "$workflow_file"
grep -Fq 'uses: actions/checkout@v4' "$workflow_file"
grep -Fq 'uses: actions/setup-python@v5' "$workflow_file"
grep -Fq 'python -m pip install -r requirements.txt' "$workflow_file"
grep -Fq 'id: strategy_requirements' "$workflow_file"
grep -Fq 'scripts/print_strategy_profile_status.py' "$workflow_file"
grep -Fq 'from us_equity_strategies import resolve_canonical_profile' "$workflow_file"
grep -Fq 'canonical_profile = resolve_canonical_profile(profile)' "$workflow_file"
grep -Fq 'requires_snapshot_artifacts=' "$workflow_file"
grep -Fq 'requires_snapshot_manifest_path=' "$workflow_file"
grep -Fq 'requires_strategy_config_path=' "$workflow_file"
grep -Fq 'Wait for Cloud Run deployment of current commit' "$workflow_file"
grep -Fq 'target_sha="${GITHUB_SHA}"' "$workflow_file"
grep -Fq "gcloud run services describe \"\${CLOUD_RUN_SERVICE}\" --region \"\${CLOUD_RUN_REGION}\" --format='value(spec.template.metadata.labels.commit-sha)'" "$workflow_file"
grep -Fq 'Timed out waiting for Cloud Run service ${CLOUD_RUN_SERVICE} to deploy commit ${target_sha}. Last seen commit: ${deployed_sha:-<none>}' "$workflow_file"
grep -Fq "environment: longbridge-hk" "$workflow_file"
grep -Fq "environment: longbridge-sg" "$workflow_file"
grep -Fq 'ENABLE_GITHUB_ENV_SYNC: ${{ vars.ENABLE_GITHUB_ENV_SYNC }}' "$workflow_file"
grep -Fq 'GLOBAL_TELEGRAM_CHAT_ID: ${{ vars.GLOBAL_TELEGRAM_CHAT_ID }}' "$workflow_file"
grep -Fq 'TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}' "$workflow_file"
grep -Fq 'TELEGRAM_TOKEN_SECRET_NAME: ${{ vars.TELEGRAM_TOKEN_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_APP_KEY_SECRET_NAME: ${{ vars.LONGPORT_APP_KEY_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_APP_SECRET_SECRET_NAME: ${{ vars.LONGPORT_APP_SECRET_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_SECRET_NAME: ${{ vars.LONGPORT_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_PATH: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_STRATEGY_CONFIG_PATH: ${{ vars.LONGBRIDGE_STRATEGY_CONFIG_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_DRY_RUN_ONLY: ${{ vars.LONGBRIDGE_DRY_RUN_ONLY }}' "$workflow_file"
grep -Fq "STRATEGY_PROFILE: \${{ vars.STRATEGY_PROFILE || 'soxl_soxx_trend_income' }}" "$workflow_file"
grep -Fq "ACCOUNT_REGION: \${{ vars.ACCOUNT_REGION || 'HK' }}" "$workflow_file"
grep -Fq "ACCOUNT_REGION: \${{ vars.ACCOUNT_REGION || 'SG' }}" "$workflow_file"
grep -Fq 'echo "enabled=false" >> "$GITHUB_OUTPUT"' "$workflow_file"
grep -Fq "Skipping HK Cloud Run env sync because ENABLE_GITHUB_ENV_SYNC is not set to true." "$workflow_file"
grep -Fq "Skipping SG Cloud Run env sync because ENABLE_GITHUB_ENV_SYNC is not set to true." "$workflow_file"
grep -Fq "HK Cloud Run env sync is enabled, but these values are missing:" "$workflow_file"
grep -Fq "SG Cloud Run env sync is enabled, but these values are missing:" "$workflow_file"
grep -Fq "set CLOUD_RUN_REGION on the longbridge-hk Environment" "$workflow_file"
grep -Fq "set CLOUD_RUN_REGION on the longbridge-sg Environment" "$workflow_file"
grep -Fq "Set LONGPORT_APP_KEY_SECRET_NAME and LONGPORT_APP_SECRET_SECRET_NAME on the longbridge-hk Environment" "$workflow_file"
grep -Fq "Set LONGPORT_APP_KEY_SECRET_NAME and LONGPORT_APP_SECRET_SECRET_NAME on the longbridge-sg Environment" "$workflow_file"
grep -Fq "if: steps.config.outputs.enabled == 'true'" "$workflow_file"
grep -Fq 'missing_vars+=("TELEGRAM_TOKEN_SECRET_NAME or TELEGRAM_TOKEN")' "$workflow_file"
grep -Fq 'missing_vars+=("LONGPORT_APP_KEY_SECRET_NAME")' "$workflow_file"
grep -Fq 'missing_vars+=("LONGPORT_APP_SECRET_SECRET_NAME")' "$workflow_file"
grep -Fq 'missing_vars+=("LONGBRIDGE_FEATURE_SNAPSHOT_PATH")' "$workflow_file"
grep -Fq 'missing_vars+=("LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH")' "$workflow_file"
grep -Fq 'missing_vars+=("LONGBRIDGE_STRATEGY_CONFIG_PATH")' "$workflow_file"
grep -Fq 'REQUIRES_SNAPSHOT_ARTIFACTS: ${{ steps.strategy_requirements.outputs.requires_snapshot_artifacts }}' "$workflow_file"
grep -Fq 'REQUIRES_SNAPSHOT_MANIFEST_PATH: ${{ steps.strategy_requirements.outputs.requires_snapshot_manifest_path }}' "$workflow_file"
grep -Fq 'REQUIRES_STRATEGY_CONFIG_PATH: ${{ steps.strategy_requirements.outputs.requires_strategy_config_path }}' "$workflow_file"
grep -Fq 'if [ "${REQUIRES_SNAPSHOT_ARTIFACTS:-}" = "true" ] && [ -z "${LONGBRIDGE_FEATURE_SNAPSHOT_PATH:-}" ]; then' "$workflow_file"
grep -Fq 'if [ "${REQUIRES_SNAPSHOT_MANIFEST_PATH:-}" = "true" ] && [ -z "${LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH:-}" ]; then' "$workflow_file"
grep -Fq 'if [ "${REQUIRES_STRATEGY_CONFIG_PATH:-}" = "true" ] && [ -z "${LONGBRIDGE_STRATEGY_CONFIG_PATH:-}" ]; then' "$workflow_file"
grep -Fq 'secret_pairs+=("TELEGRAM_TOKEN=${TELEGRAM_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("LONGPORT_APP_KEY=${LONGPORT_APP_KEY_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("LONGPORT_APP_SECRET=${LONGPORT_APP_SECRET_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'LONGPORT_SECRET_NAME=${LONGPORT_SECRET_NAME}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_PATH=${LONGBRIDGE_FEATURE_SNAPSHOT_PATH}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH=${LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH}' "$workflow_file"
grep -Fq 'LONGBRIDGE_STRATEGY_CONFIG_PATH=${LONGBRIDGE_STRATEGY_CONFIG_PATH}' "$workflow_file"
grep -Fq 'LONGBRIDGE_DRY_RUN_ONLY=${LONGBRIDGE_DRY_RUN_ONLY}' "$workflow_file"
grep -Fq 'STRATEGY_PROFILE=${STRATEGY_PROFILE}' "$workflow_file"
grep -Fq 'ACCOUNT_REGION=${ACCOUNT_REGION}' "$workflow_file"
grep -Fq '"SERVICE_NAME"' "$workflow_file"
grep -Fq 'gcloud_args+=(--remove-secrets "$(IFS=,; echo "${remove_secret_vars[*]}")")' "$workflow_file"
grep -Fq 'gcloud_args+=(--update-secrets "$(IFS=,; echo "${secret_pairs[*]}")")' "$workflow_file"

if grep -Fq 'SERVICE_NAME: ${{ vars.SERVICE_NAME }}' "$workflow_file"; then
  echo "unexpected SERVICE_NAME env wiring still present" >&2
  exit 1
fi

if grep -Fq 'SERVICE_NAME=${SERVICE_NAME}' "$workflow_file"; then
  echo "unexpected SERVICE_NAME sync still present" >&2
  exit 1
fi

if grep -Fq 'LONGPORT_APP_KEY: ${{ secrets.LONGPORT_APP_KEY }}' "$workflow_file"; then
  echo "unexpected GitHub secret fallback for LONGPORT_APP_KEY still present" >&2
  exit 1
fi

if grep -Fq 'LONGPORT_APP_SECRET: ${{ secrets.LONGPORT_APP_SECRET }}' "$workflow_file"; then
  echo "unexpected GitHub secret fallback for LONGPORT_APP_SECRET still present" >&2
  exit 1
fi
