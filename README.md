# LongPort Semiconductor Rotation & Income

Quant system on LongPort OpenAPI and Google Cloud Run.

**Layers**

- **Trading:** SOXL / SOXX / BOXX
- **Income:** QQQI / SPYI

## Behaviour

**Trading layer**

- SOXL 150-day MA for trend.
- SOXL > MA150 → hold SOXL; SOXL ≤ MA150 → hold SOXX.
- Rest of trading capital in BOXX.

**Income layer**

- Starts when total equity ≥ 150,000 USD.
- Target income allocation cap 15%.
- New income allocation: QQQI 70% / SPYI 30%.
- Income positions are buy-only (no automatic reduction).

**Execution**

- Only SOXL, SOXX, BOXX, QQQI, SPYI are used.
- Cash from account USD `available_cash`; no margin.
- `estimate_max_purchase_quantity` used before buys.
- Telegram alerts for submit, fill, reject, and errors.

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | Yes | Bot token for alerts |
| `TELEGRAM_CHAT_ID` | Yes | Chat or user ID to receive messages |
| `LONGPORT_APP_KEY` | Yes | LongPort OpenAPI app key (for token refresh) |
| `LONGPORT_APP_SECRET` | Yes | LongPort OpenAPI app secret (for token refresh) |
| `LONGPORT_SECRET_NAME` | No | Secret Manager secret name for LongPort token (default: `longport_token`) |
| `ACCOUNT_PREFIX` | No | Alert/log prefix for account/environment (default: `DEFAULT`) |
| `SERVICE_NAME` | No | Alert/log prefix for service identity (default: `longbridge-quant`) |
| `GOOGLE_CLOUD_PROJECT` | No | GCP project ID (defaults to ADC project when unset) |

Secret Manager must contain the secret named by `LONGPORT_SECRET_NAME` (default: `longport_token`), where the **latest version = active access token**. The app refreshes it when expiry is within 30 days.

### Multi-deployment isolation (HK/SG, etc.)

Deploy the same codebase as multiple Cloud Run services (e.g. `HK` and `SG`) by setting different values per service:

- `LONGPORT_SECRET_NAME`: point to different secrets (e.g. `longport_token_hk`, `longport_token_sg`)
- `ACCOUNT_PREFIX`: e.g. `HK`, `SG` (all Telegram/log alerts will include `[ACCOUNT_PREFIX/SERVICE_NAME]`)
- `SERVICE_NAME`: e.g. `longbridge-quant-hk`, `longbridge-quant-sg`

## Quick deploy

1. Enable **Cloud Run** and **Secret Manager API** in GCP.
2. Create secret `longport_token` (or your custom `LONGPORT_SECRET_NAME`) in Secret Manager and add your LongPort access token as the first version.
3. Set the required env vars above on the Cloud Run service.
4. Deploy the app to Cloud Run (e.g. `gcloud run deploy` from repo root with Dockerfile or buildpack).
5. Create a Cloud Scheduler job that POSTs to the Cloud Run URL on a schedule (e.g. `45 15 * * 1-5` for ~15 min before US market close on weekdays).

IAM: the Cloud Run service account needs **Secret Manager Admin** (or Secret Accessor for `longport_token`) and **Logs Writer**. Build/deploy typically uses a separate account with Artifact Registry Writer, Cloud Run Admin, Service Account User.

## Parameters (main.py)

- `TREND_MA_WINDOW`
- `SMALL_ACCOUNT_DEPLOY_RATIO` / `MID_ACCOUNT_DEPLOY_RATIO` / `LARGE_ACCOUNT_DEPLOY_RATIO`
- `TRADE_LAYER_DECAY_COEFF`
- `INCOME_LAYER_START_USD` / `INCOME_LAYER_MAX_RATIO`
- `INCOME_LAYER_QQQI_WEIGHT` / `INCOME_LAYER_SPYI_WEIGHT`

## Deployment (detailed)

1. **GCP:** Enable Cloud Run and Secret Manager API.
2. **Secret Manager:** Create secret `longport_token`; put the LongPort access token as the latest version. The app refreshes it when expiry is within 30 days.
3. **Env:** Configure the [environment variables](#environment-variables) on the Cloud Run service.
4. **IAM:** Service account for Cloud Run: Secret Manager access (e.g. Secret Accessor for `longport_token`), Logs Writer. For CI/build: Artifact Registry Writer, Cloud Run Admin, Service Account User.
5. **Schedule:** Trigger the service via Cloud Scheduler (e.g. POST to the service URL). Example cron: `45 15 * * 1-5` (weekdays, ~15 min before US close).
