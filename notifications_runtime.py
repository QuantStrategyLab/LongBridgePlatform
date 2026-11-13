"""Telegram and unified notification helpers for runtime routes."""


# ruff: noqa: F403, F405
from __future__ import annotations


import runtime_wiring as rw
from runtime_wiring import *  # noqa: F403, F401

def _runtime_failure_reason(exc: Exception) -> str:
    # Match only fixed platform errors; never publish arbitrary provider text.
    if isinstance(exc, RuntimeError) and str(exc) == "validation_journal_read_failed":
        return "validation_journal_read_failed"
    if isinstance(exc, RuntimeError) and str(exc) == "validation_journal_unavailable":
        return "validation_journal_unavailable"
    if isinstance(exc, RuntimeError) and str(exc) == "validation_journal_empty":
        return "validation_journal_empty"
    if isinstance(exc, RuntimeError) and str(exc) == "validation_side_effect_enabled":
        return "validation_side_effect_enabled"
    if isinstance(exc, RuntimeError) and str(exc) == "validation_marker_write_blocked":
        return "validation_marker_write_blocked"
    if isinstance(exc, RuntimeError) and str(exc) == "validation_read_only_bootstrap_unavailable":
        return "validation_read_only_bootstrap_unavailable"
    if isinstance(exc, RuntimeError) and str(exc) in {
        "durable execution command producer is paper-only and cannot be enabled live",
        "durable execution command consumer is paper-only and cannot be enabled live",
        "strategy risk state persistence is paper-only and cannot be enabled live",
    }:
        return "paper_live_conflict"
    return "unknown"
def _compact_error_notification(
    exc: Exception,
    *,
    title: str | None = None,
    prefix: str = "",
    phase: str = "strategy_cycle",
) -> str:
    error_text = type(exc).__name__
    known_phases = {"market_hours", "runtime_configuration", "strategy_cycle", "reporting", "health_probe"}
    safe_phase = phase if phase in known_phases else "strategy_cycle"
    reason = _runtime_failure_reason(exc)
    message = "\n".join((
        title or t("error_title"),
        t("failure_phase", phase=t(f"failure_phase_{safe_phase}")),
        t(f"failure_reason_{reason}"),
        f"{prefix}{error_text}",
    ))
    if len(message) <= COMPACT_ERROR_NOTIFICATION_MAX_CHARS:
        return message
    return message[: COMPACT_ERROR_NOTIFICATION_MAX_CHARS - 1].rstrip() + "…"
def _split_env_list(value: str | None) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    )
def _runtime_error_notification_targets() -> tuple[tuple[str, str], ...]:
    targets: list[tuple[str, str]] = []
    if TG_TOKEN and TG_CHAT_ID:
        targets.append((TG_TOKEN, TG_CHAT_ID))

    seen: set[tuple[str, str]] = set()
    unique_targets: list[tuple[str, str]] = []
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        unique_targets.append(target)
    return tuple(unique_targets)
def _runtime_error_notification_message(exc: Exception, *, route_label: str) -> str:
    title = "runtime_failure_title"
    return "\n".join((
        t(title),
        t("strategy_label", name=strategy_display_name or STRATEGY_PROFILE),
        t("runtime_failure_context", context=ACCOUNT_REGION),
        t("runtime_failure_result"),
        t("runtime_failure_action"),
    ))
def _send_runtime_error(exc: Exception, *, route_label: str) -> bool:
    targets = _runtime_error_notification_targets()
    if not targets:
        print(t("runtime_notification_missing_target"), flush=True)
        return False
    message = _runtime_error_notification_message(exc, route_label=route_label)
    outcomes = []
    for token, chat_id in targets:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=10,
            )
            status_code = int(getattr(response, "status_code", 200) or 200)
            acknowledged = 200 <= status_code < 300
            load_payload = getattr(response, "json", None)
            payload = load_payload() if acknowledged and callable(load_payload) else None
            if isinstance(payload, dict) and payload.get("ok") is False:
                acknowledged = False
            outcomes.append(acknowledged)
        except Exception:
            print(
                t("runtime_notification_delivery_failed"),
                flush=True,
            )
            outcomes.append(False)
    return bool(outcomes) and all(outcomes)
def _notify_runtime_error(exc: Exception, *, route_label: str) -> bool:
    return unified_notifications.dispatch_immediate(
        lambda: _send_runtime_error(exc, route_label=route_label), kind="real_error",
        incident=f"runtime_route:{route_label}:{_runtime_failure_reason(exc)}",
        occurred_at=datetime.now(timezone.utc), run_id=build_run_id(),
    ) if unified_notifications.enabled() else _send_runtime_error(exc, route_label=route_label)
def _handle_route_runtime_error(exc: Exception, *, route_label: str):
    print(t("runtime_failure_log"), flush=True)
    if validation_cycle_observation.current() is None:
        _notify_runtime_error(exc, route_label=route_label)
    return "Error", 500
def _route_with_runtime_error_fallback(handler, *, success_body: str, route_label: str, **kwargs):
    try:
        if unified_notifications.enabled():
            with unified_notifications.bind_route(route_label):
                result = handler(**kwargs)
        else:
            result = handler(**kwargs)
    except Exception as exc:
        return _handle_route_runtime_error(exc, route_label=route_label)
    if result is False:
        return "Error", 500
    if isinstance(result, tuple):
        return result
    return success_body, 200
