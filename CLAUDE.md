# LongBridgePlatform

LongBridge execution platform for us_equity, hk_equity, and quant_combo strategies. Paper/HK/SG Cloud Run services with region config.

## Key Files

- `main.py` — Flask app with /run, /dry-run, /probe, /health, /monitor-dispatch
- `strategy_registry.py` — Imports US + HK + Combo catalogs, 3-way merge
- `runtime_config_support.py` — PlatformRuntimeSettings with region/market detection
- `runtime_execution_policy.py` — Fractional buy, DCA compat mode
- `application/longbridge_execution.py` — Order submission via LongPort API
- `application/longbridge_portfolio.py` — Portfolio snapshot via LongPort API

## Services

| Service | Region | Market | Scheduler TZ |
|---|---|---|---|
| longbridge-quant-sg-service | asia-southeast1 | US | America/New_York |
| longbridge-quant-hk-service | asia-east2 | HK | Asia/Hong_Kong |
| longbridge-quant-paper-service | asia-east2 | US | America/New_York |

## Config

- Single source: `RUNTIME_TARGET_JSON`
- Market auto-detection: ACCOUNT_REGION → LONGBRIDGE_MARKET → calendar + suffix + currency
- HK market: .HK suffix, HKD currency, XHKG calendar
- US market: .US suffix, USD currency, NASDAQ calendar
