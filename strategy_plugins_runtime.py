"""Strategy plugin signal loading, report attachment, and alert dispatch."""


# ruff: noqa: F403, F405
from __future__ import annotations


import runtime_wiring as rw
from runtime_wiring import *  # noqa: F403, F401

def build_strategy_plugin_alert_messages(signals):
    return STRATEGY_ADAPTERS.build_strategy_plugin_alert_messages(signals)
def build_strategy_plugin_alert_state_settings():
    return StrategyPluginAlertStateSettings.from_env(
        project_id=PROJECT_ID,
    )
def build_strategy_plugin_alert_context_label() -> str:
    return build_alert_context_label(
        platform_id="longbridge",
        strategy_profile=STRATEGY_PROFILE,
        account_scope=ACCOUNT_REGION,
        service_name=SECRET_NAME,
        runtime_target=RUNTIME_SETTINGS.runtime_target,
    )
def publish_strategy_plugin_alerts(signals, *, report=None):
    from notifications.digest_snapshot.digest_routing import immediate_plugin_signals
    from notifications.digest_snapshot.digest_candidates import routine_observation_counts
    if report is not None:
        report.setdefault("summary", {})["routine_observation_counts"] = routine_observation_counts(signals)
    result = dispatch_strategy_plugin_alerts(
        immediate_plugin_signals(signals, mode="daily_digest"),
        notification_settings=RUNTIME_SETTINGS,
        translator=t,
        strategy_label=STRATEGY_PROFILE,
        context_label=build_strategy_plugin_alert_context_label(),
        state_settings=build_strategy_plugin_alert_state_settings(),
        log_message=print,
    )
    if report is not None:
        result.attach_to_report(report)
    return result
