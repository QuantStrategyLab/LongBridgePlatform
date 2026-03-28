#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
workflow_file="$repo_dir/.github/workflows/sync-cloud-run-env.yml"

grep -Fq "environment: longbridge-hk" "$workflow_file"
grep -Fq "environment: longbridge-sg" "$workflow_file"
grep -Fq 'ENABLE_GITHUB_ENV_SYNC: ${{ vars.ENABLE_GITHUB_ENV_SYNC }}' "$workflow_file"
grep -Fq 'GLOBAL_TELEGRAM_CHAT_ID: ${{ vars.GLOBAL_TELEGRAM_CHAT_ID }}' "$workflow_file"
grep -Fq 'GCP_SA_KEY: ${{ secrets.GCP_SA_KEY }}' "$workflow_file"
grep -Fq 'TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}' "$workflow_file"
grep -Fq 'LONGPORT_SECRET_NAME: ${{ vars.LONGPORT_SECRET_NAME }}' "$workflow_file"
grep -Fq 'credentials_json: ${{ env.GCP_SA_KEY }}' "$workflow_file"
grep -Fq "echo \"enabled=false\" >> \"\$GITHUB_OUTPUT\"" "$workflow_file"
grep -Fq "Skipping HK Cloud Run env sync because ENABLE_GITHUB_ENV_SYNC is not set to true." "$workflow_file"
grep -Fq "Skipping SG Cloud Run env sync because ENABLE_GITHUB_ENV_SYNC is not set to true." "$workflow_file"
grep -Fq "HK Cloud Run env sync is enabled, but these values are missing:" "$workflow_file"
grep -Fq "SG Cloud Run env sync is enabled, but these values are missing:" "$workflow_file"
grep -Fq "if: steps.config.outputs.enabled == 'true'" "$workflow_file"
if grep -Fq -- "--remove-env-vars TELEGRAM_CHAT_ID" "$workflow_file"; then
  echo "workflow should not force-remove TELEGRAM_CHAT_ID; keep backward compatibility" >&2
  exit 1
fi
