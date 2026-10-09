# LongPort Access Token durability (paper / HK / SG)

## Recommendation (one line)

**Prefer path A now:** keep Legacy App Key/Secret/Access Token, fix and schedule **pre-expiry** refresh into Secret Manager (paper → SG → HK last); defer OAuth2 (path B) until A is durable and SDK 4.x is planned.

## Why this exists

LongPort Legacy Access Tokens expire (docs: ~90 days / three months). After expiry, `GET /v1/token/refresh` returns **401003** and **cannot** revive the token; recovery is Developer Portal reset + Secret Manager rotate (HK was healed this way on 2026-10-09). Probe/heartbeat and paused strategy must not be the only auth paths.

Authoritative vendor docs:

- [Getting Started (Legacy refresh + OAuth)](https://open.longportapp.com/docs/getting-started.md)
- [Refresh Access Token](https://open.longportapp.com/docs/refresh-token-api.md)
- [Error codes (401003)](https://open.longportapp.com/docs/error-codes.md)

## 1. How each account stores credentials today

Public inventory: [`config/runtime_targets.manifest.json`](../config/runtime_targets.manifest.json).

| Target | GitHub Environment | Cloud Run service | SM token | SM app key | SM app secret |
| --- | --- | --- | --- | --- | --- |
| paper | `longbridge-paper` | `longbridge-quant-paper-service` | `longport_token_paper` | `longport-app-key-paper` | `longport-app-secret-paper` |
| hk | `longbridge-hk` | `longbridge-quant-hk-service` | `longport_token_hk` | `longport-app-key-hk` | `longport-app-secret-hk` |
| sg | `longbridge-sg` | `longbridge-quant-sg-service` | `longport_token_sg` | `longport-app-key-sg` | `longport-app-secret-sg` |

Runtime wiring (Actions deploy + Cloud Run):

- Env vars `LONGPORT_SECRET_NAME` / `LONGPORT_APP_KEY_SECRET_NAME` / `LONGPORT_APP_SECRET_SECRET_NAME` point at the SM **names** above (per Environment / deploy inputs).
- **App Key / App Secret** are mounted into the revision as Cloud Run secret env refs (`LONGPORT_APP_KEY` / `LONGPORT_APP_SECRET` → `:latest`).
- **Access Token** is **not** relied on as a static revision env value for broker auth: bootstrap reads SM `projects/.../secrets/<LONGPORT_SECRET_NAME>/versions/latest` via `quant_platform_kit.longbridge.auth.fetch_token_from_secret` (pinned QPK in `pyproject.toml`).
- Manual portal-reset write path: [`.github/workflows/rotate-longport-secrets.yml`](../.github/workflows/rotate-longport-secrets.yml) (temporary repo secrets → SM versions). Deploy SA needs `roles/secretmanager.secretVersionAdder` on those secrets (HK was granted separately).

Never log or commit secret **values**; this runbook only names resources.

## 2. Pre-expiry refresh: implemented, but historically ineffective

### What exists

- QPK: `quant_platform_kit.longbridge.auth.refresh_token_if_needed` calls Legacy `GET https://openapi.longportapp.com/v1/token/refresh` with HMAC headers, then writes the new token to SM when `code == 0`.
- Platform: `TOKEN_REFRESH_THRESHOLD_DAYS = 30` in `main.py`; `LongBridgeRuntimeBootstrap.build_contexts()` invokes refresh before building quote/trade contexts.
- Read-only paths **intentionally skip** refresh: `build_read_only_contexts`, account-snapshot metadata read, paper command consumer, and HK/SG history probe branch that uses `build_account_snapshot_broker_contexts()`.

### Why tokens still expired

1. **Refresh only rides strategy bootstrap.** When `RUNTIME_TARGET_ENABLED=false` (or strategy cycles are otherwise not running), the refresh path never runs. Daily probe / heartbeat still need a valid token but use read-only context builders.
2. **Past expiry is terminal for refresh.** Vendor 401003 + docs: refresh must happen **before** expiry; portal reset is the only recovery afterward (matches HK incident).
3. **Soft-fail hides pre-expiry API errors.** If refresh returns non-zero **and** JWT `exp` is still in the future, QPK returns the old token instead of failing closed—so a broken refresh can sit silent until hard expiry.
4. **Missing `expired_at` query param.** Vendor API marks `expired_at` required; official SDK `Config.refresh_access_token(expired_at=...)` sends it (default ~90 days). Current QPK call signs/sends **empty** params. That is a likely contributor to silent refresh failure (covered by soft-fail above). Fix belongs in QPK + tests, then platform pin bump.
5. **IAM for writers.** Runtime/Action writers need SM add-version permission; without it, even a successful LongPort refresh cannot persist (rotate workflow surfaced this for HK).

## 3. OAuth2 availability (path B)

LongPort OpenAPI now documents **OAuth 2.0 as recommended** for new integrations: register client (`/oauth2/register`), browser `authorization_code` + `refresh_token`, SDK `Config.from_oauth` / `OAuthBuilder`, token file under `~/.longport/openapi/tokens/<client_id>`, SDK auto-refresh. Legacy App Key mode remains compatible; Legacy `refresh_access_token` is **not** supported in OAuth mode.

For Cloud Run paper/HK/SG this is **not** a small drop-in:

- Needs interactive (or carefully automated) consent **per account**.
- Must replace HMAC App Key/Secret signing with Bearer OAuth tokens end-to-end in QPK `build_contexts` and any custom signed HTTP.
- Must store OAuth refresh material in SM (not home-directory files on ephemeral containers).
- Platform currently pins `longport==3.0.23`; OAuth-first SDK surface is on newer 4.x lines—bump is a separate risk.

Treat OAuth as a **follow-on migration** after path A is green, not the emergency durability fix.

## 4. Recommended path and blast radius

| Phase | Action | Targets | Blast radius |
| --- | --- | --- | --- |
| A0 (this PR) | Runbook + read-only JWT expiry inspector (no refresh, no SM write) | paper / hk / sg | Docs + optional manual inspect only |
| A1 | QPK: send `expired_at`; fail closed on pre-expiry refresh failure; keep SM write via store_rw | kit first | Shared kit; pin bump in platform after |
| A2 | Dedicated refresh job (Actions `workflow_dispatch` → then schedule) calling refresh **before** expiry, independent of `RUNTIME_TARGET_ENABLED` | **paper first**, then **sg**, **hk last** | Mutates token SM version; invalidates previous Access Token on success |
| A3 | Alert when days-to-exp &lt; threshold (reuse inspector) | all | Notify only |
| B (later) | OAuth spike on paper only, SDK 4.x, SM-backed refresh token | paper → sg → hk | Large: auth model + SDK + deploy |

**HK last** because it was just healed via portal reset + SM rotate + QRS binding; do not experiment with token invalidation there until paper/SG prove A1+A2.

Constraints this plan respects: no `independent_get` / ingress changes, no production strategy enablement flips, no secret values in docs/logs, no strategy logic changes.

## 5. Exact next PR scope (do not fold into this PR)

1. **QuantPlatformKit** – `longbridge/auth.py` + `tests/test_longbridge_auth.py`:
   - Pass ISO-8601 `expired_at` on `/v1/token/refresh` (and include it in the signed `params` string).
   - On non-zero refresh while still pre-expiry: raise (or structured error) instead of returning the old token.
   - Optionally prefer official SDK `Config.refresh_access_token` once pin allows; keep SM persistence in kit.
2. **LongBridgePlatform** – after QPK pin:
   - `workflow_dispatch` refresh job per target (reuse rotate’s target matrix / WIF deploy SA); schedule only after paper dry-run succeeds.
   - Confirm `secretVersionAdder` (or store_rw equivalent) on paper + sg (+ hk when ready).
   - Keep probe/snapshot read-only (no refresh on those paths).
3. **Ops** – calendar reminder / alert from expiry inspector until A2 is scheduled.
4. **Not in next PR:** OAuth client registration, `longport` 4.x bump, enabling `RUNTIME_TARGET_ENABLED`, ingress, or strategy changes.

## Emergency recovery (already proven on HK)

1. Developer Portal: reset Access Token (and confirm App Key/Secret if needed) for that account.
2. Set temporary repo secrets `ROTATE_LONGPORT_TOKEN` / `ROTATE_LONGPORT_APP_KEY` / `ROTATE_LONGPORT_APP_SECRET`.
3. Run **Rotate LongPort Secrets** with `target=paper|hk|sg`.
4. Delete temporary repo secrets.
5. Re-run heartbeat / probe; if account-history source binding changes, update QRS expected binding (HK required this after rotate).

## Related code pointers

- Platform bootstrap: `application/runtime_bootstrap_adapters.py`
- Composer read-only vs refresh: `application/runtime_composer.py`
- Probe history branch (no refresh): `main.py` `run_probe`
- Rotate workflow: `.github/workflows/rotate-longport-secrets.yml`
- Read-only expiry inspect: `.github/workflows/inspect-longport-token-expiry.yml` + `scripts/inspect_longport_token_expiry.py`
