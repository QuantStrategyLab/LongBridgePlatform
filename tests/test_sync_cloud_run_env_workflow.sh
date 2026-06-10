#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
workflow_file="$repo_dir/.github/workflows/sync-cloud-run-env.yml"

grep -Fq 'GCP_WORKLOAD_IDENTITY_PROVIDER: projects/252919773759/locations/global/workloadIdentityPools/github-actions/providers/github-main' "$workflow_file"
grep -Fq 'GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT: longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com' "$workflow_file"
grep -Fq 'name: Deploy / Sync ${{ matrix.target.label }} Cloud Run' "$workflow_file"
grep -Fq 'fail-fast: false' "$workflow_file"
grep -Fq 'environment: longbridge-paper' "$workflow_file"
grep -Fq 'environment: longbridge-hk' "$workflow_file"
grep -Fq 'environment: longbridge-sg' "$workflow_file"
grep -Fq 'environment: ${{ matrix.target.environment }}' "$workflow_file"
grep -Fq 'target:' "$workflow_file"
grep -Fq -- '- hk-verify' "$workflow_file"
grep -Fq 'INPUT_DEPLOY_IMAGE: ${{ inputs.deploy_image }}' "$workflow_file"
grep -Fq 'Apply HK verify-only dispatch defaults' "$workflow_file"
grep -Fq 'hk-verify targets only the HK deployment' "$workflow_file"
grep -Fq '"strategy_profile": "hk_global_etf_tactical_rotation"' "$workflow_file"
grep -Fq 'echo "LONGBRIDGE_DRY_RUN_ONLY=true"' "$workflow_file"
grep -Fq 'echo "LONGBRIDGE_MARKET=HK"' "$workflow_file"
grep -Fq 'echo "LONGBRIDGE_SYMBOL_SUFFIX=.HK"' "$workflow_file"
grep -Fq 'CLOUD_RUN_ENV_SYNC_WAIT_FOR_COMMIT: ${{ vars.CLOUD_RUN_ENV_SYNC_WAIT_FOR_COMMIT }}' "$workflow_file"
grep -Fq 'CLOUD_SCHEDULER_LOCATION: ${{ vars.CLOUD_SCHEDULER_LOCATION }}' "$workflow_file"
grep -Fq 'Skipping Cloud Run commit wait because CLOUD_RUN_ENV_SYNC_WAIT_FOR_COMMIT is disabled.' "$workflow_file"
grep -Fq 'permissions:' "$workflow_file"
grep -Fq 'id-token: write' "$workflow_file"
grep -Fq 'workload_identity_provider: ${{ env.GCP_WORKLOAD_IDENTITY_PROVIDER }}' "$workflow_file"
grep -Fq 'service_account: ${{ env.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT }}' "$workflow_file"
grep -Fq 'uses: actions/checkout@v6' "$workflow_file"
grep -Fq 'uses: actions/setup-python@v6' "$workflow_file"
grep -Fq 'python -m pip install -r requirements.txt' "$workflow_file"
grep -Fq 'id: strategy_requirements' "$workflow_file"
grep -Fq 'scripts/print_strategy_profile_status.py' "$workflow_file"
grep -Fq 'from strategy_registry import LONGBRIDGE_PLATFORM, resolve_strategy_definition' "$workflow_file"
grep -Fq 'canonical_profile = resolve_strategy_definition(' "$workflow_file"
grep -Fq 'requires_snapshot_artifacts=' "$workflow_file"
grep -Fq 'requires_snapshot_manifest_path=' "$workflow_file"
grep -Fq 'requires_strategy_config_path=' "$workflow_file"
grep -Fq 'config_source_policy=' "$workflow_file"
grep -Fq 'canonical_profile=' "$workflow_file"
grep -Fq 'runtime_target_json=' "$workflow_file"
grep -Fq 'Wait for Cloud Run deployment of current commit' "$workflow_file"
grep -Fq 'target_sha="${GITHUB_SHA}"' "$workflow_file"
grep -Fq "gcloud run services describe \"\${CLOUD_RUN_SERVICE}\" --region \"\${CLOUD_RUN_REGION}\" --format='value(spec.template.metadata.labels.commit-sha)'" "$workflow_file"
grep -Fq 'Timed out waiting for Cloud Run service ${CLOUD_RUN_SERVICE} to deploy commit ${target_sha}. Last seen commit: ${deployed_sha:-<none>}' "$workflow_file"
grep -Fq 'ENABLE_GITHUB_ENV_SYNC: ${{ vars.ENABLE_GITHUB_ENV_SYNC }}' "$workflow_file"
grep -Fq 'ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION: ${{ vars.ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION }}' "$workflow_file"
grep -Fq 'GLOBAL_TELEGRAM_CHAT_ID: ${{ vars.GLOBAL_TELEGRAM_CHAT_ID }}' "$workflow_file"
grep -Fq 'TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD: ${{ secrets.STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN }}' "$workflow_file"
grep -Fq 'TELEGRAM_TOKEN_SECRET_NAME: ${{ vars.TELEGRAM_TOKEN_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_APP_KEY_SECRET_NAME: ${{ vars.LONGPORT_APP_KEY_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_APP_SECRET_SECRET_NAME: ${{ vars.LONGPORT_APP_SECRET_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_SECRET_NAME: ${{ vars.LONGPORT_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_PATH: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_STRATEGY_CONFIG_PATH: ${{ vars.LONGBRIDGE_STRATEGY_CONFIG_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON: ${{ vars.LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MIN_RESERVED_CASH_USD: ${{ vars.LONGBRIDGE_MIN_RESERVED_CASH_USD }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_RESERVED_CASH_RATIO: ${{ vars.LONGBRIDGE_RESERVED_CASH_RATIO }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD: ${{ vars.LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_CHANNELS: ${{ vars.STRATEGY_PLUGIN_ALERT_CHANNELS }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS: ${{ vars.STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL: ${{ vars.STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME: ${{ vars.STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST: ${{ vars.STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT: ${{ vars.STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY: ${{ vars.STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS: ${{ vars.STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_PROVIDER: ${{ vars.STRATEGY_PLUGIN_ALERT_SMS_PROVIDER }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID: ${{ vars.STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN_SECRET_NAME: ${{ vars.STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN_SECRET_NAME }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_SENDER: ${{ vars.STRATEGY_PLUGIN_ALERT_SMS_SENDER }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID: ${{ vars.STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL: ${{ vars.STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS: ${{ vars.STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN_SECRET_NAME: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN_SECRET_NAME }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN_SECRET_NAME: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN_SECRET_NAME }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_DEVICE: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_DEVICE }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_TAGS: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_TAGS }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS: ${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS: ${{ vars.STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME: ${{ vars.STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME }}' "$workflow_file"
grep -Fq 'INCOME_THRESHOLD_USD: ${{ vars.INCOME_THRESHOLD_USD }}' "$workflow_file"
grep -Fq 'QQQI_INCOME_RATIO: ${{ vars.QQQI_INCOME_RATIO }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_DRY_RUN_ONLY: ${{ vars.LONGBRIDGE_DRY_RUN_ONLY }}' "$workflow_file"
grep -Fq 'RUNTIME_TARGET_JSON: ${{ vars.RUNTIME_TARGET_JSON }}' "$workflow_file"
grep -Fq 'ACCOUNT_REGION: ${{ vars.ACCOUNT_REGION || matrix.target.default_account_region }}' "$workflow_file"
grep -Fq 'echo "enabled=false" >> "$GITHUB_OUTPUT"' "$workflow_file"
grep -Fq 'Skipping ${DEPLOYMENT_LABEL} Cloud Run automation because ENABLE_GITHUB_CLOUD_RUN_DEPLOY and ENABLE_GITHUB_ENV_SYNC are not true.' "$workflow_file"
grep -Fq 'Skipping ${DEPLOYMENT_LABEL} Cloud Run automation on push because ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION is not true.' "$workflow_file"
grep -Fq '${DEPLOYMENT_LABEL} Cloud Run env sync is enabled, but these values are missing:' "$workflow_file"
grep -Fq 'Set CLOUD_RUN_REGION on the ${GITHUB_ENVIRONMENT_NAME} Environment so each service can target its own region.' "$workflow_file"
grep -Fq 'Set LONGPORT_APP_KEY_SECRET_NAME and LONGPORT_APP_SECRET_SECRET_NAME on the ${GITHUB_ENVIRONMENT_NAME} Environment so credentials do not fall back to shared defaults.' "$workflow_file"
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
grep -Fq 'CONFIG_SOURCE_POLICY: ${{ steps.strategy_requirements.outputs.config_source_policy }}' "$workflow_file"
grep -Fq 'STRATEGY_PROFILE: ${{ steps.strategy_requirements.outputs.canonical_profile }}' "$workflow_file"
grep -Fq 'RUNTIME_TARGET_JSON: ${{ steps.strategy_requirements.outputs.runtime_target_json }}' "$workflow_file"
grep -Fq 'if [ "${REQUIRES_SNAPSHOT_ARTIFACTS:-}" = "true" ] && [ -z "${LONGBRIDGE_FEATURE_SNAPSHOT_PATH:-}" ]; then' "$workflow_file"
grep -Fq 'if [ "${REQUIRES_SNAPSHOT_MANIFEST_PATH:-}" = "true" ] && [ -z "${LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH:-}" ]; then' "$workflow_file"
grep -Fq '&& [ "${CONFIG_SOURCE_POLICY:-}" = "env_only" ] \' "$workflow_file"
grep -Fq '&& [ -z "${LONGBRIDGE_STRATEGY_CONFIG_PATH:-}" ]; then' "$workflow_file"
grep -Fq 'secret_pairs+=("TELEGRAM_TOKEN=${TELEGRAM_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD=${STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN=${STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN=${STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN=${STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN=${STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("LONGPORT_APP_KEY=${LONGPORT_APP_KEY_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("LONGPORT_APP_SECRET=${LONGPORT_APP_SECRET_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'LONGPORT_SECRET_NAME=${LONGPORT_SECRET_NAME}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_PATH=${LONGBRIDGE_FEATURE_SNAPSHOT_PATH}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH=${LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH}' "$workflow_file"
grep -Fq 'LONGBRIDGE_STRATEGY_CONFIG_PATH=${LONGBRIDGE_STRATEGY_CONFIG_PATH}' "$workflow_file"
grep -Fq 'LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON=${LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MIN_RESERVED_CASH_USD=${LONGBRIDGE_MIN_RESERVED_CASH_USD}' "$workflow_file"
grep -Fq 'LONGBRIDGE_RESERVED_CASH_RATIO=${LONGBRIDGE_RESERVED_CASH_RATIO}' "$workflow_file"
grep -Fq 'LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD=${LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD}' "$workflow_file"
grep -Fq 'remove_env_vars+=("LONGBRIDGE_MIN_RESERVED_CASH_USD")' "$workflow_file"
grep -Fq 'remove_env_vars+=("LONGBRIDGE_RESERVED_CASH_RATIO")' "$workflow_file"
grep -Fq 'remove_env_vars+=("LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD")' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_CHANNELS=${STRATEGY_PLUGIN_ALERT_CHANNELS}' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_CHANNELS")' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS=${STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL=${STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD=${STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST=${STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT=${STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY=${STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS=${STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_PROVIDER=${STRATEGY_PLUGIN_ALERT_SMS_PROVIDER}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID=${STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_SENDER=${STRATEGY_PLUGIN_ALERT_SMS_SENDER}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID=${STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL=${STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS=${STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS=${STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER=${STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL=${STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_DEVICE=${STRATEGY_PLUGIN_ALERT_PUSH_DEVICE}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY=${STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_TAGS=${STRATEGY_PLUGIN_ALERT_PUSH_TAGS}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS=${STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS=${STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_TELEGRAM_BODY_MAX_CHARS=${STRATEGY_PLUGIN_ALERT_TELEGRAM_BODY_MAX_CHARS}' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_SMS_PROVIDER")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_SMS_SENDER")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_DEVICE")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_TAGS")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN")' "$workflow_file"
grep -Fq 'remove_env_vars+=("STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS")' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_TO"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_GATEWAY"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_GMAIL_USER"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_GMAIL_APP_PASSWORD"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_RECIPIENTS"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_SENDER_EMAIL"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_SENDER_PASSWORD"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_SMTP_HOST"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_SMTP_PORT"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_GOOGLE_VOICE_SMTP_SECURITY"' "$workflow_file"
grep -Fq '"CRISIS_ALERT_SMTP_HOST"' "$workflow_file"
grep -Fq 'LONGBRIDGE_DRY_RUN_ONLY=${LONGBRIDGE_DRY_RUN_ONLY}' "$workflow_file"
grep -Fq 'INCOME_THRESHOLD_USD=${INCOME_THRESHOLD_USD}' "$workflow_file"
grep -Fq 'QQQI_INCOME_RATIO=${QQQI_INCOME_RATIO}' "$workflow_file"
grep -Fq 'STRATEGY_PROFILE=${STRATEGY_PROFILE}' "$workflow_file"
grep -Fq 'ACCOUNT_REGION=${ACCOUNT_REGION}' "$workflow_file"
grep -Fq 'RUNTIME_TARGET_JSON=${RUNTIME_TARGET_JSON}' "$workflow_file"
grep -Fq '"LONGBRIDGE_FRACTIONAL_SHARES_ENABLED"' "$workflow_file"
grep -Fq '"LONGBRIDGE_ORDER_QUANTITY_STEP"' "$workflow_file"
grep -Fq '"LONGBRIDGE_MIN_ORDER_NOTIONAL_USD"' "$workflow_file"
grep -Fq '"SERVICE_NAME"' "$workflow_file"
grep -Fq 'join_by_delimiter()' "$workflow_file"
grep -Fq 'gcloud_args+=(--remove-secrets "$(IFS=,; echo "${remove_secret_vars[*]}")")' "$workflow_file"
grep -Fq 'gcloud_args+=(--update-secrets "$(IFS=,; echo "${secret_pairs[*]}")")' "$workflow_file"
grep -Fq -- '--update-env-vars "^|^$(join_by_delimiter "|" "${env_pairs[@]}")"' "$workflow_file"
grep -Fq 'Sync Cloud Scheduler timezone' "$workflow_file"
grep -Fq 'scheduler_location="${CLOUD_SCHEDULER_LOCATION:-${CLOUD_RUN_REGION}}"' "$workflow_file"
grep -Fq 'timezone = os.environ.get("LONGBRIDGE_MARKET_TIMEZONE", "").strip()' "$workflow_file"
grep -Fq 'timezone = "Asia/Hong_Kong" if market == "HK" else "America/New_York"' "$workflow_file"
grep -Fq 'for suffix in scheduler probe-scheduler precheck-scheduler; do' "$workflow_file"
grep -Fq 'gcloud scheduler jobs describe "${job_name}"' "$workflow_file"
grep -Fq 'gcloud scheduler jobs update http "${job_name}"' "$workflow_file"
grep -Fq -- '--time-zone="${market_timezone}"' "$workflow_file"

if grep -Fq 'SERVICE_NAME: ${{ vars.SERVICE_NAME }}' "$workflow_file"; then
  echo "unexpected SERVICE_NAME env wiring still present" >&2
  exit 1
fi

if grep -Fq 'SERVICE_NAME=${SERVICE_NAME}' "$workflow_file"; then
  echo "unexpected SERVICE_NAME sync still present" >&2
  exit 1
fi

if grep -Fq 'LONGBRIDGE_FRACTIONAL_SHARES_ENABLED: ${{ vars.LONGBRIDGE_FRACTIONAL_SHARES_ENABLED }}' "$workflow_file"; then
  echo "unexpected LongBridge fractional-share env wiring still present" >&2
  exit 1
fi

if grep -Fq 'LONGBRIDGE_ORDER_QUANTITY_STEP: ${{ vars.LONGBRIDGE_ORDER_QUANTITY_STEP }}' "$workflow_file"; then
  echo "unexpected LongBridge order quantity step env wiring still present" >&2
  exit 1
fi

if grep -Fq 'LONGBRIDGE_MIN_ORDER_NOTIONAL_USD: ${{ vars.LONGBRIDGE_MIN_ORDER_NOTIONAL_USD }}' "$workflow_file"; then
  echo "unexpected LongBridge minimum order notional env wiring still present" >&2
  exit 1
fi

if grep -Fq 'LONGBRIDGE_FRACTIONAL_SHARES_ENABLED=${LONGBRIDGE_FRACTIONAL_SHARES_ENABLED}' "$workflow_file"; then
  echo "unexpected LongBridge fractional-share env sync still present" >&2
  exit 1
fi

if grep -Fq 'LONGBRIDGE_ORDER_QUANTITY_STEP=${LONGBRIDGE_ORDER_QUANTITY_STEP}' "$workflow_file"; then
  echo "unexpected LongBridge order quantity step env sync still present" >&2
  exit 1
fi

if grep -Fq 'LONGBRIDGE_MIN_ORDER_NOTIONAL_USD=${LONGBRIDGE_MIN_ORDER_NOTIONAL_USD}' "$workflow_file"; then
  echo "unexpected LongBridge minimum order notional env sync still present" >&2
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
