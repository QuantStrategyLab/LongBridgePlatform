#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
workflow_file="$repo_dir/.github/workflows/longbridge-api-probe.yml"

grep -Fq "name: LongBridge API Probe" "$workflow_file"
grep -Fq "workflow_dispatch:" "$workflow_file"
grep -Fq "environment: longbridge-hk" "$workflow_file"
grep -Fq "id-token: write" "$workflow_file"
grep -Fq "repository: QuantStrategyLab/QuantPlatformKit" "$workflow_file"
grep -Fq "ref: \${{ inputs.qpk_ref }}" "$workflow_file"
grep -Fq "google-github-actions/auth@v3" "$workflow_file"
grep -Fq "google-github-actions/setup-gcloud@v3" "$workflow_file"
grep -Fq "LONGPORT_APP_KEY_SECRET_NAME: \${{ vars.LONGPORT_APP_KEY_SECRET_NAME }}" "$workflow_file"
grep -Fq "LONGPORT_APP_SECRET_SECRET_NAME: \${{ vars.LONGPORT_APP_SECRET_SECRET_NAME }}" "$workflow_file"
grep -Fq "LONGPORT_SECRET_NAME: \${{ vars.LONGPORT_SECRET_NAME }}" "$workflow_file"
grep -Fq 'gcloud secrets versions access latest --secret="${LONGPORT_APP_KEY_SECRET_NAME}"' "$workflow_file"
grep -Fq 'gcloud secrets versions access latest --secret="${LONGPORT_APP_SECRET_SECRET_NAME}"' "$workflow_file"
grep -Fq 'gcloud secrets versions access latest --secret="${LONGPORT_SECRET_NAME}"' "$workflow_file"
grep -Fq "python -m pip install -e quant-platform-kit pytest longport" "$workflow_file"
grep -Fq 'LONGBRIDGE_API_PROBE: "1"' "$workflow_file"
grep -Fq "test_longbridge_fractional_order_api_probe.py" "$workflow_file"
