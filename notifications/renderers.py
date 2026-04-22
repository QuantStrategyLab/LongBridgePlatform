"""Notification rendering helpers for LongBridgePlatform."""

from __future__ import annotations

import re

from notifications.events import RenderedNotification
from quant_platform_kit.common.notification_localization import (
    localize_notification_text as _base_localize_notification_text,
    translator_uses_zh as _base_translator_uses_zh,
)

_DETAIL_FIELD_SPLIT_RE = re.compile(r"\s+(?=[^\s=:：]+[=:：])")


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


def _build_timing_audit_lines(execution, *, translator):
    signal_date = str(execution.get("signal_date") or "").strip()
    effective_date = str(execution.get("effective_date") or "").strip()
    contract = str(execution.get("execution_timing_contract") or "").strip()
    if not signal_date and not effective_date and not contract:
        return []
    label = "⏱ 执行时点" if _translator_uses_zh(translator) else "⏱ Timing"
    if signal_date and effective_date:
        value = f"{signal_date} -> {effective_date}"
    else:
        value = signal_date or effective_date or contract
    if contract and contract not in value:
        value = f"{value} ({contract})" if value else contract
    return [f"{label}: {value}"]


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


def _append_timing_lines(lines, *, execution, translator) -> None:
    lines.extend(_build_timing_audit_lines(execution, translator=translator))


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


def render_rebalance_notification(
    *,
    execution,
    logs,
    skip_logs,
    note_logs,
    translator,
    separator,
    strategy_display_name,
    dry_run_only,
) -> RenderedNotification:
    formatted_logs = "\n".join(f"  - {log}" for log in [*logs, *skip_logs, *note_logs])
    detailed_lines = [translator("rebalance_title")]
    _append_strategy_line(detailed_lines, strategy_display_name=strategy_display_name, translator=translator)
    if dry_run_only:
        detailed_lines.append(translator("dry_run_banner"))
    _append_dashboard_lines(detailed_lines, execution=execution)
    _append_timing_lines(detailed_lines, execution=execution, translator=translator)
    _append_status_lines(
        detailed_lines,
        execution=execution,
        translator=translator,
        signal_key="signal",
    )
    detailed_lines.extend([separator, translator("order_logs_title"), formatted_logs])

    compact_lines = [translator("rebalance_title")]
    _append_strategy_line(compact_lines, strategy_display_name=strategy_display_name, translator=translator)
    if dry_run_only:
        compact_lines.append(translator("dry_run_banner"))
    _append_dashboard_lines(compact_lines, execution=execution)
    _append_timing_lines(compact_lines, execution=execution, translator=translator)
    _append_compact_status_lines(
        compact_lines,
        execution=execution,
        translator=translator,
        signal_key="signal",
    )
    compact_lines.extend([separator, translator("order_logs_title"), formatted_logs])
    return RenderedNotification(
        detailed_text="\n".join(detailed_lines),
        compact_text="\n".join(compact_lines),
    )


def render_heartbeat_notification(
    *,
    execution,
    skip_logs,
    note_logs,
    translator,
    separator,
    strategy_display_name,
    dry_run_only,
) -> RenderedNotification:
    detailed_lines = [translator("heartbeat_title")]
    _append_strategy_line(detailed_lines, strategy_display_name=strategy_display_name, translator=translator)
    if dry_run_only:
        detailed_lines.append(translator("dry_run_banner"))
    _append_dashboard_lines(detailed_lines, execution=execution)
    _append_timing_lines(detailed_lines, execution=execution, translator=translator)
    detailed_lines.append(separator)
    _append_status_lines(
        detailed_lines,
        execution=execution,
        translator=translator,
        signal_key="heartbeat_signal",
    )
    detailed_lines.extend(
        [
            separator,
            translator("no_executable_orders") if (skip_logs or note_logs) else translator("no_trades"),
        ]
    )
    detailed_text = "\n".join(detailed_lines)
    if skip_logs:
        detailed_text += (
            f"\n{separator}\n"
            f"{translator('skipped_actions')}\n"
            + "\n".join(f"  - {log}" for log in skip_logs)
        )
    if note_logs:
        detailed_text += (
            f"\n{separator}\n"
            f"{translator('notes_title')}\n"
            + "\n".join(f"  - {log}" for log in note_logs)
        )

    compact_lines = [translator("heartbeat_title")]
    _append_strategy_line(compact_lines, strategy_display_name=strategy_display_name, translator=translator)
    if dry_run_only:
        compact_lines.append(translator("dry_run_banner"))
    _append_dashboard_lines(compact_lines, execution=execution)
    _append_timing_lines(compact_lines, execution=execution, translator=translator)
    _append_compact_status_lines(
        compact_lines,
        execution=execution,
        translator=translator,
        signal_key="heartbeat_signal",
    )
    compact_lines.append(
        translator("no_executable_orders") if (skip_logs or note_logs) else translator("no_trades")
    )
    if skip_logs:
        compact_lines.extend([separator, translator("skipped_actions")])
        compact_lines.extend(f"  - {log}" for log in skip_logs)
    if note_logs:
        compact_lines.extend([separator, translator("notes_title")])
        compact_lines.extend(f"  - {log}" for log in note_logs)

    return RenderedNotification(
        detailed_text=detailed_text,
        compact_text="\n".join(compact_lines),
    )
