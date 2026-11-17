"""LongBridge adapter for QPK account-level NEW_RISK gate (fail-closed)."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

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
_MAX_DAILY_LOSS_ENV_KEYS = ("LONGBRIDGE_MAX_DAILY_LOSS_USD", "MAX_DAILY_LOSS_USD")

_DEFAULT_STRATEGY_PROFILE = "soxl_soxx_trend_income"
_DEFAULT_DOMAIN = "us_equity"

# Minimal carrier for gate-only daily-loss axis; not a production RRL binding.
_DAILY_LOSS_LIMIT_CARRIER_SYMBOL = "SPY"

_cycle_snapshot: InjectedReconciliationSnapshot | None = None


def is_account_new_risk_gate_enabled() -> bool:
    """Production default on; set ACCOUNT_NEW_RISK_GATE=0 only for tests."""
    return str(os.environ.get(ACCOUNT_NEW_RISK_GATE_ENV, "") or "").strip() != "0"


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
                return _positive_limit_or_none(source.get("max_daily_loss_usd"))
    policy_limit = _max_daily_loss_from_runtime_target_json()
    if policy_limit is not None:
        return policy_limit
    env_raw = _first_env_nonempty(*_MAX_DAILY_LOSS_ENV_KEYS)
    return _positive_limit_or_none(env_raw) if env_raw else None


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
    from notifications.unified import enabled as unified_enabled, publish_attention
    if unified_enabled():
        return publish_attention(decision, telegram_sender=telegram_sender,
            recover=(not prohibited and not operational_uncertain and snapshot is not None
                     and str(snapshot.observation_status) == "COMPLETE"
                     and str(snapshot.reconciliation_status) == "VERIFIED"))
    return publish_attention_telegram_transition(
        decision=decision,
        platform=_ATTENTION_PLATFORM,
        account_alias=_resolve_attention_account_alias(portfolio, execution),
        strategy_profile=profile,
        previous_level=None,
        already_sent_keys=list(_attention_sent_keys),
        record_sent_key=_attention_sent_keys.add,
        telegram_sender=telegram_sender,
        log_message=log_message,
    )


def reset_attention_sent_keys_for_tests() -> None:
    _attention_sent_keys.clear()


def get_cycle_snapshot() -> InjectedReconciliationSnapshot | None:
    return _cycle_snapshot


def set_cycle_snapshot(snapshot: InjectedReconciliationSnapshot | None) -> None:
    global _cycle_snapshot
    _cycle_snapshot = snapshot


@contextmanager
def account_new_risk_gate_cycle(portfolio: Mapping[str, Any], *, execution: Mapping[str, Any] | None = None):
    """Bind one portfolio projection for the current execution cycle."""
    previous = _cycle_snapshot
    set_cycle_snapshot(build_snapshot_from_portfolio(portfolio, execution=execution))
    try:
        yield
    finally:
        set_cycle_snapshot(previous)


def evaluate_cycle_new_risk_admission() -> NewRiskAdmissionResult:
    if _cycle_snapshot is None:
        return _prohibited_admission("EQUITY_UNKNOWN_FAIL_CLOSED")
    try:
        limits = runtime_risk_limits_for_daily_loss_axis(resolve_max_daily_loss_usd())
        return evaluate_new_risk_admission(_cycle_snapshot, limits)
    except AccountNewRiskGateError:
        return _prohibited_admission("SNAPSHOT_VALIDATION_FAIL_CLOSED")
