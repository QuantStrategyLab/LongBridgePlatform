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
grep -Fq -- '- paper-command-verify' "$workflow_file"
grep -Fq 'INPUT_DEPLOY_IMAGE: ${{ inputs.deploy_image }}' "$workflow_file"
grep -Fq 'Apply HK verify-only dispatch defaults' "$workflow_file"
grep -Fq 'Apply isolated paper-command verification defaults' "$workflow_file"
grep -Fq 'paper-command-verify targets only the PAPER deployment' "$workflow_file"
grep -Fq 'paper-command-verify requires a strategy profile, a dedicated command GCS URI, and a complete strategy release identity.' "$workflow_file"
grep -Fq 'paper-command-verify command storage must not reuse the execution report URI or its prefix.' "$workflow_file"
grep -Fq 'echo "RUNTIME_TARGET_ENABLED=false"' "$workflow_file"
grep -Fq 'echo "LONGBRIDGE_DURABLE_EXECUTION_COMMAND_PAPER_CONSUMER_ENABLED=true"' "$workflow_file"
grep -Fq 'hk-verify targets only the HK deployment' "$workflow_file"
grep -Fq '"strategy_profile": "hk_global_etf_tactical_rotation"' "$workflow_file"
grep -Fq 'echo "LONGBRIDGE_DRY_RUN_ONLY=true"' "$workflow_file"
grep -Fq 'echo "LONGBRIDGE_MARKET=HK"' "$workflow_file"
grep -Fq 'echo "LONGBRIDGE_SYMBOL_SUFFIX=.HK"' "$workflow_file"
grep -Fq 'CLOUD_RUN_ENV_SYNC_WAIT_FOR_COMMIT: ${{ vars.CLOUD_RUN_ENV_SYNC_WAIT_FOR_COMMIT }}' "$workflow_file"
grep -Fq 'CLOUD_SCHEDULER_LOCATION: ${{ vars.CLOUD_SCHEDULER_LOCATION }}' "$workflow_file"
grep -Fq 'CLOUD_SCHEDULER_MAIN_TIME: ${{ vars.CLOUD_SCHEDULER_MAIN_TIME }}' "$workflow_file"
grep -Fq 'CLOUD_SCHEDULER_PROBE_TIME: ${{ vars.CLOUD_SCHEDULER_PROBE_TIME }}' "$workflow_file"
grep -Fq 'CLOUD_SCHEDULER_PRECHECK_TIME: ${{ vars.CLOUD_SCHEDULER_PRECHECK_TIME }}' "$workflow_file"
grep -Fq 'CLOUD_RUN_SERVICE_TARGETS_JSON: ${{ vars.CLOUD_RUN_SERVICE_TARGETS_JSON || secrets.CLOUD_RUN_SERVICE_TARGETS_JSON }}' "$workflow_file"
grep -Fq 'GCP_SCHEDULER_SERVICE_ACCOUNT: longbridge-platform-scheduler@longbridgequant.iam.gserviceaccount.com' "$workflow_file"
grep -Fq 'Skipping Cloud Run commit wait because CLOUD_RUN_ENV_SYNC_WAIT_FOR_COMMIT is disabled.' "$workflow_file"
grep -Fq 'permissions:' "$workflow_file"
grep -Fq 'id-token: write' "$workflow_file"
grep -Fq 'workload_identity_provider: ${{ env.GCP_WORKLOAD_IDENTITY_PROVIDER }}' "$workflow_file"
grep -Fq 'service_account: ${{ env.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT }}' "$workflow_file"
grep -Fq 'uses: actions/checkout@v6' "$workflow_file"
grep -Fq 'uses: actions/setup-python@v6' "$workflow_file"
grep -Fq 'uv sync --frozen --no-dev' "$workflow_file"
grep -Fq 'id: strategy_requirements' "$workflow_file"
grep -Fq 'name: Resolve Cloud Run sync targets' "$workflow_file"
grep -Fq 'uv run --no-sync python scripts/build_cloud_run_env_sync_plan.py --json' "$workflow_file"
grep -Fq 'scripts/build_cloud_run_env_sync_plan.py --json' "$workflow_file"
grep -Fq 'sync_plan_json<<__SYNC_PLAN_JSON__' "$workflow_file"
grep -Fq 'SYNC_PLAN_JSON: ${{ steps.strategy_requirements.outputs.sync_plan_json }}' "$workflow_file"
grep -Fq 'Cloud Run env sync did not resolve any targets' "$workflow_file"
grep -Fq 'Cloud Run sync target is missing service_name' "$workflow_file"
grep -Fq 'Cloud Run sync target {service_name} is missing env' "$workflow_file"
grep -Fq 'for key, value in sorted(target["env"].items()):' "$workflow_file"
grep -Fq 'target.get("remove_env_vars")' "$workflow_file"
grep -Fq 'plan = json.loads(os.environ["SYNC_PLAN_JSON"])' "$workflow_file"
grep -Fq 'scheduler = target.get("scheduler") or {}' "$workflow_file"
grep -Fq 'Wait for Cloud Run deployment of current commit' "$workflow_file"
grep -Fq 'target_sha="${GITHUB_SHA}"' "$workflow_file"
grep -Fq "gcloud run services describe \"\${CLOUD_RUN_SERVICE}\" --region \"\${CLOUD_RUN_REGION}\" --format='value(spec.template.metadata.labels.commit-sha)'" "$workflow_file"
grep -Fq 'Timed out waiting for Cloud Run service ${CLOUD_RUN_SERVICE} to deploy commit ${target_sha}. Last seen commit: ${deployed_sha:-<none>}' "$workflow_file"
grep -Fq 'ENABLE_GITHUB_ENV_SYNC: ${{ vars.ENABLE_GITHUB_ENV_SYNC }}' "$workflow_file"
grep -Fq 'ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION: ${{ vars.ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION }}' "$workflow_file"
grep -Fq 'GLOBAL_TELEGRAM_CHAT_ID: ${{ secrets.GLOBAL_TELEGRAM_CHAT_ID }}' "$workflow_file"
grep -Fq 'TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD: ${{ secrets.STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN }}' "$workflow_file"
grep -Fq 'STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN: ${{ secrets.TG_TOKEN }}' "$workflow_file"
grep -Fq 'TELEGRAM_TOKEN_SECRET_NAME: ${{ vars.TELEGRAM_TOKEN_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_APP_KEY_SECRET_NAME: ${{ vars.LONGPORT_APP_KEY_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_APP_SECRET_SECRET_NAME: ${{ vars.LONGPORT_APP_SECRET_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGPORT_SECRET_NAME: ${{ vars.LONGPORT_SECRET_NAME }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_PATH: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_FALLBACK_MODE: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_FALLBACK_MODE }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_FALLBACK_CACHE_DIR: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_FALLBACK_CACHE_DIR }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_FEATURE_SNAPSHOT_MAX_STALE_DAYS: ${{ vars.LONGBRIDGE_FEATURE_SNAPSHOT_MAX_STALE_DAYS }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_STRATEGY_CONFIG_PATH: ${{ vars.LONGBRIDGE_STRATEGY_CONFIG_PATH }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON: ${{ vars.LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MIN_RESERVED_CASH_USD: ${{ vars.LONGBRIDGE_MIN_RESERVED_CASH_USD }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_RESERVED_CASH_RATIO: ${{ vars.LONGBRIDGE_RESERVED_CASH_RATIO }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD: ${{ vars.LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MARKET_SIGNAL_HANDOFF_INDEX_URI: ${{ vars.LONGBRIDGE_MARKET_SIGNAL_HANDOFF_INDEX_URI }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MARKET_SIGNAL_HANDOFF_MANIFEST_URI: ${{ vars.LONGBRIDGE_MARKET_SIGNAL_HANDOFF_MANIFEST_URI }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MARKET_SIGNAL_CONSUMPTION_AUDIT_URI: ${{ vars.LONGBRIDGE_MARKET_SIGNAL_CONSUMPTION_AUDIT_URI }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MARKET_SIGNAL_CACHE_DIR: ${{ vars.LONGBRIDGE_MARKET_SIGNAL_CACHE_DIR }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MARKET_SIGNAL_REQUIRED: ${{ vars.LONGBRIDGE_MARKET_SIGNAL_REQUIRED }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MARKET_SIGNAL_FALLBACK_MODE: ${{ vars.LONGBRIDGE_MARKET_SIGNAL_FALLBACK_MODE }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_MARKET_SIGNAL_MAX_STALE_DAYS: ${{ vars.LONGBRIDGE_MARKET_SIGNAL_MAX_STALE_DAYS }}' "$workflow_file"
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
grep -Fq 'INCOME_LAYER_START_USD: ${{ vars.INCOME_LAYER_START_USD }}' "$workflow_file"
grep -Fq 'DCA_MODE: ${{ vars.DCA_MODE }}' "$workflow_file"
grep -Fq 'DCA_BASE_INVESTMENT_USD: ${{ vars.DCA_BASE_INVESTMENT_USD }}' "$workflow_file"
grep -Fq 'IBIT_ZSCORE_EXIT_ENABLED: ${{ vars.IBIT_ZSCORE_EXIT_ENABLED }}' "$workflow_file"
grep -Fq 'IBIT_ZSCORE_EXIT_MODE: ${{ vars.IBIT_ZSCORE_EXIT_MODE }}' "$workflow_file"
grep -Fq 'IBIT_ZSCORE_EXIT_PARKING_SYMBOL: ${{ vars.IBIT_ZSCORE_EXIT_PARKING_SYMBOL }}' "$workflow_file"
grep -Fq 'IBIT_ZSCORE_EXIT_RISK_REDUCED_EXPOSURE: ${{ vars.IBIT_ZSCORE_EXIT_RISK_REDUCED_EXPOSURE }}' "$workflow_file"
grep -Fq 'IBIT_ZSCORE_EXIT_RISK_OFF_EXPOSURE: ${{ vars.IBIT_ZSCORE_EXIT_RISK_OFF_EXPOSURE }}' "$workflow_file"
grep -Fq 'IBIT_ZSCORE_EXIT_ALLOW_OUTSIDE_EXECUTION_WINDOW: ${{ vars.IBIT_ZSCORE_EXIT_ALLOW_OUTSIDE_EXECUTION_WINDOW }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_DRY_RUN_ONLY: ${{ vars.LONGBRIDGE_DRY_RUN_ONLY }}' "$workflow_file"
grep -Fq 'LIFECYCLE_PERFORMANCE_BUCKET: ${{ vars.LIFECYCLE_PERFORMANCE_BUCKET }}' "$workflow_file"
grep -Fq 'GOOGLE_CLOUD_PROJECT: ${{ vars.GOOGLE_CLOUD_PROJECT || env.GCP_PROJECT_ID }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_DURABLE_EXECUTION_COMMAND_PAPER_ENABLED: ${{ vars.LONGBRIDGE_DURABLE_EXECUTION_COMMAND_PAPER_ENABLED }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_DURABLE_EXECUTION_COMMAND_PAPER_CONSUMER_ENABLED: ${{ vars.LONGBRIDGE_DURABLE_EXECUTION_COMMAND_PAPER_CONSUMER_ENABLED }}' "$workflow_file"
grep -Fq 'LONGBRIDGE_EXECUTION_COMMAND_CLOUD_URI: ${{ vars.LONGBRIDGE_EXECUTION_COMMAND_CLOUD_URI }}' "$workflow_file"
grep -Fq 'RUNTIME_TARGET_JSON: ${{ vars.RUNTIME_TARGET_JSON || secrets.RUNTIME_TARGET_JSON }}' "$workflow_file"
grep -Fq 'ACCOUNT_REGION: ${{ vars.ACCOUNT_REGION || matrix.target.default_account_region }}' "$workflow_file"
grep -Fq 'write_github_output "enabled=false"' "$workflow_file"
grep -Fq 'Skipping ${DEPLOYMENT_LABEL} Cloud Run automation because ENABLE_GITHUB_CLOUD_RUN_DEPLOY and ENABLE_GITHUB_ENV_SYNC are not true.' "$workflow_file"
grep -Fq 'Skipping ${DEPLOYMENT_LABEL} Cloud Run automation on push because ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION is not true.' "$workflow_file"
grep -Fq '${DEPLOYMENT_LABEL} Cloud Run env sync is enabled, but these values are missing:' "$workflow_file"
grep -Fq 'Set CLOUD_RUN_REGION on the ${GITHUB_ENVIRONMENT_NAME} Environment so each service can target its own region.' "$workflow_file"
grep -Fq 'Set LONGPORT_APP_KEY_SECRET_NAME and LONGPORT_APP_SECRET_SECRET_NAME on the ${GITHUB_ENVIRONMENT_NAME} Environment so credentials do not fall back to shared defaults.' "$workflow_file"
grep -Fq "if: steps.config.outputs.enabled == 'true'" "$workflow_file"
grep -Fq 'missing_vars+=("TELEGRAM_TOKEN_SECRET_NAME or TELEGRAM_TOKEN")' "$workflow_file"
grep -Fq 'missing_vars+=("LONGPORT_APP_KEY_SECRET_NAME")' "$workflow_file"
grep -Fq 'missing_vars+=("LONGPORT_APP_SECRET_SECRET_NAME")' "$workflow_file"
grep -Fq 'secret_pairs+=("TELEGRAM_TOKEN=${TELEGRAM_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD=${STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN=${STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN=${STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN=${STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN=${STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("LONGPORT_APP_KEY=${LONGPORT_APP_KEY_SECRET_NAME}:latest")' "$workflow_file"
grep -Fq 'secret_pairs+=("LONGPORT_APP_SECRET=${LONGPORT_APP_SECRET_SECRET_NAME}:latest")' "$workflow_file"
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
grep -Fq '"LONGBRIDGE_FRACTIONAL_SHARES_ENABLED"' "$workflow_file"
grep -Fq '"LONGBRIDGE_DURABLE_EXECUTION_COMMAND_PAPER_ENABLED"' "$workflow_file"
grep -Fq '"LONGBRIDGE_EXECUTION_COMMAND_CLOUD_URI"' "$workflow_file"
grep -Fq '"LONGBRIDGE_ORDER_QUANTITY_STEP"' "$workflow_file"
grep -Fq '"LONGBRIDGE_MIN_ORDER_NOTIONAL_USD"' "$workflow_file"
grep -Fq '"SERVICE_NAME"' "$workflow_file"
grep -Fq 'join_by_delimiter()' "$workflow_file"
grep -Fq 'gcloud_args+=(--remove-secrets "$(IFS=,; echo "${remove_secret_vars[*]}")")' "$workflow_file"
grep -Fq 'gcloud_args+=(--update-secrets "$(IFS=,; echo "${secret_pairs[*]}")")' "$workflow_file"
grep -Fq -- '--update-env-vars "^|^$(join_by_delimiter "|" "${env_pairs[@]}")"' "$workflow_file"
grep -Fq 'Sync Cloud Scheduler schedule' "$workflow_file"
grep -Fq 'scheduler_location="${CLOUD_SCHEDULER_LOCATION:-${CLOUD_RUN_REGION}}"' "$workflow_file"
grep -Fq 'target_env = target.get("env") or {}' "$workflow_file"
grep -Fq 'runtime_target_enabled="${scheduler_config[4]}"' "$workflow_file"
grep -Fq 'scheduler_job_candidates=("${CLOUD_RUN_SERVICE}-scheduler")' "$workflow_file"
grep -Fq 'current_schedule="$(gcloud scheduler jobs describe "${candidate_job}"' "$workflow_file"
grep -Fq 'desired_schedule="$(CURRENT_SCHEDULE="${current_schedule}" SCHEDULE_TIME="${main_time}" python - <<' "$workflow_file"
grep -Fq 'if len(time_fields) == 5:' "$workflow_file"
grep -Fq 'print(" ".join(time_fields))' "$workflow_file"
grep -Fq 'print(" ".join([*time_fields, *current_fields[2:]]))' "$workflow_file"
grep -Fq 'gcloud scheduler jobs update http "${job_name}"' "$workflow_file"
grep -Fq 'gcloud scheduler jobs create http "${job_name}"' "$workflow_file"
grep -Fq -- '--no-allow-unauthenticated' "$workflow_file"
grep -Fq -- '--concurrency=1' "$workflow_file"
grep -Fq 'probe_job_name="${CLOUD_RUN_SERVICE}-probe-scheduler"' "$workflow_file"
grep -Fq 'probe_uri="${service_url}/probe"' "$workflow_file"
grep -Fq 'precheck_job_name="${CLOUD_RUN_SERVICE}-precheck-scheduler"' "$workflow_file"
grep -Fq 'precheck_uri="${service_url}/dry-run"' "$workflow_file"
grep -Fq 'managed_scheduler_jobs=("${job_name}" "${probe_job_name}" "${precheck_job_name}")' "$workflow_file"
grep -Fq 'gcloud scheduler jobs resume "${managed_job_name}"' "$workflow_file"
grep -Fq 'gcloud scheduler jobs pause "${managed_job_name}"' "$workflow_file"
grep -Fq 'monitor_job_name="longbridge-monitor-dispatcher-scheduler"' "$workflow_file"
grep -Fq 'gcloud scheduler jobs delete "${monitor_job_name}"' "$workflow_file"
grep -Fq 'Reconcile Cloud Run traffic' "$workflow_file"
grep -Fq 'python3 scripts/reconcile_cloud_runtime.py --platform longbridge --ensure-latest-traffic --service "${CLOUD_RUN_SERVICE}"' "$workflow_file"
grep -Fq 'Reconcile legacy Cloud Scheduler jobs' "$workflow_file"
grep -Fq 'python3 scripts/reconcile_cloud_runtime.py --platform longbridge --delete-legacy-schedulers --service "${CLOUD_RUN_SERVICE}"' "$workflow_file"
grep -Fq -- '--schedule="${desired_schedule}"' "$workflow_file"
grep -Fq -- '--time-zone="${market_timezone}"' "$workflow_file"

if grep -Fq 'monitor_uri="${service_url}/monitor-dispatch"' "$workflow_file"; then
  echo "unexpected shared monitor dispatcher creation still present" >&2
  exit 1
fi

if grep -Fq 'legacy_jobs=(' "$workflow_file"; then
  echo "unexpected inline legacy scheduler deletion logic still present" >&2
  exit 1
fi

if grep -Fq 'legacy_scheduler_locations=' "$workflow_file"; then
  echo "unexpected inline legacy scheduler location logic still present" >&2
  exit 1
fi

if grep -Fq 'gcloud scheduler jobs delete "${legacy_job}"' "$workflow_file"; then
  echo "unexpected inline legacy scheduler deletion command still present" >&2
  exit 1
fi

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

# Execute the workflow's actual shell blocks with local command stubs only.
python3 - "$workflow_file" <<'PY'
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap

workflow = Path(sys.argv[1]).read_text()
lifecycle = Path(sys.argv[1]).with_name("runtime-target-lifecycle.yml").read_text()
run_name = re.search(r"^run-name: \$\{\{ (.+) \}\}$", workflow, re.MULTILINE)
assert run_name, "image-only staging must identify downstream lifecycle exclusion"
lifecycle_job = lifecycle.split("  lifecycle:\n", 1)[1]
lifecycle_if = re.search(r"^    if: (.+)$", lifecycle_job, re.MULTILINE)
assert lifecycle_if, "lifecycle must skip image-only completions before matrix authentication"
# Evaluate the actual two comparison/boolean expressions for dispatch and follow-up events.
for mode in ("legacy", "image-only-no-traffic"):
    title = eval(run_name[1].replace("inputs.deployment_mode", "mode").replace("&&", "and").replace("||", "or"),
                 {"__builtins__": {}}, {"mode": mode})
    for event in ("workflow_run", "workflow_dispatch", "schedule"):
        condition = lifecycle_if[1].replace("github.event_name", "event").replace(
            "github.event.workflow_run.display_title", "title").replace("||", "or")
        allowed = eval(condition, {"__builtins__": {}}, {"event": event, "title": title})
        assert allowed == (event != "workflow_run" or mode == "legacy"), (mode, event)
print("lifecycle event condition cases: 6 passed")
assert "  image-only-no-traffic:\n" in workflow, "missing explicit no-traffic job"
job = workflow.split("  image-only-no-traffic:\n", 1)[1].split("\n  sync:\n", 1)[0]
assert "matrix" not in job, "image-only mode must select exactly one environment"
assert "inputs.deployment_mode == 'image-only-no-traffic'" in job
assert "inputs.target == 'PAPER'" in job and "inputs.target == 'HK'" in job and "inputs.target == 'SG'" in job
assert "ref: ${{ github.sha }}" in job
legacy = workflow.split("\n  sync:\n", 1)[1].split("\n  cleanup-shared-monitor:\n", 1)[0]
cleanup = workflow.split("\n  cleanup-shared-monitor:\n", 1)[1]
for section in (legacy, cleanup):
    assert "inputs.deployment_mode != 'image-only-no-traffic'" in section.split("    steps:", 1)[0]
assert job.index("name: Validate image-only dispatch") < job.index("uses: google-github-actions/auth@")
assert job.index("name: Verify exact image-only source") < job.index("uses: google-github-actions/auth@")
# The isolated job cannot bind broker/runtime credentials or run legacy helpers.
assert re.findall(r"secrets\.([A-Z_]+)", job) == ["CLOUD_RUN_SERVICE"]
for forbidden in ("scripts/", "sync_plan", "scheduler", "cleanup", "retire", "update-traffic"):
    assert forbidden not in job.lower(), forbidden


def run_block(name):
    step = job.split(f"      - name: {name}\n", 1)[1].split("\n      - ", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


blocks = [run_block(name) for name in (
    "Validate image-only dispatch", "Verify exact image-only source", "Build and stage image without traffic",
)]

with tempfile.TemporaryDirectory(prefix="lb-no-traffic-test-") as directory:
    root = Path(directory)
    log = root / "calls.jsonl"
    stub = "#!" + sys.executable + "\n" + '''
import json, os, sys
from pathlib import Path
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as stream:
    stream.write(json.dumps([command, *args]) + "\\n")
if command == "git" and args == ["rev-parse", "HEAD"]:
    print(os.environ["CHECKOUT_SHA"])
elif command == "git" and args == ["archive", "HEAD"]:
    print("synthetic tracked source archive")
elif command == "docker" and args[0] in ("build", "push"):
    pass
elif command == "gcloud" and args[:3] == ["run", "services", "describe"]:
    if os.environ.get("SERVICE_MISSING") == "1":
        sys.exit(1)
    print(os.environ["CLOUD_RUN_SERVICE"])
elif command == "gcloud" and args[:2] == ["auth", "configure-docker"]:
    pass
elif command == "gcloud" and args[:4] == ["artifacts", "docker", "images", "describe"]:
    print(os.environ["IMAGE_DIGEST"])
elif command == "gcloud" and args[:3] == ["run", "services", "update"]:
    if os.environ.get("UPDATE_FAIL") == "1":
        sys.exit(1)
else:
    raise SystemExit("unexpected command in image-only workflow")
'''
    for command in ("git", "docker", "gcloud"):
        path = root / command
        path.write_text(stub)
        path.chmod(0o700)
    base = {
        "PATH": f"{root}:/usr/bin:/bin", "HOME": str(root), "STUB_LOG": str(log),
        "DEPLOYMENT_MODE": "image-only-no-traffic", "WORKFLOW_TARGET": "PAPER",
        "APPROVED_REF": "main", "SOURCE_COMMIT": "a" * 40,
        "GITHUB_REF_NAME": "main", "GITHUB_SHA": "a" * 40, "CHECKOUT_SHA": "a" * 40,
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "123",
        "GITHUB_REPOSITORY": "synthetic/repository", "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_SHA": "a" * 40,
        "GITHUB_WORKFLOW_REF": "synthetic/repository/.github/workflows/sync-cloud-run-env.yml@refs/heads/main",
        "CLOUD_RUN_SERVICE": "synthetic-paper", "CLOUD_RUN_REGION": "synthetic-region",
        "GCP_PROJECT_ID": "synthetic-project", "GCP_ARTIFACT_REGISTRY_HOSTNAME": "registry.invalid",
        "GCP_ARTIFACT_REGISTRY_REPOSITORY": "synthetic-images", "IMAGE_DIGEST": "sha256:" + "b" * 64,
    }

    def execute(**overrides):
        log.write_text("")
        result = subprocess.run(
            ["bash", "-c", "\n".join(blocks)], env={**base, **overrides},
            text=True, capture_output=True, cwd=root,
        )
        return result.returncode, [json.loads(line) for line in log.read_text().splitlines()]

    cases = 0
    for label in ("PAPER", "HK", "SG"):
        code, calls = execute(WORKFLOW_TARGET=label, CLOUD_RUN_SERVICE=f"synthetic-{label.lower()}")
        assert code == 0, label
        updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
        assert len(updates) == 1
        service = f"synthetic-{label.lower()}"
        image_repo = f"registry.invalid/synthetic-project/synthetic-images/longbridgeplatform/{service}"
        assert updates[0] == [
            "gcloud", "run", "services", "update", service,
            "--project=synthetic-project", "--region=synthetic-region",
            f"--image={image_repo}@{base['IMAGE_DIGEST']}", "--no-traffic",
            f"--update-labels=commit-sha={base['SOURCE_COMMIT']},github-run-id=123", "--quiet",
        ]
        assert sum(call[:2] == ["docker", "push"] for call in calls) == 1
        assert [call for call in calls if call[:2] == ["docker", "build"]] == [
            ["docker", "build", "--pull", "-t", f"{image_repo}:{base['SOURCE_COMMIT']}-123", "-"],
        ]
        assert ["git", "archive", "HEAD"] in calls
        cases += 1
    for overrides in (
        {"WORKFLOW_TARGET": "configured"}, {"WORKFLOW_TARGET": "hk-verify"},
        {"WORKFLOW_TARGET": "paper-command-verify"}, {"WORKFLOW_TARGET": ""},
        {"DEPLOYMENT_MODE": "legacy"}, {"GITHUB_EVENT_NAME": "push"},
        {"APPROVED_REF": "other"}, {"APPROVED_REF": ""},
        {"SOURCE_COMMIT": "main"}, {"SOURCE_COMMIT": "c" * 40},
        {"GITHUB_WORKFLOW_SHA": "c" * 40}, {"GITHUB_WORKFLOW_REF": "other-workflow"},
        {"CLOUD_RUN_SERVICE": ""}, {"CLOUD_RUN_REGION": ""},
    ):
        code, calls = execute(**overrides)
        assert code != 0 and calls == [], overrides
        cases += 1
    for overrides in ({"CHECKOUT_SHA": "c" * 40}, {"SERVICE_MISSING": "1"}, {"IMAGE_DIGEST": "not-a-digest"}):
        code, calls = execute(**overrides)
        assert code != 0, overrides
        assert not any(call[:4] == ["gcloud", "run", "services", "update"] for call in calls)
        cases += 1
    code, calls = execute(UPDATE_FAIL="1")
    assert code != 0
    assert sum(call[:4] == ["gcloud", "run", "services", "update"] for call in calls) == 1
    cases += 1
    print(f"image-only no-traffic shell cases: {cases} passed")
PY

# Execute the real build command against a synthetic checkout. Authentication
# files created after checkout must never enter the container build context.
python3 - "$workflow_file" <<'PY'
import os
from pathlib import Path
import subprocess
import sys
import tempfile

build_commands = [line.strip() for line in Path(sys.argv[1]).read_text().splitlines() if "docker build --pull" in line]
assert len(build_commands) == 2, "both image-only and legacy builds must be exercised"
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    checkout = root / "checkout"
    checkout.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    def git(*args):
        subprocess.run(["git", *args], cwd=checkout, env=env, check=True, capture_output=True)
    git("init", "-q")
    (checkout / "Dockerfile").write_text("FROM scratch\nCOPY . /app/\n")
    (checkout / "tracked.txt").write_text("synthetic source\n")
    git("add", "Dockerfile", "tracked.txt")
    git("-c", "user.name=synthetic", "-c", "user.email=synthetic@example.invalid", "-c", "commit.gpgSign=false", "commit", "-qm", "synthetic")
    (checkout / "gha-creds-synthetic.json").write_text('{"synthetic":true}\n')
    bin_dir = root / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(f"#!{sys.executable}\n" + '''import sys, tarfile
if sys.argv[-1] != "-":
    raise SystemExit("workspace build context can include generated credentials")
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
    names = {member.name for member in archive}
if names != {"Dockerfile", "tracked.txt"}:
    raise SystemExit("build context must contain only the approved tracked source")
''')
    docker.chmod(0o700)
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", image="synthetic:test")
    for index, build_command in enumerate(build_commands, 1):
        result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + build_command], cwd=checkout, env=env, capture_output=True)
        if result.returncode:
            raise SystemExit(f"FAIL: build {index} admits a workspace context instead of tracked source")
print("PASS: both tracked-source builds exclude generated authentication files")
PY
