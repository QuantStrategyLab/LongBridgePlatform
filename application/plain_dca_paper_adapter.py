"""Phase 1 plain-DCA consumer. It can return previews, never broker orders.

The trusted caller supplies a fresh protected binding readback on every call.
Labels are checked against the manifest and explicit broker PAPER configuration;
this does not authenticate credentials or establish deployed account isolation.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
import importlib
import json
from typing import Any

from application.runtime_target_manifest import load_runtime_target_manifest

PAPER_ENVIRONMENT = "longbridge-paper"
_BASELINES = frozenset({"nasdaq100_buy_and_hold", "sp500_buy_and_hold"})


class PlainDcaPaperError(ValueError):
    """Fixed diagnostic codes only; protected values must never be rendered."""


def _paper_account_from_readback(readback: object, *, now: datetime) -> str:
    if not isinstance(readback, Mapping):
        raise PlainDcaPaperError("paper_binding_readback_invalid")
    snapshot = dict(readback)
    observed, expires = snapshot.get("observed_at"), snapshot.get("valid_until")
    if (not all(isinstance(value, datetime) and value.utcoffset() is not None
                for value in (now, observed, expires))
            or not observed <= now < expires or now - observed > timedelta(minutes=5)):
        raise PlainDcaPaperError("paper_binding_readback_stale")
    if snapshot.get("environment") != PAPER_ENVIRONMENT or snapshot.get("target_id") != "paper":
        raise PlainDcaPaperError("paper_target_environment_required")
    if any(snapshot.get(key) is not False for key in (
        "live_enabled", "kelly_enabled", "stop_loss_enabled",
    )):
        raise PlainDcaPaperError("paper_safety_off_not_proven")
    protected = snapshot.get("protected_env")
    if not isinstance(protected, Mapping):
        raise PlainDcaPaperError("paper_protected_binding_missing")
    env = dict(protected)
    try:
        manifest = load_runtime_target_manifest()
        entries = [entry for entry in manifest.targets if entry.id == "paper"]
        target = json.loads(env.get("RUNTIME_TARGET_JSON", ""))
        if "QSL_RUNTIME_TARGET_JSON" in env and json.loads(env["QSL_RUNTIME_TARGET_JSON"]) != target:
            raise ValueError
    except Exception:
        raise PlainDcaPaperError("paper_target_readback_invalid") from None
    if (len(entries) != 1 or entries[0].environment != PAPER_ENVIRONMENT
            or entries[0].mode != "paper" or not isinstance(target, dict)):
        raise PlainDcaPaperError("paper_target_readback_invalid")
    if (env.get("CLOUD_RUN_SERVICE") != entries[0].service
            or target.get("service_name") != entries[0].service
            or target.get("platform_id") != "longbridge"
            or target.get("execution_environment") != "paper"
            or target.get("execution_mode") != "live"
            or target.get("dry_run_only") is not False
            or target.get("account_scope") != "PAPER"
            or target.get("account_selector") != ["PAPER"]
            or target.get("deployment_selector") != "PAPER"):
        raise PlainDcaPaperError("paper_protected_target_mismatch")
    # False means broker PAPER rather than local dry-run in the shared contract.
    # Phase 1 remains disabled and this module has no order writer in either case.
    if (env.get("LONGBRIDGE_DRY_RUN_ONLY") != "false"
            or env.get("RUNTIME_TARGET_ENABLED") != "false"
            or any(env.get("QSL_" + key, "false") != "false" for key in (
                "RUNTIME_TARGET_ENABLED", "LONGBRIDGE_DRY_RUN_ONLY"))):
        raise PlainDcaPaperError("paper_disabled_binding_required")
    account = env.get("LONGBRIDGE_PHYSICAL_ACCOUNT_ID")
    if (not isinstance(account, str) or not account.strip() or account != account.strip()
            or account.upper() in {"PAPER", "LIVE", "SG", "HK", "US", "DEFAULT"}):
        raise PlainDcaPaperError("paper_physical_account_binding_required")
    return account


def check_plain_dca_paper_binding(readback: object, *, now: datetime) -> None:
    """Reject any unresolved, non-paper, enabled or unknown protected binding."""
    _paper_account_from_readback(readback, now=now)


def consume_plain_dca_preview(
    *, read_binding: Callable[[], Mapping[str, object]], plan: Any, calendar: Any,
    period: Any, facts: Any, quote: Any, capabilities: Any, now: Any,
) -> Any:
    """Consume UES's exact preview contract without scaling or persisting it.

    No cached admission, period claim, simulated fill, schedule or broker call is
    created. A future order writer still needs atomic reread/claim/fencing and
    authentic isolation/qualification evidence. Reader failures are terminal for
    this attempt. Restarting the caller does not retain an earlier admission.
    """
    try:
        readback = read_binding()
    except Exception:
        raise PlainDcaPaperError("paper_binding_readback_unavailable") from None
    account = _paper_account_from_readback(readback, now=now)
    binding = getattr(getattr(plan, "mandate", None), "binding", None)
    if (getattr(binding, "environment", None) != "paper"
            or getattr(binding, "account_ref", None) != account
            or getattr(binding, "profile", None) not in _BASELINES):
        raise PlainDcaPaperError("plain_dca_plan_binding_mismatch")
    try:
        api = importlib.import_module("us_equity_strategies.plain_dca")
        result = api.evaluate_dca_period(
            plan=plan, calendar=calendar, period=period, facts=facts, quote=quote,
            capabilities=capabilities, now=now,
        )
    except (ImportError, AttributeError):
        raise PlainDcaPaperError("plain_dca_api_unavailable") from None
    except Exception:
        raise PlainDcaPaperError("plain_dca_evaluation_failed") from None
    if (not isinstance(result, api.PeriodPreview)
            or result.no_order is not True or result.no_execute is not True
            or result.preview_only is not True or result.live_ready is not False):
        raise PlainDcaPaperError("plain_dca_preview_contract_invalid")
    return result
