#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
workflow_file="$repo_dir/.github/workflows/sync-cloud-run-env.yml"

grep -Fq "environment: longbridge-hk" "$workflow_file"
grep -Fq "environment: longbridge-sg" "$workflow_file"
grep -Fq 'GLOBAL_TELEGRAM_CHAT_ID: ${{ vars.GLOBAL_TELEGRAM_CHAT_ID }}' "$workflow_file"
grep -Fq 'TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}' "$workflow_file"
grep -Fq 'LONGPORT_SECRET_NAME: ${{ vars.LONGPORT_SECRET_NAME }}' "$workflow_file"
if grep -Fq -- "--remove-env-vars TELEGRAM_CHAT_ID" "$workflow_file"; then
  echo "workflow should not force-remove TELEGRAM_CHAT_ID; keep backward compatibility" >&2
  exit 1
fi
