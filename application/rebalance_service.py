"""Application orchestration for LongBridgePlatform."""

from __future__ import annotations

import re
from datetime import datetime

from application.execution_service import execute_rebalance_cycle
from application.runtime_dependencies import LongBridgeRebalanceConfig, LongBridgeRebalanceRuntime
from notifications.events import NotificationPublisher
from notifications import renderers as notification_renderers
from quant_platform_kit.common.notification_localization import (
    localize_notification_text as _base_localize_notification_text,
    translator_uses_zh as _base_translator_uses_zh,
)

_DETAIL_FIELD_SPLIT_RE = re.compile(r"\s+(?=[^\s=:：]+[=:：])")


def _plan_portfolio(plan):
    return dict(plan.get("portfolio") or {})


def _plan_execution(plan):
    return dict(plan.get("execution") or {})


def _plan_allocation(plan):
    return dict(plan.get("allocation") or {})


def _noop_sleep(_seconds):
    return None

def _translator_uses_zh(translator) -> bool:
    return _base_translator_uses_zh(translator)


def _localize_notification_text(text, *, translator):
    return _base_localize_notification_text(text, translator=translator)


def _split_detail_segment(text):
    value = str(text or "").strip()
    if not value:
        return []
    if "=" not in value and ":" not in value and "：" not in value:
        return [value]
    return [part.strip() for part in _DETAIL_FIELD_SPLIT_RE.split(value) if part.strip()]


def _split_labeled_text(text):
    segments = [segment.strip() for segment in str(text or "").split(" | ") if segment.strip()]
    if not segments:
        return []
    lines = [segments[0]]
    for segment in segments[1:]:
        lines.extend(_split_detail_segment(segment))
    return lines


def _append_labeled_text(lines, template_key, value, *, translator, value_key):
    parts = _split_labeled_text(value)
    if not parts:
        return
    lines.append(translator(template_key, **{value_key: parts[0]}))
    lines.extend(f"  - {part}" for part in parts[1:])


def _has_benchmark_context(execution):
    return any(
        float(execution.get(key) or 0.0) > 0.0
        for key in ("benchmark_price", "long_trend_value", "exit_line")
    )


def _build_benchmark_lines(execution, *, translator):
    if not _has_benchmark_context(execution):
        return []
    benchmark_symbol = str(execution.get("benchmark_symbol") or "QQQ")
    benchmark_price = float(execution.get("benchmark_price") or 0.0)
    long_trend_value = float(execution.get("long_trend_value") or 0.0)
    exit_line = float(execution.get("exit_line") or 0.0)
    return [
        translator("benchmark_title", symbol=benchmark_symbol),
        f"  - {translator('benchmark_price', symbol=benchmark_symbol, value=f'{benchmark_price:.2f}')}",
        f"  - {translator('benchmark_ma200', value=f'{long_trend_value:.2f}')}",
        f"  - {translator('benchmark_exit', value=f'{exit_line:.2f}')}",
    ]


def _format_dashboard_text(text) -> str:
    return "\n".join(
        line.rstrip()
        for line in str(text or "").splitlines()
        if line.strip()
    )


def _append_dashboard_lines(lines, *, execution) -> None:
    dashboard_text = _format_dashboard_text(execution.get("dashboard_text"))
    if dashboard_text:
        lines.extend(dashboard_text.splitlines())


def _append_status_lines(lines, *, execution, translator, signal_key):
    status_display = _localize_notification_text(execution.get("status_display"), translator=translator)
    if status_display:
        _append_labeled_text(lines, "market_status", status_display, translator=translator, value_key="status")

    deploy_ratio_text = str(execution.get("deploy_ratio_text") or "").strip()
    if deploy_ratio_text:
        lines.append(translator("risk_position", ratio=deploy_ratio_text))

    income_ratio_text = str(execution.get("income_ratio_text") or "").strip()
    if income_ratio_text:
        lines.append(translator("income_target", ratio=income_ratio_text))

    income_locked_ratio_text = str(execution.get("income_locked_ratio_text") or "").strip()
    if income_locked_ratio_text:
        lines.append(translator("income_locked", ratio=income_locked_ratio_text))

    signal_display = _localize_notification_text(execution.get("signal_display"), translator=translator)
    if signal_display:
        _append_labeled_text(lines, signal_key, signal_display, translator=translator, value_key="msg")

    lines.extend(_build_benchmark_lines(execution, translator=translator))


def _first_detail_line(text) -> str:
    parts = _split_labeled_text(text)
    return parts[0] if parts else ""


def _append_compact_status_lines(lines, *, execution, translator, signal_key):
    status_summary = _first_detail_line(
        _localize_notification_text(execution.get("status_display"), translator=translator)
    )
    if status_summary:
        lines.append(translator("market_status", status=status_summary))

    signal_summary = _first_detail_line(
        _localize_notification_text(execution.get("signal_display"), translator=translator)
    )
    if signal_summary:
        lines.append(translator(signal_key, msg=signal_summary))



def _append_strategy_line(lines, *, strategy_display_name, translator):
    name = str(strategy_display_name or "").strip()
    if name:
        lines.append(translator("strategy_label", name=name))


_localize_notification_text = notification_renderers._localize_notification_text
_format_dashboard_text = notification_renderers._format_dashboard_text
_append_status_lines = notification_renderers._append_status_lines



def run_strategy(
    *,
    runtime: LongBridgeRebalanceRuntime,
    config: LongBridgeRebalanceConfig,
):
    print(config.with_prefix(f"[{datetime.now()}] Starting strategy..."), flush=True)
    notification_publisher = NotificationPublisher(
        log_message=lambda message: print(config.with_prefix(message), flush=True),
        send_message=runtime.notifications.send_text,
    )
    quote_context, trade_context, indicators = runtime.bootstrap()
    market_data_port = runtime.market_data_port_factory(quote_context)
    execution_port = runtime.execution_port_factory(trade_context)

    def load_plan(*, current_snapshot):
        current_plan = runtime.resolve_rebalance_plan(
            indicators=indicators,
            snapshot=current_snapshot,
        )
        current_portfolio = _plan_portfolio(current_plan)
        current_execution = _plan_execution(current_plan)
        current_allocation = _plan_allocation(current_plan)
        if current_allocation.get("target_mode") != "value":
            raise ValueError("LongBridgePlatform requires allocation.target_mode=value")
        return current_plan, current_portfolio, current_execution, current_allocation

    def fetch_replanned_state():
        current_snapshot = runtime.portfolio_port_factory(
            quote_context,
            trade_context,
        ).get_portfolio_snapshot()
        return load_plan(current_snapshot=current_snapshot)

    plan, portfolio, execution, allocation = fetch_replanned_state()

    execution_result = execute_rebalance_cycle(
        trade_context=trade_context,
        plan=plan,
        portfolio=portfolio,
        execution=execution,
        allocation=allocation,
        fetch_replanned_state=fetch_replanned_state,
        market_data_port=market_data_port,
        estimate_max_purchase_quantity=runtime.estimate_max_purchase_quantity,
        execution_port=execution_port,
        post_submit_order=runtime.post_submit_order,
        notify_issue=runtime.notify_issue,
        translator=config.translator,
        with_prefix=config.with_prefix,
        limit_sell_discount=config.limit_sell_discount,
        limit_buy_premium=config.limit_buy_premium,
        dry_run_only=config.dry_run_only,
        post_sell_refresh_attempts=config.post_sell_refresh_attempts,
        post_sell_refresh_interval_sec=config.post_sell_refresh_interval_sec,
        sleeper=config.sleeper or _noop_sleep,
    )
    execution = execution_result.execution
    logs = list(execution_result.logs)
    skip_logs = list(execution_result.skip_logs)
    note_logs = list(execution_result.note_logs)
    action_done = execution_result.action_done

    if action_done:
        notification_publisher.publish(
            notification_renderers.render_rebalance_notification(
                execution=execution,
                logs=logs,
                skip_logs=skip_logs,
                note_logs=note_logs,
                translator=config.translator,
                separator=config.separator,
                strategy_display_name=config.strategy_display_name,
                dry_run_only=config.dry_run_only,
            )
        )
    else:
        notification_publisher.publish(
            notification_renderers.render_heartbeat_notification(
                execution=execution,
                skip_logs=skip_logs,
                note_logs=note_logs,
                translator=config.translator,
                separator=config.separator,
                strategy_display_name=config.strategy_display_name,
                dry_run_only=config.dry_run_only,
            )
        )
