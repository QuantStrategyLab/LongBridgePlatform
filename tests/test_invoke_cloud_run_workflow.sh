#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
workflow_file="$repo_dir/.github/workflows/invoke-cloud-run.yml"

grep -Fq "name: Invoke Cloud Run" "$workflow_file"
grep -Fq "workflow_dispatch:" "$workflow_file"
grep -Fq "environment: \${{ inputs.environment }}" "$workflow_file"
grep -Fq "id-token: write" "$workflow_file"
grep -Fq "google-github-actions/auth@v3" "$workflow_file"
grep -Fq "google-github-actions/setup-gcloud@v3" "$workflow_file"
grep -Fq "CLOUD_RUN_REGION: \${{ vars.CLOUD_RUN_REGION }}" "$workflow_file"
grep -Fq "CLOUD_RUN_SERVICE: \${{ vars.CLOUD_RUN_SERVICE }}" "$workflow_file"
grep -Fq "longbridge-hk|longbridge-sg" "$workflow_file"
grep -Fq "gcloud run services describe \"\${CLOUD_RUN_SERVICE}\"" "$workflow_file"
grep -Fq "token_format: id_token" "$workflow_file"
grep -Fq "id_token_audience: \${{ steps.service.outputs.url }}" "$workflow_file"
grep -Fq "id_token_include_email: true" "$workflow_file"
grep -Fq "curl --fail-with-body --show-error --silent" "$workflow_file"
grep -Fq -- "--request POST" "$workflow_file"
grep -Fq "steps.invoke-auth.outputs.id_token" "$workflow_file"
