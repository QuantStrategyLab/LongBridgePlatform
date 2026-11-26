"""LongBridge adapter for QPK account-level NEW_RISK gate (fail-closed)."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from quant_platform_kit.common.broker_new_risk_gate_helpers import resolve_fuse_readback_inputs
from quant_platform_kit.risk.account_new_risk_gate import (
    AccountNewRiskGateError,
    InjectedReconciliationSnapshot,
    NewRiskAdmissionResult,
    NewRiskDisposition,
    evaluate_new_risk_admission,
)
from quant_platform_kit.risk.contracts import RuntimeRiskLimits
from quant_platform_kit.risk.cycle_new_risk_health import (
    CycleNewRiskHealthEvidence,
    apply_cycle_new_risk_health_axes,
)
from quant_platform_kit.risk.production_drift_new_risk import (
    resolve_production_drift_status_from_store,
)

ACCOUNT_NEW_RISK_GATE_ENV = "ACCOUNT_NEW_RISK_GATE"
_FUSE_CONTROL_ENV_KEYS = ("FUSE_READBACK_MODE", "FUSE_TRADE_DATE", "FUSE_OBSERVE_UNTIL")
_MAX_DAILY_LOSS_ENV_KEYS = ("LONGBRIDGE_MAX_DAILY_LOSS_USD", "MAX_DAILY_LOSS_USD")

_DEFAULT_STRATEGY_PROFILE = "soxl_soxx_trend_income"
_DEFAULT_DOMAIN = "us_equity"

# Minimal carrier for gate-only daily-loss axis; not a production RRL binding.
_DAILY_LOSS_LIMIT_CARRIER_SYMBOL = "SPY"

@dataclass(frozen=True)
class _CycleRiskState:
    snapshot: InjectedReconciliationSnapshot | None = None
    market_timezone: str | None = None
    controls_from_env: bool = False
    max_daily_loss_usd: float | None = None
    max_daily_loss_bound: bool = False


_EMPTY_CYCLE_RISK_STATE = _CycleRiskState()
_CYCLE_RISK_STATE: ContextVar[_CycleRiskState | None] = ContextVar(
    "longbridge_cycle_risk_state",
    default=None,
)


def _current_cycle_risk_state() -> _CycleRiskState:
    return _CYCLE_RISK_STATE.get() or _EMPTY_CYCLE_RISK_STATE


_LAST_ATTENTION_CONFIRMED: ContextVar[bool] = ContextVar(
    "longbridge_last_attention_confirmed",
    default=False,
)

_FUSE_TELEMETRY_KEYS = (
    "fuse_readback_effective_mode",
    "fuse_readback_would_block",
    "fuse_readback_reason_codes",
    "fuse_readback_observe_window_expired",
)

_MARKET_DEFAULT_TIMEZONES = {
    "US": "America/New_York",
    "US_EQUITY": "America/New_York",
    "USA": "America/New_York",
    "NYSE": "America/New_York",
    "NASDAQ": "America/New_York",
    "HK": "Asia/Hong_Kong",
    "HK_EQUITY": "Asia/Hong_Kong",
    "HONG_KONG": "Asia/Hong_Kong",
    "HONGKONG": "Asia/Hong_Kong",
}


def is_account_new_risk_gate_enabled() -> bool:
    """Production default on; set ACCOUNT_NEW_RISK_GATE=0 only for tests."""
    return str(os.environ.get(ACCOUNT_NEW_RISK_GATE_ENV, "") or "").strip() != "0"


def _market_to_timezone(market: object) -> str | None:
    normalized = str(market or "").strip().upper()
    if not normalized:
        return None
    return _MARKET_DEFAULT_TIMEZONES.get(normalized)


def _timezone_from_mapping(source: Mapping[str, Any] | None) -> str | None:
    if not isinstance(source, Mapping):
        return None
    explicit_tz = str(source.get("market_timezone") or "").strip()
    if explicit_tz:
        return explicit_tz
    return _market_to_timezone(source.get("market"))


def _resolve_runtime_target_timezone_or_market() -> tuple[str | None, str]:
    raw_target = str(
        os.environ.get("QSL_RUNTIME_TARGET_JSON") or os.environ.get("RUNTIME_TARGET_JSON") or ""
    ).strip()
    if not raw_target:
        return None, ""
    try:
        payload = json.loads(raw_target)
    except ValueError:
        return None, ""
    if not isinstance(payload, Mapping):
        return None, ""
    return str(payload.get("market_timezone") or "").strip() or None, str(
        payload.get("market") or ""
    ).strip()


def _resolve_fuse_market_timezone(
    portfolio: Mapping[str, Any] | None = None,
    *,
    execution: Mapping[str, Any] | None = None,
) -> str:
    """Resolve authoritative market timezone; runtime config beats snapshot market."""
    from longbridge_platform.runtime_config_support import (
        _market_defaults,
        infer_account_region,
        infer_market,
    )

    for env_key in ("LONGBRIDGE_MARKET_TIMEZONE", "MARKET_TIMEZONE"):
        env_tz = str(os.environ.get(env_key) or "").strip()
        if env_tz:
            return env_tz
    target_tz, target_market = _resolve_runtime_target_timezone_or_market()
    if target_tz:
        return target_tz

    metadata = _mapping_or_empty(portfolio.get("metadata")) if portfolio is not None else {}
    for source in (portfolio, metadata, execution):
        if isinstance(source, Mapping):
            explicit_tz = str(source.get("market_timezone") or "").strip()
            if explicit_tz:
                return explicit_tz

    explicit_env_market = (
        os.environ.get("LONGBRIDGE_MARKET")
        or target_market
        or os.environ.get("MARKET")
        or None
    )
    if explicit_env_market and str(explicit_env_market).strip():
        market = infer_market(str(explicit_env_market).strip(), account_region="US")
        return _market_defaults(market)["market_timezone"]

    for source in (portfolio, metadata, execution):
        resolved = _timezone_from_mapping(source)
        if resolved:
            return resolved

    account_region = infer_account_region(
        os.environ.get("ACCOUNT_REGION"),
        account_prefix=os.environ.get("ACCOUNT_PREFIX", "DEFAULT"),
    )
    market = infer_market(None, account_region=account_region)
    return _market_defaults(market)["market_timezone"]


def _current_fuse_trade_date(
    portfolio: Mapping[str, Any] | None = None,
    *,
    execution: Mapping[str, Any] | None = None,
    market_timezone: str | None = None,
) -> str:
    """Return current trading date in the configured market timezone (fail-closed if invalid)."""
    tz_name = str(market_timezone or "").strip() or _resolve_fuse_market_timezone(
        portfolio, execution=execution
    )
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        # Non-empty sentinel prevents resolve_fuse_readback_inputs from falling back to FUSE_TRADE_DATE.
        return "__INVALID_MARKET_TIMEZONE__"
    return datetime.now(tz).date().isoformat()


def attach_snapshot_fuse_metadata(portfolio: dict[str, Any], snapshot: Any) -> None:
    """Forward FUSE readback and market timezone from ``PortfolioSnapshot.metadata``.

    Decision controls (``fuse_mode``, ``fuse_observe_until``, ``fuse_trade_date``)
    are intentionally excluded because QPK ``resolve_fuse_readback_inputs`` reads
    them strictly from runtime environment / explicit controls.
    """
    snapshot_metadata = getattr(snapshot, "metadata", None)
    if not isinstance(snapshot_metadata, Mapping):
        return
    for key in ("fuse_enabled", "fuse_readback"):
        if key in snapshot_metadata:
            portfolio[key] = snapshot_metadata[key]
    for key in ("market_timezone", "market"):
        if key in snapshot_metadata and key not in portfolio:
            portfolio[key] = snapshot_metadata[key]


def fuse_telemetry_from_admission(admission: NewRiskAdmissionResult) -> dict[str, Any]:
    """Extract redacted FUSE-2 decision fields (including non-blocking observe telemetry)."""
    fuse_decision = getattr(admission, "fuse_decision", None)
    if fuse_decision is None:
        return {}
    return {
        "fuse_readback_effective_mode": fuse_decision.effective_mode,
        "fuse_readback_would_block": bool(fuse_decision.would_block),
        "fuse_readback_reason_codes": list(fuse_decision.reason_codes),
        "fuse_readback_observe_window_expired": bool(fuse_decision.observe_window_expired),
    }


def copy_fuse_telemetry(target: dict[str, Any], source: Mapping[str, Any] | None) -> None:
    """Copy non-blocking FUSE-2 telemetry fields when present."""
    if not isinstance(source, Mapping):
        return
    for key in _FUSE_TELEMETRY_KEYS:
        if key in source:
            target[key] = source[key]


def merge_fuse_telemetry(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge FUSE telemetry across cycle checks without losing blocking evidence."""
    base = dict(existing or {})
    added = dict(incoming or {})
    if not added:
        return base
    if not base:
        return added
    merged_mode = (
        "enforce"
        if "enforce" in (base.get("fuse_readback_effective_mode"), added.get("fuse_readback_effective_mode"))
        else (added.get("fuse_readback_effective_mode") or base.get("fuse_readback_effective_mode"))
    )
    merged_reasons = list(
        dict.fromkeys(
            (
                *(base.get("fuse_readback_reason_codes") or ()),
                *(added.get("fuse_readback_reason_codes") or ()),
            )
        )
    )
    return {
        "fuse_readback_effective_mode": merged_mode,
        "fuse_readback_would_block": bool(
            base.get("fuse_readback_would_block") or added.get("fuse_readback_would_block")
        ),
        "fuse_readback_reason_codes": merged_reasons,
        "fuse_readback_observe_window_expired": bool(
            base.get("fuse_readback_observe_window_expired")
            or added.get("fuse_readback_observe_window_expired")
        ),
    }


def _snapshot_has_fuse_signal(snapshot: InjectedReconciliationSnapshot) -> bool:
    return (
        bool(snapshot.fuse_enabled)
        or snapshot.fuse_readback is not None
        or snapshot.fuse_trade_date is not None
        or snapshot.fuse_observe_until is not None
        or snapshot.fuse_mode != "enforce"
    )


def _resolve_fuse_inputs_for_portfolio(
    portfolio: Mapping[str, Any],
    projection: Mapping[str, Any],
    *,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve FUSE-2 inputs with native/account readback precedence and market trade date."""
    metadata = _mapping_or_empty(portfolio.get("metadata"))
    projection_for_fuse = (
        {key: value for key, value in projection.items() if key != "fuse_readback"}
        if ("fuse_readback" in portfolio or "fuse_readback" in metadata)
        else projection
    )
    explicit_fuse_enabled = bool(
        portfolio.get("fuse_enabled") or metadata.get("fuse_enabled") or projection.get("fuse_enabled")
    )
    base = resolve_fuse_readback_inputs(portfolio, projection_for_fuse)
    if not base.get("fuse_enabled") and not explicit_fuse_enabled:
        return base
    resolved = resolve_fuse_readback_inputs(
        portfolio,
        projection_for_fuse,
        current_trade_date=_current_fuse_trade_date(portfolio, execution=execution),
    )
    if explicit_fuse_enabled:
        resolved["fuse_enabled"] = True
    return resolved


def is_fuse_readback_active(
    portfolio: Mapping[str, Any] | None = None,
    *,
    execution: Mapping[str, Any] | None = None,
    snapshot: InjectedReconciliationSnapshot | None = None,
) -> bool:
    """Return True when runtime FUSE controls or any FUSE readback input/signal is active."""
    if snapshot is not None and _snapshot_has_fuse_signal(snapshot):
        return True
    if portfolio is not None:
        projection = _mapping_or_empty(portfolio.get("account_new_risk_snapshot"))
        if _resolve_fuse_inputs_for_portfolio(portfolio, projection, execution=execution).get("fuse_enabled"):
            return True
    return any(str(os.environ.get(key) or "").strip() for key in _FUSE_CONTROL_ENV_KEYS)


def is_effective_new_risk_gate_enabled(
    portfolio: Mapping[str, Any] | None = None,
    *,
    execution: Mapping[str, Any] | None = None,
    snapshot: InjectedReconciliationSnapshot | None = None,
) -> bool:
    """ACCOUNT_NEW_RISK_GATE=0 cannot disable active FUSE-2 readback enforcement."""
    return is_account_new_risk_gate_enabled() or is_fuse_readback_active(
        portfolio, execution=execution, snapshot=snapshot
    )


def _coerce_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _coerce_positive_float(value: object) -> float | None:
    """Accept only finite strictly positive floats."""
    number = _coerce_optional_float(value)
    return number if number is not None and number > 0.0 else None


def _positive_limit_or_none(value: object) -> float | None:
    """Accept only finite positive limits; never invent a production default."""
    return _coerce_positive_float(value)


def _mapping_or_empty(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_env_nonempty(*keys: str) -> str:
    for key in keys:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _first_nonempty_str(
    sources: tuple[Mapping[str, Any], ...],
    keys: tuple[str, ...],
    *,
    require_str: bool = True,
) -> str | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if require_str:
                if isinstance(value, str) and value.strip():
                    return value.strip()
            elif value is not None and str(value).strip():
                return str(value).strip()
    return None


def _first_present_float(key: str, *sources: Mapping[str, Any]) -> float | None:
    for source in sources:
        if key in source:
            return _coerce_optional_float(source.get(key))
    return None


def _prohibited_admission(reason_code: str) -> NewRiskAdmissionResult:
    return NewRiskAdmissionResult(
        disposition=NewRiskDisposition.NEW_RISK_PROHIBITED,
        reason_codes=(reason_code,),
    )


def _resolve_equity_usd(portfolio: Mapping[str, Any], execution: Mapping[str, Any] | None) -> float | None:
    for key in ("total_equity", "total_strategy_equity"):
        equity = _coerce_positive_float(portfolio.get(key))
        if equity is not None:
            return equity
    equity = _coerce_positive_float(_mapping_or_empty(portfolio.get("broker_capital")).get("net_assets"))
    if equity is not None:
        return equity
    return _coerce_positive_float(_mapping_or_empty(execution).get("portfolio_total_equity"))


def _resolve_unknown_pending(portfolio: Mapping[str, Any], projection: Mapping[str, Any]) -> bool:
    """LB local_admission uses ``unknown_pending_orders``; honor it from either surface."""
    if "unknown_pending_orders" in projection:
        return bool(projection.get("unknown_pending_orders"))
    if portfolio.get("unknown_pending_orders") is not None:
        return bool(portfolio.get("unknown_pending_orders"))
    return bool(_mapping_or_empty(portfolio.get("metadata")).get("unknown_pending_orders"))


def _resolve_durable_breaker_open(portfolio: Mapping[str, Any], projection: Mapping[str, Any]) -> bool:
    """Only an explicit durable OPEN state trips the breaker; absence is not OPEN."""
    for source in (projection, portfolio, _mapping_or_empty(portfolio.get("metadata"))):
        value = source.get("durable_circuit_breaker_state")
        if value is not None:
            return str(value) == "OPEN"
    return False


def _resolve_digest_flag(portfolio: Mapping[str, Any], projection: Mapping[str, Any], key: str) -> bool:
    for source in (projection, _mapping_or_empty(portfolio.get("metadata"))):
        if key in source:
            return bool(source.get(key))
    return False


def build_account_new_risk_snapshot(
    portfolio: Mapping[str, Any],
    *,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a cycle-health projection from evidence available this cycle.

    Health axes are derived from real cycle evidence (resolved equity,
    ``unknown_pending_orders``, an explicit durable breaker state, and any
    configured reconciliation digests) rather than a blanket unhealthy
    default. Explicit keys already present on an injected
    ``account_new_risk_snapshot`` projection always win.
    """
    projection = dict(portfolio.get("account_new_risk_snapshot") or {})
    equity_usd = _coerce_optional_float(projection.get("equity_usd"))
    if equity_usd is None:
        equity_usd = _resolve_equity_usd(portfolio, execution)

    evidence = CycleNewRiskHealthEvidence(
        observation_ok=equity_usd is not None,
        unknown_pending=_resolve_unknown_pending(portfolio, projection),
        digests_configured=_resolve_digest_flag(portfolio, projection, "digests_configured"),
        digests_verified=_resolve_digest_flag(portfolio, projection, "digests_verified"),
        durable_breaker_open=_resolve_durable_breaker_open(portfolio, projection),
    )
    projection = apply_cycle_new_risk_health_axes(projection, evidence)
    projection["equity_usd"] = equity_usd
    return projection

def _resolve_strategy_profile(sources: tuple[Mapping[str, Any], ...]) -> str:
    return (
        _first_nonempty_str(sources, ("strategy_profile",), require_str=True)
        or _first_env_nonempty("STRATEGY_PROFILE")
        or _DEFAULT_STRATEGY_PROFILE
    )


def _resolve_production_drift_status(portfolio: Mapping[str, Any], projection: Mapping[str, Any]) -> str | None:
    """Prefer explicit inject; else read-only PerformanceStore (Policy A). Fail-soft."""
    sources = (projection, portfolio, _mapping_or_empty(portfolio.get("metadata")))
    for source in sources:
        raw = source.get("production_drift_status")
        if raw is not None and raw != "":
            return str(raw).strip()
    profile = _resolve_strategy_profile(sources)
    domain = (
        _first_nonempty_str(sources, ("strategy_domain", "domain"), require_str=True)
        or _first_env_nonempty("STRATEGY_DOMAIN")
        or _DEFAULT_DOMAIN
    )
    return resolve_production_drift_status_from_store(
        strategy_profile=profile,
        domain=domain,
    )


def _resolve_drawdown_from_peak(
    *,
    equity_usd: float | None,
    peak_equity_usd: float | None,
    explicit: float | None,
) -> float | None:
    if explicit is not None:
        return explicit
    if equity_usd is None or peak_equity_usd is None or peak_equity_usd <= 0.0:
        return None
    return max(0.0, 1.0 - (equity_usd / peak_equity_usd))


def _resolve_explicit_daily_loss_usd(
    projection: Mapping[str, Any],
    portfolio: Mapping[str, Any],
    execution: Mapping[str, Any] | None,
) -> float | None:
    """Pass through an explicit daily_loss_usd fact only; never invent one."""
    return _first_present_float("daily_loss_usd", projection, portfolio, _mapping_or_empty(execution))


def _max_daily_loss_from_runtime_target_json() -> float | None:
    """Read max_daily_loss_usd from RUNTIME_TARGET_JSON when present; soft-omit on errors."""
    raw_target = os.environ.get("RUNTIME_TARGET_JSON")
    if raw_target is None or not str(raw_target).strip():
        return None
    try:
        payload = json.loads(raw_target)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    policy = payload.get("runtime_risk_limits")
    if not isinstance(policy, dict) or "max_daily_loss_usd" not in policy:
        return None
    return _positive_limit_or_none(policy.get("max_daily_loss_usd"))


def resolve_max_daily_loss_usd(
    portfolio: Mapping[str, Any] | None = None,
) -> float | None:
    """Resolve an explicit max_daily_loss_usd; omit the axis when unset.

    Priority: account_new_risk_snapshot / portfolio key → RUNTIME_TARGET_JSON →
    LONGBRIDGE_MAX_DAILY_LOSS_USD / MAX_DAILY_LOSS_USD. No approved production default.
    """
    if portfolio is not None:
        projection = _mapping_or_empty(portfolio.get("account_new_risk_snapshot"))
        for source in (projection, portfolio):
            if "max_daily_loss_usd" in source:
                limit = _positive_limit_or_none(source.get("max_daily_loss_usd"))
                if limit is not None:
                    return limit
    policy_limit = _max_daily_loss_from_runtime_target_json()
    if policy_limit is not None:
        return policy_limit
    for key in _MAX_DAILY_LOSS_ENV_KEYS:
        limit = _positive_limit_or_none(os.environ.get(key))
        if limit is not None:
            return limit
    return None


def runtime_risk_limits_for_daily_loss_axis(
    max_daily_loss_usd: float | None,
) -> RuntimeRiskLimits | None:
    """Build admission-only limits carrying ``max_daily_loss_usd``, or omit.

    SPY/1.0 caps are a minimal legal RuntimeRiskLimits carrier for the gate only —
    not a production RRL binding and not an exposure raise.
    """
    if max_daily_loss_usd is None:
        return None
    symbol = _DAILY_LOSS_LIMIT_CARRIER_SYMBOL
    return RuntimeRiskLimits(
        allowed_symbols=(symbol,),
        product_leverage_factors={symbol: 1},
        nominal_caps={symbol: 1.0},
        total_nominal_exposure_cap=1.0,
        total_effective_exposure_cap=1.0,
        max_positions=1,
        max_daily_loss_usd=max_daily_loss_usd,
    )


def build_snapshot_from_portfolio(
    portfolio: Mapping[str, Any],
    *,
    execution: Mapping[str, Any] | None = None,
) -> InjectedReconciliationSnapshot:
    """Project an injected reconciliation snapshot from an existing portfolio read."""
    projection = build_account_new_risk_snapshot(portfolio, execution=execution)
    fuse_inputs = _resolve_fuse_inputs_for_portfolio(portfolio, projection, execution=execution)
    equity_usd = _coerce_optional_float(projection.get("equity_usd"))
    if equity_usd is None:
        equity_usd = _resolve_equity_usd(portfolio, execution)
    peak_equity_usd = _first_present_float("peak_equity_usd", projection, portfolio)
    explicit_dd = _first_present_float("drawdown_from_peak", projection, portfolio)
    return InjectedReconciliationSnapshot(
        observation_status=str(projection.get("observation_status") or "UNAVAILABLE"),
        reconciliation_status=str(projection.get("reconciliation_status") or "UNVERIFIED"),
        circuit_breaker_state=str(projection.get("circuit_breaker_state") or "OPEN"),
        equity_usd=equity_usd,
        peak_equity_usd=peak_equity_usd,
        drawdown_from_peak=_resolve_drawdown_from_peak(
            equity_usd=equity_usd,
            peak_equity_usd=peak_equity_usd,
            explicit=explicit_dd,
        ),
        realized_vol=_first_present_float("realized_vol", projection, portfolio),
        production_drift_status=_resolve_production_drift_status(portfolio, projection),
        daily_loss_usd=_resolve_explicit_daily_loss_usd(projection, portfolio, execution),
        fuse_enabled=bool(fuse_inputs["fuse_enabled"]),
        fuse_readback=fuse_inputs["fuse_readback"],
        fuse_trade_date=fuse_inputs["fuse_trade_date"],
        fuse_mode=fuse_inputs["fuse_mode"],
        fuse_observe_until=fuse_inputs["fuse_observe_until"],
        legacy_new_risk_switch_enabled=is_account_new_risk_gate_enabled(),
    )


def evaluate_portfolio_new_risk_admission(
    portfolio: Mapping[str, Any],
    *,
    execution: Mapping[str, Any] | None = None,
) -> NewRiskAdmissionResult:
    try:
        snapshot = build_snapshot_from_portfolio(portfolio, execution=execution)
        limits = runtime_risk_limits_for_daily_loss_axis(resolve_max_daily_loss_usd(portfolio))
        return evaluate_new_risk_admission(snapshot, limits)
    except AccountNewRiskGateError:
        return _prohibited_admission("SNAPSHOT_VALIDATION_FAIL_CLOSED")


def new_risk_buy_prohibited(result: NewRiskAdmissionResult) -> bool:
    return result.disposition == NewRiskDisposition.NEW_RISK_PROHIBITED


def apply_combined_scale_to_allocation_targets(
    allocation: Mapping[str, Any] | None,
    combined_scale: float | None,
) -> dict[str, Any]:
    """Shrink allocation targets by admission combined_scale; omit when scale missing."""
    from quant_platform_kit.risk.capital_risk_envelope import apply_combined_scale_to_targets

    allocation_out = dict(allocation or {})
    allocation_out["targets"] = apply_combined_scale_to_targets(
        allocation_out.get("targets"),
        combined_scale,
    )
    return allocation_out


_ATTENTION_PLATFORM = "longbridge"
_OPERATIONAL_UNCERTAIN_REASONS = frozenset(
    {
        "EQUITY_UNKNOWN_FAIL_CLOSED",
        "SNAPSHOT_VALIDATION_FAIL_CLOSED",
        "RECONCILIATION_NOT_VERIFIED",
        "CIRCUIT_BREAKER_OPEN",
        "UNKNOWN_PENDING_ORDERS",
        "FUSE_READBACK_MISSING_FAIL_CLOSED",
        "FUSE_READBACK_STALE_FAIL_CLOSED",
        "FUSE_READBACK_INVALID_FAIL_CLOSED",
        "FUSE_READBACK_MODE_INVALID_FAIL_CLOSED",
        "FUSE_OBSERVE_WINDOW_INVALID_FAIL_CLOSED",
    }
)
_attention_sent_keys: set[str] = set()


def _resolve_attention_strategy_profile(portfolio: Mapping[str, Any]) -> str:
    projection = _mapping_or_empty(portfolio.get("account_new_risk_snapshot"))
    return _resolve_strategy_profile((projection, portfolio, _mapping_or_empty(portfolio.get("metadata"))))


def _resolve_attention_account_alias(
    portfolio: Mapping[str, Any],
    execution: Mapping[str, Any] | None,
) -> str:
    sources = (
        portfolio,
        _mapping_or_empty(portfolio.get("metadata")),
        _mapping_or_empty(execution),
    )
    text = (
        _first_nonempty_str(
            sources,
            ("account_alias", "account_hash", "account_id", "account"),
            require_str=False,
        )
        or _first_env_nonempty("LONGBRIDGE_ACCOUNT_ID", "ACCOUNT_ALIAS")
    )
    if not text:
        return "unknown"
    return text[-8:] if len(text) > 8 else text


def maybe_publish_attention_for_admission(
    admission: NewRiskAdmissionResult,
    *,
    portfolio: Mapping[str, Any],
    execution: Mapping[str, Any] | None = None,
    snapshot: InjectedReconciliationSnapshot | None = None,
    telegram_sender: Any | None = None,
    log_message: Any = print,
) -> Mapping[str, int]:
    """Publish ACTION/HALT attention when NEW_RISK / ops axes require a page.

    Legacy uses process-local transition keys; the opted-in notification adapter
    uses durable claims. Never grants live, raises RRL, or invents daily-loss facts.
    """

    try:
        from quant_platform_kit.risk.attention import (
            AttentionAxes,
            evaluate_attention,
            resolve_mandate_dd_budget,
        )
        from quant_platform_kit.risk.attention_notify import publish_attention_telegram_transition
    except ImportError:
        try:
            log_message("attention_telegram_skipped reason=attention_api_unavailable")
        except TypeError:
            log_message("attention_telegram_skipped reason=attention_api_unavailable", flush=True)
        return {"sent": 0, "skipped": 1, "failed": 0}

    reasons = tuple(admission.reason_codes or ())
    prohibited = new_risk_buy_prohibited(admission)
    operational_uncertain = any(code in _OPERATIONAL_UNCERTAIN_REASONS for code in reasons)
    profile = _resolve_attention_strategy_profile(portfolio)
    drawdown = None if snapshot is None else snapshot.drawdown_from_peak
    decision = evaluate_attention(
        AttentionAxes(
            new_risk_prohibited=True if prohibited else None,
            operational_uncertain=True if operational_uncertain else None,
            drawdown_from_peak=drawdown,
            mandate_dd_budget=resolve_mandate_dd_budget(profile),
        )
    )
    unconfigured_logged = False

    def _tracking_log_message(message: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal unconfigured_logged
        if "reason=telegram_not_configured_or_false" in str(message or ""):
            unconfigured_logged = True
        try:
            return log_message(message, *args, **kwargs)
        except TypeError:
            return log_message(message)

    from longbridge_platform.notifications.unified import enabled as unified_enabled, publish_attention
    if unified_enabled():
        counts = publish_attention(decision, telegram_sender=telegram_sender,
            recover=(not prohibited and not operational_uncertain and snapshot is not None
                     and str(snapshot.observation_status) == "COMPLETE"
                     and str(snapshot.reconciliation_status) == "VERIFIED"))
        # Unified `publish_attention` folds send/transport failures into `skipped`,
        # so only `sent > 0` proves confirmed delivery there.
        _LAST_ATTENTION_CONFIRMED.set(int(counts.get("sent", 0) or 0) > 0)
        return counts
    counts = publish_attention_telegram_transition(
        decision=decision,
        platform=_ATTENTION_PLATFORM,
        account_alias=_resolve_attention_account_alias(portfolio, execution),
        strategy_profile=profile,
        previous_level=None,
        already_sent_keys=list(_attention_sent_keys),
        record_sent_key=_attention_sent_keys.add,
        telegram_sender=telegram_sender,
        log_message=_tracking_log_message,
    )
    _LAST_ATTENTION_CONFIRMED.set(
        int(counts.get("sent", 0) or 0) > 0
        or (
            int(counts.get("failed", 0) or 0) == 0
            and int(counts.get("skipped", 0) or 0) > 0
            and not unconfigured_logged
            and decision.level.value in {"action", "halt"}
        )
    )
    return counts


def last_attention_delivery_confirmed() -> bool:
    """Return True if the last attention publish sent or confirmed transition dedup."""
    return bool(_LAST_ATTENTION_CONFIRMED.get())


def reset_attention_sent_keys_for_tests() -> None:
    _attention_sent_keys.clear()
    _LAST_ATTENTION_CONFIRMED.set(False)


def get_cycle_snapshot() -> InjectedReconciliationSnapshot | None:
    return _current_cycle_risk_state().snapshot


_UNSET_DAILY_LOSS = object()


def set_cycle_snapshot(
    snapshot: InjectedReconciliationSnapshot | None,
    *,
    portfolio: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
    market_timezone: str | None = None,
    controls_from_env: bool | None = None,
    max_daily_loss_usd: object = _UNSET_DAILY_LOSS,
) -> None:
    if snapshot is None:
        _CYCLE_RISK_STATE.set(None)
        return
    next_controls_from_env = (
        bool(controls_from_env)
        if controls_from_env is not None
        else (portfolio is not None or execution is not None)
    )
    if market_timezone is not None:
        next_tz: str | None = market_timezone
    elif portfolio is not None or execution is not None:
        next_tz = _resolve_fuse_market_timezone(portfolio, execution=execution)
    else:
        next_tz = None

    if max_daily_loss_usd is not _UNSET_DAILY_LOSS:
        next_limit = _positive_limit_or_none(max_daily_loss_usd)
        next_limit_bound = next_limit is not None
    elif portfolio is not None:
        next_limit = resolve_max_daily_loss_usd(portfolio)
        next_limit_bound = next_limit is not None
    else:
        next_limit = None
        next_limit_bound = False

    _CYCLE_RISK_STATE.set(
        _CycleRiskState(
            snapshot=snapshot,
            market_timezone=next_tz,
            controls_from_env=next_controls_from_env,
            max_daily_loss_usd=next_limit,
            max_daily_loss_bound=next_limit_bound,
        )
    )


@contextmanager
def account_new_risk_gate_cycle(portfolio: Mapping[str, Any], *, execution: Mapping[str, Any] | None = None):
    """Bind one portfolio projection for the current execution cycle."""
    previous_state = _CYCLE_RISK_STATE.get()
    set_cycle_snapshot(
        build_snapshot_from_portfolio(portfolio, execution=execution),
        portfolio=portfolio,
        execution=execution,
        controls_from_env=True,
    )
    try:
        yield
    finally:
        _CYCLE_RISK_STATE.set(previous_state)


def evaluate_cycle_new_risk_admission() -> NewRiskAdmissionResult:
    state = _current_cycle_risk_state()
    if state.snapshot is None:
        return _prohibited_admission("EQUITY_UNKNOWN_FAIL_CLOSED")
    snapshot = state.snapshot
    runtime_inputs = resolve_fuse_readback_inputs(
        {}, {}, current_trade_date=_current_fuse_trade_date(market_timezone=state.market_timezone)
    )
    env_fuse_active = any(str(os.environ.get(key) or "").strip() for key in _FUSE_CONTROL_ENV_KEYS)
    legacy_enabled = snapshot.legacy_new_risk_switch_enabled if is_account_new_risk_gate_enabled() else False
    if env_fuse_active or _snapshot_has_fuse_signal(snapshot):
        if state.controls_from_env:
            fuse_mode = runtime_inputs["fuse_mode"]
            fuse_observe_until = runtime_inputs["fuse_observe_until"]
        else:
            env_mode = str(os.environ.get("FUSE_READBACK_MODE") or "").strip()
            env_observe_until = str(os.environ.get("FUSE_OBSERVE_UNTIL") or "").strip()
            fuse_mode = runtime_inputs["fuse_mode"] if env_mode else snapshot.fuse_mode
            fuse_observe_until = (
                runtime_inputs["fuse_observe_until"] if env_observe_until else snapshot.fuse_observe_until
            )
        snapshot = replace(
            snapshot,
            fuse_enabled=True,
            fuse_trade_date=runtime_inputs["fuse_trade_date"],
            fuse_mode=fuse_mode,
            fuse_observe_until=fuse_observe_until,
            legacy_new_risk_switch_enabled=legacy_enabled,
        )
    elif legacy_enabled != snapshot.legacy_new_risk_switch_enabled:
        snapshot = replace(snapshot, legacy_new_risk_switch_enabled=legacy_enabled)
    try:
        resolved_limit = (
            state.max_daily_loss_usd if state.max_daily_loss_bound else resolve_max_daily_loss_usd()
        )
        limits = runtime_risk_limits_for_daily_loss_axis(resolved_limit)
        return evaluate_new_risk_admission(snapshot, limits)
    except AccountNewRiskGateError:
        return _prohibited_admission("SNAPSHOT_VALIDATION_FAIL_CLOSED")
