#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
workflow_file="$repo_dir/.github/workflows/sync-cloud-run-env.yml"

grep -Fq 'GCP_WORKLOAD_IDENTITY_PROVIDER: projects/252919773759/locations/global/workloadIdentityPools/github-actions/providers/github-main' "$workflow_file"
grep -Fq 'GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT: longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com' "$workflow_file"
grep -Fq 'name: Deploy / Sync ${{ matrix.target.label }} Cloud Run' "$workflow_file"
grep -Fq 'fail-fast: false' "$workflow_file"
grep -Fq 'resolve-matrix:' "$workflow_file"
grep -Fq 'render_runtime_target_matrix.py --profile sync --github-output' "$workflow_file"
grep -Fq 'needs: resolve-matrix' "$workflow_file"
grep -Fq 'matrix: ${{ fromJSON(needs.resolve-matrix.outputs.matrix) }}' "$workflow_file"
grep -Fq "inputs.target == 'PAPER' && 'longbridge-paper'" "$workflow_file"
grep -Fq "inputs.target == 'HK' && 'longbridge-hk'" "$workflow_file"
grep -Fq "inputs.target == 'SG' && 'longbridge-sg'" "$workflow_file"
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
# probe/precheck may collide on maxScale=1; retry transient 429 capacity aborts.
# Keep /run without retries to avoid duplicate live submits.
grep -Fq -- '--max-retry-attempts=3' "$workflow_file"
grep -Fq -- '--min-backoff=120s' "$workflow_file"
grep -Fq -- '--max-backoff=300s' "$workflow_file"
grep -Fq -- '--max-retry-duration=900s' "$workflow_file"
grep -Fq 'managed_scheduler_jobs=("${job_name}" "${probe_job_name}" "${precheck_job_name}")' "$workflow_file"
grep -Fq 'shift_traffic_to_commit:' "$workflow_file"
grep -Fq 'scheduler_enabled_action:' "$workflow_file"
grep -Fq 'INPUT_SHIFT_TRAFFIC_TO_COMMIT: ${{ inputs.shift_traffic_to_commit }}' "$workflow_file"
grep -Fq 'INPUT_SCHEDULER_ENABLED_ACTION: ${{ inputs.scheduler_enabled_action }}' "$workflow_file"
grep -Fq 'traffic_shift_enabled=${traffic_shift_enabled}' "$workflow_file"
grep -Fq 'scheduler_enabled_action=${scheduler_enabled_action}' "$workflow_file"
grep -Fq -- '--no-traffic' "$workflow_file"
grep -Fq 'Shift Cloud Run traffic to this commit' "$workflow_file"
grep -Fq 'Read back Cloud Run traffic without shifting' "$workflow_file"
grep -Fq 'Apply explicit Scheduler enabled-state action' "$workflow_file"
grep -Fq -- '--ensure-latest-traffic' "$workflow_file"
grep -Fq -- '--read-traffic' "$workflow_file"
grep -Fq -- '--read-scheduler-enabled' "$workflow_file"
grep -Fq -- '--scheduler-enabled-action' "$workflow_file"
grep -Fq 'Pausing newly created Cloud Scheduler job' "$workflow_file"
grep -Fq 'gcloud scheduler jobs pause "${managed_job_name}"' "$workflow_file"
grep -Fq 'monitor_job_name="longbridge-monitor-dispatcher-scheduler"' "$workflow_file"
grep -Fq 'gcloud scheduler jobs delete "${monitor_job_name}"' "$workflow_file"
grep -Fq 'Reconcile legacy Cloud Scheduler jobs' "$workflow_file"
grep -Fq 'python3 scripts/reconcile_cloud_runtime.py --platform longbridge --delete-legacy-schedulers --service "${CLOUD_RUN_SERVICE}"' "$workflow_file"
grep -Fq -- '--schedule="${desired_schedule}"' "$workflow_file"
grep -Fq -- '--time-zone="${market_timezone}"' "$workflow_file"

# Counter-examples: ordinary image/env sync must not imply traffic or pause/resume.
python3 - "$workflow_file" <<'PY'
from pathlib import Path
import re
import sys

workflow = Path(sys.argv[1]).read_text()
legacy = workflow.split("\n  sync:\n", 1)[1].split("\n  cleanup-shared-monitor:\n", 1)[0]

deploy = legacy.split("      - name: Build, push, and deploy Cloud Run image\n", 1)[1]
deploy = deploy.split("\n      - name: Wait for Cloud Run deployment of current commit\n", 1)[0]
assert "--no-traffic" in deploy, "legacy image deploy must preserve serving traffic"

env_sync = legacy.split("      - name: Sync Cloud Run environment\n", 1)[1]
env_sync = env_sync.split("\n      - name: Verify strategy plugin mounts\n", 1)[0]
assert "--no-traffic" in env_sync, "legacy env sync must preserve serving traffic"

traffic_header = [
    line for line in legacy.splitlines()
    if "Shift Cloud Run traffic to this commit" in line or "traffic_shift_enabled" in line
]
assert any("if: steps.config.outputs.traffic_shift_enabled == 'true'" in line for line in legacy.splitlines())
assert "Reconcile Cloud Run traffic" not in legacy

scheduler = legacy.split("      - name: Sync Cloud Scheduler schedule\n", 1)[1]
scheduler = scheduler.split("\n      - name: Apply explicit Scheduler enabled-state action\n", 1)[0]
assert "RUNTIME_TARGET_ENABLED" not in scheduler
assert "gcloud scheduler jobs resume" not in scheduler
assert "is enabled." not in scheduler
assert "is disabled." not in scheduler

assert any(
    "if: steps.config.outputs.scheduler_enabled_action != 'preserve'" in line
    for line in legacy.splitlines()
)
assert "--scheduler-enabled-action" in legacy
assert "--read-scheduler-enabled" in legacy
assert "--read-traffic" in legacy

# Desired-state coupling must not remain in the workflow.
assert not re.search(
    r"Resuming Cloud Scheduler job \$\{managed_job_name\} because \$\{CLOUD_RUN_SERVICE\} is enabled",
    workflow,
)
assert not re.search(
    r"Pausing Cloud Scheduler job \$\{managed_job_name\} because \$\{CLOUD_RUN_SERVICE\} is disabled",
    workflow,
)
print("runtime write isolation counter-examples: passed")
PY

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
job = workflow.split("  image-only-no-traffic:\n", 1)[1].split("\n  resolve-matrix:\n", 1)[0]
assert "matrix" not in job, "image-only mode must select exactly one environment"
assert "inputs.deployment_mode == 'image-only-no-traffic'" in job
assert "inputs.target == 'PAPER'" in job and "inputs.target == 'HK'" in job and "inputs.target == 'SG'" in job
assert "ref: ${{ github.sha }}" in job
assert "  resolve-matrix:\n" in workflow
assert "render_runtime_target_matrix.py --profile sync --github-output" in workflow
assert "matrix: ${{ fromJSON(needs.resolve-matrix.outputs.matrix) }}" in workflow
legacy = workflow.split("\n  sync:\n", 1)[1].split("\n  cleanup-shared-monitor:\n", 1)[0]
cleanup = workflow.split("\n  cleanup-shared-monitor:\n", 1)[1]
for section in (legacy, cleanup):
    assert "inputs.deployment_mode != 'image-only-no-traffic'" in section.split("    steps:", 1)[0]
assert "needs: resolve-matrix" in legacy.split("    steps:", 1)[0]
assert job.index("name: Validate image-only dispatch") < job.index("uses: google-github-actions/auth@")
assert job.index("name: Verify exact image-only source") < job.index("uses: google-github-actions/auth@")
# The isolated job cannot bind broker/runtime credentials or run legacy helpers.
assert re.findall(r"secrets\.([A-Z_]+)", job) == ["CLOUD_RUN_SERVICE"]
assert "scripts/verify_deployed_runtime_target_admission.py" in job
assert "record_daily_account_snapshot" not in job
assert "git checkout" not in job
assert 'git archive "${SOURCE_COMMIT}"' not in job
assert "git archive ${{" not in job
assert "0b939723c1db3ef59175535998b470cbcd4b8824" in job
assert "d8314a61df697cae1dd03a78ddc5c2fc4179ec67" in job
assert "318bf0419ec91002fd0f0dfd1bc80a0664a85e79" in job
assert "82788aa7c73690d2b87f63540a9102943e9372eb" in job
assert "df3d29d13daeffc7b4af9fec5db0e9ec1f07b60f" in job
assert "779d8e1c39d161144bc34ae8503ce34d87971b16" in job
assert "approved_probe_financing_candidate=82788aa7c73690d2b87f63540a9102943e9372eb" in job
assert 'image_mode=probe-financing' in job
assert '[ "${GITHUB_REPOSITORY:-}" != "QuantStrategyLab/LongBridgePlatform" ]' in job
assert '[ "${SOURCE_COMMIT}" = "${GITHUB_SHA}" ] || [ "${SOURCE_COMMIT}" = "${approved_candidate}" ]' not in job
for forbidden in ("sync_plan", "scheduler", "cleanup", "retire"):
    assert forbidden not in job.lower(), forbidden
assert "hk-account-snapshot" in job
assert "update-traffic" in job.lower()
assert 'if [ "${image_mode}" = "hk-account-snapshot" ]; then' in job


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
approved = "0b939723c1db3ef59175535998b470cbcd4b8824"
http_snapshot_candidate = "d8314a61df697cae1dd03a78ddc5c2fc4179ec67"
probe_snapshot_candidate = "318bf0419ec91002fd0f0dfd1bc80a0664a85e79"
probe_financing_candidate = "82788aa7c73690d2b87f63540a9102943e9372eb"
probe_candidates = (probe_snapshot_candidate, probe_financing_candidate)
sghk_snapshot_candidate = "df3d29d13daeffc7b4af9fec5db0e9ec1f07b60f"
hk_probe_diagnostics_candidate = "779d8e1c39d161144bc34ae8503ce34d87971b16"
staged_source_revision = "longbridge-quant-paper-service-r36423178119"
staged_source_image = (
    "asia-east1-docker.pkg.dev/synthetic-project/images/longbridgeplatform/synthetic-paper"
    "@sha256:9a3260ca8255873309b1c27cd2fd7403e6006a4e9088241e6f89a4e5e65f7a10"
)
history = {
    "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
    "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/account_snapshots",
    "ACCOUNT_HISTORY_TARGET_ID": "paper",
    "ACCOUNT_HISTORY_EXPECTED_SCOPE": "PAPER",
}
serving_sha = "1" * 40
ues = "e" * 40

def declaration(name, pin):
    if name == "uv.lock":
        return (
            '[[package]]\\nname = "us-equity-strategies"\\n'
            'source = { git = "https://github.com/QuantStrategyLab/UsEquityStrategies.git?rev=%s#%s" }\\n' % (pin, pin)
        )
    if name == "pyproject.toml":
        return (
            "[project]\\ndependencies = ["
            '"us-equity-strategies @ git+https://github.com/QuantStrategyLab/UsEquityStrategies.git@%s"'
            "]\\n" % pin
        )
    if name == "qsl.toml":
        return '[qsl.requires]\\nus_equity_strategies = "%s"\\n' % pin
    raise SystemExit("unexpected declaration")

def base_env():
    service = os.environ["CLOUD_RUN_SERVICE"]
    label = os.environ.get("WORKFLOW_TARGET", "PAPER")
    scope = os.environ.get("TARGET_ACCOUNT_SCOPE", label)
    profile = "tqqq_growth_income" if label == "HK" else "soxl_soxx_trend_income" if label == "SG" else "russell_top50_leader_rotation"
    target = {
        "platform_id": "longbridge",
        "service_name": service,
        "account_scope": scope,
        "account_selector": [scope],
        "deployment_selector": "HK" if label == "HK" else "SG" if label == "SG" else "PAPER",
        "strategy_profile": profile,
        "execution_mode": "live",
        "dry_run_only": False,
    }
    return [
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
        {"name": "STRATEGY_PROFILE", "value": profile},
        {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": "false"},
        {"name": "RUNTIME_TARGET_ENABLED", "value": os.environ.get("TARGET_RUNTIME_ENABLED", "false") if os.environ.get("SOURCE_COMMIT") == hk_probe_diagnostics_candidate else "true"},
    ]

def hk_retained_history():
    return {
        "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
        "ACCOUNT_HISTORY_GCS_PREFIX": "gs://qsl-runtime-logs-shared/longbridge/account_snapshots",
        "ACCOUNT_HISTORY_TARGET_ID": "hk",
        "ACCOUNT_HISTORY_EXPECTED_SCOPE": "HK",
    }

def service_document(ingress):
    env = base_env()
    template = {"spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env, "image": "serving-image"}]}}
    status = {"traffic": [{"revisionName": "serving-rev", "percent": 100}], "latestReadyRevisionName": "ignored-latest"}
    if os.environ.get("SOURCE_COMMIT") == http_snapshot_candidate:
        env.extend({"name": key, "value": value} for key, value in history.items())
        template["metadata"] = {"name": staged_source_revision}
        template["spec"]["containers"][0]["image"] = staged_source_image
        status["latestCreatedRevisionName"] = staged_source_revision
    if (
        os.environ.get("SOURCE_COMMIT") == sghk_snapshot_candidate
        and os.environ.get("ACCOUNT_SNAPSHOT_ENABLED_INPUT") == "true"
        and os.environ.get("WORKFLOW_TARGET") == "HK"
    ):
        env.extend({"name": key, "value": value} for key, value in hk_retained_history().items())
    return {
        "metadata": {
            "name": os.environ["CLOUD_RUN_SERVICE"],
            "annotations": {"run.googleapis.com/ingress": ingress},
        },
        "spec": {"template": template},
        "status": status,
    }

def serving_revision():
    env = base_env()
    if (
        os.environ.get("SOURCE_COMMIT") == sghk_snapshot_candidate
        and os.environ.get("ACCOUNT_SNAPSHOT_ENABLED_INPUT") == "true"
        and os.environ.get("WORKFLOW_TARGET") == "HK"
    ):
        env.extend({"name": key, "value": value} for key, value in hk_retained_history().items())
    return {
        "metadata": {"name": "serving-rev", "labels": {"commit-sha": serving_sha}},
        "spec": {
            "serviceAccountName": "runtime@example.invalid",
            "containers": [{"env": env, "image": "serving-image"}],
        },
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }

def staged_source_revision_payload():
    service = os.environ["CLOUD_RUN_SERVICE"]
    env = base_env() + [{"name": key, "value": value} for key, value in history.items()]
    image = (
        "asia-east1-docker.pkg.dev/synthetic-project/images/longbridgeplatform/"
        + service
        + "@sha256:9a3260ca8255873309b1c27cd2fd7403e6006a4e9088241e6f89a4e5e65f7a10"
    )
    return {
        "metadata": {"name": staged_source_revision, "labels": {"commit-sha": approved}},
        "spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env, "image": image}]},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }

def staged_revision():
    service = os.environ["CLOUD_RUN_SERVICE"]
    image_repo = (
        "registry.invalid/" + os.environ["GCP_PROJECT_ID"] + "/"
        + os.environ["GCP_ARTIFACT_REGISTRY_REPOSITORY"] + "/longbridgeplatform/" + service
    )
    env = base_env()
    history_keys = (
        "ACCOUNT_HISTORY_RECORDING_ENABLED",
        "ACCOUNT_HISTORY_GCS_PREFIX",
        "ACCOUNT_HISTORY_TARGET_ID",
        "ACCOUNT_HISTORY_EXPECTED_SCOPE",
    )
    if os.environ.get("SOURCE_COMMIT") == http_snapshot_candidate:
        env.extend({"name": key, "value": value} for key, value in history.items())
    if (
        os.environ.get("SOURCE_COMMIT") == sghk_snapshot_candidate
        and os.environ.get("ACCOUNT_SNAPSHOT_ENABLED_INPUT") == "true"
        and os.environ.get("WORKFLOW_TARGET") == "HK"
    ):
        env.extend({"name": key, "value": value} for key, value in hk_retained_history().items())
    if os.environ.get("CONFIG_DRIFT") == "1":
        env.append({"name": "UNRELATED_SETTING", "value": "1"})
    update_path = Path(os.environ["HOME"]) / "updated-env.json"
    updated_env = json.loads(update_path.read_text()) if update_path.exists() else {}
    if os.environ.get("HISTORY_OMITTED") == "1":
        updated_env = {key: value for key, value in updated_env.items() if key not in history_keys}
    elif not updated_env:
        updated_env = {key: os.environ[key] for key in history_keys if os.environ.get(key)}
    for key, value in updated_env.items():
        env.append({"name": key, "value": value})
    ready = [] if os.environ.get("NOT_READY") == "1" else [{"type": "Ready", "status": "True"}]
    return {
        "metadata": {
            "name": service + "-r" + os.environ["GITHUB_RUN_ID"],
            "labels": {"commit-sha": os.environ["SOURCE_COMMIT"]},
        },
        "spec": {
            "serviceAccountName": "runtime@example.invalid",
            "containers": [{"env": env, "image": image_repo + "@" + os.environ["IMAGE_DIGEST"]}],
        },
        "status": {"conditions": ready, "latestReadyRevisionName": "ignored-latest"},
    }

if command == "git" and args == ["rev-parse", "HEAD"]:
    print(os.environ["CHECKOUT_SHA"])
elif command == "git" and args[:4] == ["fetch", "--depth", "1", "origin"] and args[4] in (approved, http_snapshot_candidate, probe_snapshot_candidate, probe_financing_candidate, sghk_snapshot_candidate, hk_probe_diagnostics_candidate):
    pass
elif command == "git" and args[:2] == ["cat-file", "-t"] and args[2] in (approved, http_snapshot_candidate, probe_snapshot_candidate, probe_financing_candidate, sghk_snapshot_candidate, hk_probe_diagnostics_candidate):
    print("commit")
elif command == "git" and args[:1] == ["rev-parse"] and len(args) == 2 and args[1] in (approved + "^{commit}", http_snapshot_candidate + "^{commit}", probe_snapshot_candidate + "^{commit}", probe_financing_candidate + "^{commit}", sghk_snapshot_candidate + "^{commit}", hk_probe_diagnostics_candidate + "^{commit}"):
    print(args[1].split("^", 1)[0])
elif command == "git" and args == ["archive", "HEAD"]:
    print("synthetic tracked source archive")
elif command == "git" and args == ["archive", approved]:
    print("synthetic candidate archive")
elif command == "git" and args == ["archive", http_snapshot_candidate]:
    print("synthetic HTTP snapshot candidate archive")
elif command == "git" and args == ["archive", probe_snapshot_candidate]:
    print("synthetic internal probe snapshot candidate archive")
elif command == "git" and args == ["archive", probe_financing_candidate]:
    print("synthetic internal probe financing candidate archive")
elif command == "git" and args == ["archive", sghk_snapshot_candidate]:
    print("synthetic SG/HK account snapshot candidate archive")
elif command == "git" and args == ["archive", hk_probe_diagnostics_candidate]:
    print("synthetic HK probe diagnostics candidate archive")
elif command == "git" and args[:1] == ["show"] and len(args) == 2 and ":" in args[1]:
    sha, name = args[1].split(":", 1)
    if name not in ("uv.lock", "pyproject.toml", "qsl.toml"):
        raise SystemExit("unexpected git show")
    if sha not in ("a" * 40, serving_sha, approved, http_snapshot_candidate, probe_snapshot_candidate, probe_financing_candidate, sghk_snapshot_candidate, hk_probe_diagnostics_candidate):
        raise SystemExit("admission read an unapproved source lock")
    pin = ("f" * 40) if sha == serving_sha and os.environ.get("BAD_SERVING_LOCK") == "1" else ues
    print(declaration(name, pin), end="")
elif command == "uv" and args[:3] == ["run", "--no-sync", "python"]:
    expected = os.environ["GITHUB_WORKSPACE"] + "/scripts/verify_deployed_runtime_target_admission.py"
    if args[3] != expected or any("record_daily" in part for part in args):
        raise SystemExit("refusing to execute a candidate script")
    os.execv(os.environ["ADMISSION_PYTHON"], [os.environ["ADMISSION_PYTHON"], *args[3:]])
elif command == "uv" and args == ["sync", "--frozen", "--no-dev"]:
    pass
elif command == "python3":
    os.execv(sys.executable, [sys.executable, *args])
elif command == "docker" and args[0] in ("build", "push"):
    if args[0] == "build":
        sys.stdin.buffer.read()
elif command == "gcloud" and args[:3] == ["run", "services", "describe"]:
    if os.environ.get("SERVICE_MISSING") == "1":
        sys.exit(1)
    if "--format=json" in args:
        count_path = Path(os.environ["HOME"]) / "service-json-count"
        count = int(count_path.read_text()) + 1 if count_path.exists() else 1
        count_path.write_text(str(count))
        ingress = "all" if os.environ.get("INGRESS_DRIFT") == "1" and count > 1 else "internal"
        document = service_document(ingress)
        if os.environ.get("TRAFFIC_CHANGED") == "1" and count > 1:
            document["status"]["traffic"] = [{"revisionName": "other-rev", "percent": 100}]
        print(json.dumps(document))
    else:
        fmt = next((arg.partition("=")[2] for arg in args if arg.startswith("--format=")), "")
        shifted = (Path(os.environ["HOME"]) / "traffic-shifted").exists()
        staged_name = os.environ["CLOUD_RUN_SERVICE"] + "-r" + os.environ["GITHUB_RUN_ID"]
        if fmt == "value(status.traffic[0].revisionName)":
            print(staged_name if shifted else "serving-rev")
        elif fmt == "value(status.traffic[0].percent)":
            print("100")
        elif fmt == "value(status.latestReadyRevisionName)":
            print(staged_name if shifted else "serving-rev")
        else:
            print(os.environ["CLOUD_RUN_SERVICE"])
elif command == "gcloud" and args[:3] == ["run", "revisions", "describe"]:
    revision_name = args[3]
    if os.environ.get("UNKNOWN_READBACK") == "1" and revision_name.endswith("-r" + os.environ["GITHUB_RUN_ID"]):
        sys.exit(1)
    if revision_name == staged_source_revision:
        print(json.dumps(staged_source_revision_payload()))
    elif revision_name.endswith("-r" + os.environ["GITHUB_RUN_ID"]):
        print(json.dumps(staged_revision()))
    else:
        print(json.dumps(serving_revision()))
elif command == "gcloud" and args[:2] == ["auth", "configure-docker"]:
    pass
elif command == "gcloud" and args[:4] == ["artifacts", "docker", "images", "describe"]:
    print(os.environ["IMAGE_DIGEST"])
elif command == "gcloud" and args[:3] == ["run", "services", "update"]:
    if os.environ.get("UPDATE_FAIL") == "1":
        sys.exit(1)
    update = next((arg.partition("=")[2] for arg in args if arg.startswith("--update-env-vars=")), "")
    values = {}
    for pair in update.split(","):
        if pair:
            key, separator, value = pair.partition("=")
            if not separator:
                raise SystemExit("invalid synthetic environment update")
            values[key] = value
    (Path(os.environ["HOME"]) / "updated-env.json").write_text(json.dumps(values))
elif command == "gcloud" and args[:3] == ["run", "services", "update-traffic"]:
    (Path(os.environ["HOME"]) / "traffic-shifted").write_text("1")
else:
    raise SystemExit("unexpected command in image-only workflow")
'''
    for command in ("git", "docker", "gcloud", "uv", "python3"):
        path = root / command
        path.write_text(stub)
        path.chmod(0o700)
    base = {
        "PATH": f"{root}:/usr/bin:/bin", "HOME": str(root), "STUB_LOG": str(log),
        "DEPLOYMENT_MODE": "image-only-no-traffic", "WORKFLOW_TARGET": "PAPER",
        "APPROVED_REF": "main", "SOURCE_COMMIT": "a" * 40,
        "GITHUB_REF_NAME": "main", "GITHUB_SHA": "a" * 40, "CHECKOUT_SHA": "a" * 40,
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "123",
        "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform", "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_SHA": "a" * 40,
        "GITHUB_WORKFLOW_REF": "QuantStrategyLab/LongBridgePlatform/.github/workflows/sync-cloud-run-env.yml@refs/heads/main",
        "CLOUD_RUN_SERVICE": "synthetic-paper", "CLOUD_RUN_REGION": "synthetic-region",
        "GCP_PROJECT_ID": "synthetic-project", "GCP_ARTIFACT_REGISTRY_HOSTNAME": "registry.invalid",
        "GCP_ARTIFACT_REGISTRY_REPOSITORY": "synthetic-images", "IMAGE_DIGEST": "sha256:" + "b" * 64,
        "GITHUB_WORKSPACE": str(Path(sys.argv[1]).resolve().parents[2]),
        "ADMISSION_PYTHON": str(Path(sys.argv[1]).resolve().parents[2] / ".venv" / "bin" / "python"),
    }
    candidate = "0b939723c1db3ef59175535998b470cbcd4b8824"
    http_snapshot_candidate = "d8314a61df697cae1dd03a78ddc5c2fc4179ec67"
    probe_snapshot_candidate = "318bf0419ec91002fd0f0dfd1bc80a0664a85e79"
    probe_financing_candidate = "82788aa7c73690d2b87f63540a9102943e9372eb"
    probe_candidates = (probe_snapshot_candidate, probe_financing_candidate)
    sghk_snapshot_candidate = "df3d29d13daeffc7b4af9fec5db0e9ec1f07b60f"
    hk_probe_diagnostics_candidate = "779d8e1c39d161144bc34ae8503ce34d87971b16"
    staged_source_revision = "longbridge-quant-paper-service-r36423178119"
    staged_source_image = (
        "asia-east1-docker.pkg.dev/synthetic-project/images/longbridgeplatform/"
        "synthetic-paper@sha256:9a3260ca8255873309b1c27cd2fd7403e6006a4e9088241e6f89a4e5e65f7a10"
    )
    history = {
        "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
        "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/account_snapshots",
        "ACCOUNT_HISTORY_TARGET_ID": "paper",
        "ACCOUNT_HISTORY_EXPECTED_SCOPE": "PAPER",
    }
    sghk_history = {
        "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
        "ACCOUNT_HISTORY_GCS_PREFIX": "gs://qsl-runtime-logs-shared/longbridge/account_snapshots",
    }

    def execute(**overrides):
        log.write_text("")
        updated_env_path = root / "updated-env.json"
        if updated_env_path.exists():
            updated_env_path.unlink()
        count_path = root / "service-json-count"
        if count_path.exists():
            count_path.unlink()
        traffic_path = root / "traffic-shifted"
        if traffic_path.exists():
            traffic_path.unlink()
        step_env_path = root / "github-env"
        step_env_path.write_text("")
        step_env = {**base, **overrides, "GITHUB_ENV": str(step_env_path)}
        for block in blocks:
            result = subprocess.run(
                ["bash", "-c", block], env=step_env,
                text=True, capture_output=True, cwd=root,
            )
            if result.returncode:
                print(result.stderr, file=sys.stderr)
                return result.returncode, [json.loads(line) for line in log.read_text().splitlines()]
            # GitHub exposes values appended to GITHUB_ENV only to later steps.
            for line in step_env_path.read_text().splitlines():
                name, separator, value = line.partition("=")
                if separator:
                    step_env[name] = value
        return 0, [json.loads(line) for line in log.read_text().splitlines()]

    cases = 0
    for label in ("PAPER", "HK", "SG"):
        code, calls = execute(WORKFLOW_TARGET=label, CLOUD_RUN_SERVICE=f"synthetic-{label.lower()}")
        assert code == 0, (label, calls)
        updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
        assert len(updates) == 1
        service = f"synthetic-{label.lower()}"
        image_repo = f"registry.invalid/synthetic-project/synthetic-images/longbridgeplatform/{service}"
        expected_update = [
            "gcloud", "run", "services", "update", service,
            "--project=synthetic-project", "--region=synthetic-region",
            f"--image={image_repo}@{base['IMAGE_DIGEST']}", "--no-traffic",
            f"--update-labels=commit-sha={base['SOURCE_COMMIT']},github-run-id=123",
        ]
        if label == "PAPER":
            expected_update.extend(["--quiet", "--revision-suffix=r123"])
            assert any(call[:3] == ["uv", "run", "--no-sync"] for call in calls)
        else:
            expected_update.append("--quiet")
            assert not any(call[0] == "uv" for call in calls)
        assert updates[0] == expected_update
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
    for setting in ("true", "false"):
        code, calls = execute(ACCOUNT_SNAPSHOT_ENABLED_INPUT=setting)
        assert code == 0, (setting, code, calls)
        updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
        assert len(updates) == 1
        assert f"--update-env-vars=LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED={setting}" in updates[0]
        assert updates[0][-1] == "--revision-suffix=r123"
        assert any(call[:4] == ["gcloud", "run", "revisions", "describe"] for call in calls)
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
    code, calls = execute(SOURCE_COMMIT=candidate, CHECKOUT_SHA="a" * 40, **history)
    assert code == 0, (code, calls)
    updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
    assert len(updates) == 1
    assert "--no-traffic" in updates[0]
    assert "--update-env-vars=" + ",".join(f"{key}={history[key]}" for key in (
        "ACCOUNT_HISTORY_RECORDING_ENABLED",
        "ACCOUNT_HISTORY_GCS_PREFIX",
        "ACCOUNT_HISTORY_TARGET_ID",
        "ACCOUNT_HISTORY_EXPECTED_SCOPE",
    )) in updates[0]
    assert updates[0][-1] == "--revision-suffix=r123"
    assert f"--update-labels=commit-sha={candidate},github-run-id=123" in updates[0]
    assert ["git", "archive", candidate] in calls
    assert ["git", "archive", "HEAD"] not in calls
    assert ["git", "fetch", "--depth", "1", "origin", candidate] in calls
    assert not any(call[:2] == ["git", "show"] and call[2].startswith("a" * 40) for call in calls)
    assert any(call[:2] == ["git", "show"] and call[2].startswith(candidate + ":") for call in calls)
    assert not any("record_daily" in part for call in calls for part in call)
    assert sum(call[:4] == ["gcloud", "run", "services", "update"] for call in calls) == 1
    cases += 1
    code, calls = execute(SOURCE_COMMIT=http_snapshot_candidate, CHECKOUT_SHA="a" * 40)
    assert code == 0, (code, calls)
    updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
    assert len(updates) == 1
    assert "--update-env-vars=" not in " ".join(updates[0])
    assert updates[0][-1] == "--revision-suffix=r123"
    assert f"--image=registry.invalid/synthetic-project/synthetic-images/longbridgeplatform/synthetic-paper@{base['IMAGE_DIGEST']}" in updates[0]
    assert f"--update-labels=commit-sha={http_snapshot_candidate},github-run-id=123" in updates[0]
    assert ["docker", "build", "--pull", "-t",
            f"registry.invalid/synthetic-project/synthetic-images/longbridgeplatform/synthetic-paper:{http_snapshot_candidate}-123", "-"] in calls
    assert ["git", "fetch", "--depth", "1", "origin", http_snapshot_candidate] in calls
    assert ["git", "archive", http_snapshot_candidate] in calls
    assert ["git", "archive", "HEAD"] not in calls
    assert ["gcloud", "run", "revisions", "describe", staged_source_revision,
            "--project=synthetic-project", "--region=synthetic-region", "--format=json"] in calls
    assert not any(call[:4] == ["gcloud", "run", "services", "update"] and "ACCOUNT_HISTORY_" in " ".join(call) for call in calls)
    cases += 1
    code, calls = execute(SOURCE_COMMIT=http_snapshot_candidate, CHECKOUT_SHA="a" * 40, ACCOUNT_SNAPSHOT_ENABLED_INPUT="true")
    assert code == 0, (code, calls)
    updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
    assert len(updates) == 1
    update_args = [arg for arg in updates[0] if arg.startswith("--update-env-vars=")]
    assert update_args == ["--update-env-vars=LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED=true"]
    assert ["git", "archive", http_snapshot_candidate] in calls
    cases += 1
    for overrides in (
        {"SOURCE_COMMIT": http_snapshot_candidate, "WORKFLOW_TARGET": "HK"},
        {"SOURCE_COMMIT": http_snapshot_candidate, **history},
    ):
        code, calls = execute(**overrides)
        assert code != 0 and calls == [], overrides
        cases += 1
    for probe_candidate in probe_candidates:
        code, calls = execute(SOURCE_COMMIT=probe_candidate, CHECKOUT_SHA="a" * 40)
        assert code == 0, (probe_candidate, code, calls)
        updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
        assert len(updates) == 1
        assert "--no-traffic" in updates[0]
        assert "--update-env-vars=" not in " ".join(updates[0])
        assert updates[0][-1] == "--revision-suffix=r123"
        assert f"--image=registry.invalid/synthetic-project/synthetic-images/longbridgeplatform/synthetic-paper@{base['IMAGE_DIGEST']}" in updates[0]
        assert f"--update-labels=commit-sha={probe_candidate},github-run-id=123" in updates[0]
        assert ["docker", "build", "--pull", "-t",
                f"registry.invalid/synthetic-project/synthetic-images/longbridgeplatform/synthetic-paper:{probe_candidate}-123", "-"] in calls
        assert ["git", "fetch", "--depth", "1", "origin", probe_candidate] in calls
        assert ["git", "archive", probe_candidate] in calls
        assert ["git", "archive", "HEAD"] not in calls
        assert not any(call[:4] == ["gcloud", "run", "revisions", "describe"]
                       and call[4] == staged_source_revision for call in calls)
        assert any(call[:3] == ["uv", "run", "--no-sync"] for call in calls)
        cases += 1
        for overrides in (
            {"SOURCE_COMMIT": probe_candidate, "WORKFLOW_TARGET": "HK"},
            {"SOURCE_COMMIT": probe_candidate, "WORKFLOW_TARGET": "SG"},
            {"SOURCE_COMMIT": "c" * 40},
            {"SOURCE_COMMIT": probe_candidate, **history},
            {"SOURCE_COMMIT": probe_candidate, "ACCOUNT_SNAPSHOT_ENABLED_INPUT": "true"},
            {"SOURCE_COMMIT": probe_candidate, "ACCOUNT_SNAPSHOT_ENABLED_INPUT": "false"},
        ):
            code, calls = execute(**overrides)
            assert code != 0 and calls == [], overrides
            cases += 1
    for label, region in (("HK", "asia-east2"), ("SG", "asia-southeast1")):
        target_history = {
            **sghk_history,
            "ACCOUNT_HISTORY_TARGET_ID": label.lower(),
            "ACCOUNT_HISTORY_EXPECTED_SCOPE": label,
        }
        service = f"longbridge-quant-{label.lower()}-service"
        code, calls = execute(
            SOURCE_COMMIT=sghk_snapshot_candidate,
            CHECKOUT_SHA="a" * 40,
            WORKFLOW_TARGET=label,
            CLOUD_RUN_SERVICE=service,
            CLOUD_RUN_REGION=region,
            GCP_PROJECT_ID="longbridgequant",
            **target_history,
        )
        assert code == 0, (label, code, calls)
        updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
        image_repo = f"registry.invalid/longbridgequant/synthetic-images/longbridgeplatform/{service}"
        assert len(updates) == 1
        assert "--no-traffic" in updates[0]
        assert updates[0][-1] == "--revision-suffix=r123"
        assert f"--update-labels=commit-sha={sghk_snapshot_candidate},github-run-id=123" in updates[0]
        assert f"--image={image_repo}@{base['IMAGE_DIGEST']}" in updates[0]
        assert "--update-env-vars=" + ",".join(f"{key}={value}" for key, value in target_history.items()) in updates[0]
        assert ["git", "fetch", "--depth", "1", "origin", sghk_snapshot_candidate] in calls
        assert ["git", "archive", sghk_snapshot_candidate] in calls
        assert ["git", "archive", "HEAD"] not in calls
        assert any(call[:2] == ["git", "show"] and call[2].startswith(sghk_snapshot_candidate + ":") for call in calls)
        assert any(call[:3] == ["uv", "run", "--no-sync"] for call in calls)
        assert not any("ACCOUNT_SNAPSHOT_ENABLED_INPUT" in part for call in calls for part in call)
        cases += 1
    for overrides in (
        {
            "SOURCE_COMMIT": sghk_snapshot_candidate,
            "WORKFLOW_TARGET": "HK",
            "CLOUD_RUN_SERVICE": "longbridge-quant-hk-service",
            "CLOUD_RUN_REGION": "asia-east2",
            "GCP_PROJECT_ID": "longbridgequant",
            **{**sghk_history, "ACCOUNT_HISTORY_TARGET_ID": "sg", "ACCOUNT_HISTORY_EXPECTED_SCOPE": "HK"},
        },
        {
            "SOURCE_COMMIT": sghk_snapshot_candidate,
            "WORKFLOW_TARGET": "HK",
            "CLOUD_RUN_SERVICE": "longbridge-quant-hk-service",
            "CLOUD_RUN_REGION": "asia-east2",
            "GCP_PROJECT_ID": "longbridgequant",
            **{**sghk_history, "ACCOUNT_HISTORY_TARGET_ID": "hk", "ACCOUNT_HISTORY_EXPECTED_SCOPE": "SG"},
        },
        {
            "SOURCE_COMMIT": sghk_snapshot_candidate,
            "WORKFLOW_TARGET": "HK",
            "CLOUD_RUN_SERVICE": "longbridge-quant-hk-service",
            "CLOUD_RUN_REGION": "asia-east2",
            "GCP_PROJECT_ID": "longbridgequant",
            "ACCOUNT_SNAPSHOT_ENABLED_INPUT": "false",
            **{**sghk_history, "ACCOUNT_HISTORY_TARGET_ID": "hk", "ACCOUNT_HISTORY_EXPECTED_SCOPE": "HK"},
        },
        {
            "SOURCE_COMMIT": sghk_snapshot_candidate,
            "WORKFLOW_TARGET": "HK",
            "CLOUD_RUN_SERVICE": "longbridge-quant-hk-service",
            "CLOUD_RUN_REGION": "asia-east2",
            "GCP_PROJECT_ID": "longbridgequant",
            "ACCOUNT_SNAPSHOT_ENABLED_INPUT": "true",
            **{**sghk_history, "ACCOUNT_HISTORY_TARGET_ID": "hk", "ACCOUNT_HISTORY_EXPECTED_SCOPE": "HK"},
        },
        {
            "SOURCE_COMMIT": sghk_snapshot_candidate,
            "WORKFLOW_TARGET": "SG",
            "CLOUD_RUN_SERVICE": "longbridge-quant-sg-service",
            "CLOUD_RUN_REGION": "asia-southeast1",
            "GCP_PROJECT_ID": "longbridgequant",
            "ACCOUNT_SNAPSHOT_ENABLED_INPUT": "true",
        },
    ):
        code, calls = execute(**overrides)
        assert code != 0 and calls == [], overrides
        cases += 1
    code, calls = execute(
        SOURCE_COMMIT=sghk_snapshot_candidate,
        CHECKOUT_SHA="a" * 40,
        WORKFLOW_TARGET="HK",
        CLOUD_RUN_SERVICE="longbridge-quant-hk-service",
        CLOUD_RUN_REGION="asia-east2",
        GCP_PROJECT_ID="longbridgequant",
        ACCOUNT_SNAPSHOT_ENABLED_INPUT="true",
    )
    assert code == 0, (code, calls)
    updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
    assert len(updates) == 1
    assert "--update-env-vars=LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED=true" in updates[0]
    assert updates[0][-1] == "--revision-suffix=r123"
    traffic_calls = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update-traffic"]]
    assert len(traffic_calls) == 1
    assert "--to-revisions=longbridge-quant-hk-service-r123=100" in traffic_calls[0]
    cases += 1
    code, calls = execute(
        SOURCE_COMMIT=hk_probe_diagnostics_candidate,
        CHECKOUT_SHA="a" * 40,
        WORKFLOW_TARGET="HK",
        CLOUD_RUN_SERVICE="longbridge-quant-hk-service",
        CLOUD_RUN_REGION="asia-east2",
        GCP_PROJECT_ID="longbridgequant",
    )
    assert code == 0, (code, calls)
    updates = [call for call in calls if call[:4] == ["gcloud", "run", "services", "update"]]
    assert len(updates) == 1
    image_repo = "registry.invalid/longbridgequant/synthetic-images/longbridgeplatform/longbridge-quant-hk-service"
    assert "--no-traffic" in updates[0]
    assert "--update-env-vars=" not in " ".join(updates[0])
    assert updates[0][-1] == "--revision-suffix=r123"
    assert f"--image={image_repo}@{base['IMAGE_DIGEST']}" in updates[0]
    assert f"--update-labels=commit-sha={hk_probe_diagnostics_candidate},github-run-id=123" in updates[0]
    assert ["git", "fetch", "--depth", "1", "origin", hk_probe_diagnostics_candidate] in calls
    assert ["git", "archive", hk_probe_diagnostics_candidate] in calls
    assert any(call[:3] == ["uv", "run", "--no-sync"] for call in calls)
    assert sum(call[:4] == ["gcloud", "run", "services", "update"] for call in calls) == 1
    cases += 1
    for overrides in (
        {"SOURCE_COMMIT": hk_probe_diagnostics_candidate, "WORKFLOW_TARGET": "SG"},
        {"SOURCE_COMMIT": hk_probe_diagnostics_candidate, "ACCOUNT_HISTORY_RECORDING_ENABLED": "true"},
        {"SOURCE_COMMIT": hk_probe_diagnostics_candidate, "ACCOUNT_SNAPSHOT_ENABLED_INPUT": "false"},
        {"SOURCE_COMMIT": "c" * 40, "WORKFLOW_TARGET": "HK"},
    ):
        code, calls = execute(**overrides)
        assert code != 0 and calls == [], overrides
        cases += 1
    code, calls = execute(
        SOURCE_COMMIT=hk_probe_diagnostics_candidate,
        WORKFLOW_TARGET="HK",
        CLOUD_RUN_SERVICE="longbridge-quant-hk-service",
        CLOUD_RUN_REGION="asia-east2",
        GCP_PROJECT_ID="longbridgequant",
        TARGET_RUNTIME_ENABLED="true",
    )
    assert code != 0
    assert not any(call[0] == "docker" and call[1] == "build" for call in calls)
    assert not any(call[:4] == ["gcloud", "run", "services", "update"] for call in calls)
    cases += 1
    for overrides in (
        {
            "GITHUB_REF": "refs/heads/codex/natural-cycle-history-20260928",
            "GITHUB_REF_NAME": "codex/natural-cycle-history-20260928",
            "APPROVED_REF": "codex/natural-cycle-history-20260928",
            "SOURCE_COMMIT": candidate,
            "GITHUB_SHA": candidate,
            "GITHUB_WORKFLOW_SHA": candidate,
            "CHECKOUT_SHA": candidate,
            "GITHUB_WORKFLOW_REF": "synthetic/repository/.github/workflows/sync-cloud-run-env.yml@refs/heads/codex/natural-cycle-history-20260928",
            **history,
        },
        {"GITHUB_WORKFLOW_REF": "other/repo/.github/workflows/sync-cloud-run-env.yml@refs/heads/main"},
        {
            "GITHUB_REPOSITORY": "other/LongBridgePlatform",
            "GITHUB_WORKFLOW_REF": "other/LongBridgePlatform/.github/workflows/sync-cloud-run-env.yml@refs/heads/main",
        },
        {"SOURCE_COMMIT": "a" * 40, **history},
        {"SOURCE_COMMIT": "d" * 40, **history},
        {"WORKFLOW_TARGET": "HK", "SOURCE_COMMIT": candidate, "CLOUD_RUN_SERVICE": "synthetic-hk", **history},
        {"SOURCE_COMMIT": candidate},
        {"SOURCE_COMMIT": candidate, "ACCOUNT_HISTORY_RECORDING_ENABLED": "true"},
        {"SOURCE_COMMIT": candidate, **{**history, "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/account_snapshots,evil"}},
    ):
        code, calls = execute(**overrides)
        assert code != 0 and calls == [], overrides
        cases += 1
    for overrides in (
        {"SOURCE_COMMIT": candidate, "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/other", **{key: history[key] for key in history if key != "ACCOUNT_HISTORY_GCS_PREFIX"}},
        {"SOURCE_COMMIT": candidate, "BAD_SERVING_LOCK": "1", **history},
        {"SOURCE_COMMIT": candidate, "TARGET_ACCOUNT_SCOPE": "SG", **history},
    ):
        code, calls = execute(**overrides)
        assert code != 0, overrides
        assert not any(call[:2] == ["docker", "build"] for call in calls)
        assert not any(call[:4] == ["gcloud", "run", "services", "update"] for call in calls)
        cases += 1
    for overrides in (
        {"SOURCE_COMMIT": candidate, "INGRESS_DRIFT": "1", **history},
        {"SOURCE_COMMIT": candidate, "CONFIG_DRIFT": "1", **history},
        {"SOURCE_COMMIT": candidate, "HISTORY_OMITTED": "1", **history},
        {"SOURCE_COMMIT": candidate, "NOT_READY": "1", **history},
        {"SOURCE_COMMIT": candidate, "UNKNOWN_READBACK": "1", **history},
        {"SOURCE_COMMIT": candidate, "TRAFFIC_CHANGED": "1", **history},
    ):
        code, calls = execute(**overrides)
        assert code != 0, overrides
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
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", image="synthetic:test", archive_ref="HEAD")
    for index, build_command in enumerate(build_commands, 1):
        result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + build_command], cwd=checkout, env=env, capture_output=True)
        if result.returncode:
            raise SystemExit(f"FAIL: build {index} admits a workspace context instead of tracked source")
print("PASS: both tracked-source builds exclude generated authentication files")
PY

# The history image archives the approved candidate commit, not the main checkout.
python3 - "$workflow_file" <<'PY'
import os
from pathlib import Path
import subprocess
import sys
import tempfile

workflow = Path(sys.argv[1]).read_text().splitlines()
build_commands = [line.strip() for line in workflow if "docker build --pull" in line and "archive_ref" in line]
assert len(build_commands) == 1
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    checkout = root / "checkout"
    checkout.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    def git(*args):
        return subprocess.run(["git", *args], cwd=checkout, env=env, check=True, capture_output=True, text=True)
    git("init", "-q")
    (checkout / "Dockerfile").write_text("FROM scratch\n")
    (checkout / "only-main.txt").write_text("main checkout\n")
    git("add", "Dockerfile", "only-main.txt")
    git("-c", "user.name=synthetic", "-c", "user.email=synthetic@example.invalid", "-c", "commit.gpgSign=false", "commit", "-qm", "main")
    (checkout / "only-main.txt").unlink()
    (checkout / "only-candidate.txt").write_text("candidate source\n")
    git("add", "-A")
    git("-c", "user.name=synthetic", "-c", "user.email=synthetic@example.invalid", "-c", "commit.gpgSign=false", "commit", "-qm", "candidate")
    candidate = git("rev-parse", "HEAD").stdout.strip()
    (checkout / "gha-creds-synthetic.json").write_text('{"synthetic":true}\n')
    bin_dir = root / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(f"#!{sys.executable}\n" + r'''import sys, tarfile
names = set()
payload = b""
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
    for member in archive:
        names.add(member.name)
        if member.name == "only-candidate.txt":
            payload = archive.extractfile(member).read()
if names != {"Dockerfile", "only-candidate.txt"} or payload != b"candidate source\n":
    raise SystemExit("archive was not the approved candidate commit")
''')
    docker.chmod(0o700)
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", image="synthetic:test", archive_ref=candidate)
    result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + build_commands[0]], cwd=checkout, env=env, capture_output=True)
    if result.returncode:
        raise SystemExit("FAIL: image archive did not use the candidate commit")
print("PASS: history image archive is the candidate commit")
PY
