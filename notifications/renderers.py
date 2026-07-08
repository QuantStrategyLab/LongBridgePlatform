"""Notification rendering helpers for LongBridgePlatform."""

from __future__ import annotations

from collections.abc import Mapping

from notifications.events import RenderedNotification
from quant_platform_kit.common.notification_localization import (
    localize_notification_text as _base_localize_notification_text,
)
from quant_platform_kit.notifications.renderer_base import (
    as_float_or_none as _as_float_or_none,
    build_timing_audit_lines as _build_timing_audit_lines_shared,
    build_tqqq_risk_control_lines as _build_tqqq_risk_control_lines_shared,
    effective_volatility_delever_threshold as _effective_volatility_delever_threshold,
    format_percent as _format_percent,
    format_percentile as _format_percentile,
    format_sample_count as _format_sample_count,
    format_signal_snapshot_line as _format_signal_snapshot_line_shared,
    format_tqqq_volatility_delever_allocation_detail as _format_tqqq_volatility_delever_allocation_detail,
    format_volatility_delever_threshold_detail as _format_volatility_delever_threshold_detail,
    is_compact_dashboard_audit_line as _is_compact_dashboard_audit_line,
    is_truthy,
    localize_price_source_label as _localize_price_source_label,
    localize_timing_contract as _localize_timing_contract_shared,
    present as _present,
    relabel_dashboard_cash_labels as _relabel_dashboard_cash_labels_shared,
    split_detail_segment as _split_detail_segment,
    split_labeled_text as _split_labeled_text,
    translator_uses_zh as _translator_uses_zh,
)

_LONG_BRIDGE_ZH_NOTIFICATION_REPLACEMENTS = (
    ("regime=hard_defense", "市场阶段=强防御"),
    ("regime=soft_defense", "市场阶段=软防御"),
    ("regime=risk_on", "市场阶段=进攻"),
    ("benchmark_trend=down", "基准趋势=向下"),
    ("benchmark_trend=up", "基准趋势=向上"),
    ("benchmark=down", "基准趋势=向下"),
    ("benchmark=up", "基准趋势=向上"),
    ("benchmark_risk_off=True", "基准避险=开启"),
    ("benchmark_risk_off=False", "基准避险=关闭"),
    ("breadth=", "市场宽度="),
    ("target_stock=", "目标股票仓位="),
    ("realized_stock=", "实际股票仓位="),
    ("stock_exposure=", "股票目标仓位="),
    ("safe_haven=", "避险仓位="),
    ("selected=", "入选标的数="),
    ("top=", "前排标的="),
    ("sentiment=", "市场情绪="),
    ("state=", "状态="),
    ("candidate_count=", "候选数="),
    ("selected_count=", "入选数="),
    ("momentum=", "动量="),
    ("trend=", "趋势="),
    ("gross=", "总仓位="),
    ("cash=", "现金仓位="),
    ("target_vol=", "目标波动率="),
    ("top_n=", "前N标的="),
    ("partial_history_refresh", "部分行情刷新"),
    ("full_history_refresh", "完整行情刷新"),
    ("universe_fallback", "股票池复用"),
)

_SOURCE_INPUT_STATUS_LABELS = {
    "partial_history_refresh": ("部分行情刷新", "partial history refresh"),
    "full_history_refresh": ("完整行情刷新", "full history refresh"),
    "universe_fallback": ("股票池复用", "universe fallback"),
}



def _localize_notification_text(text, *, translator):
    try:
        return _base_localize_notification_text(
            text,
            translator=translator,
            extra_replacements=_LONG_BRIDGE_ZH_NOTIFICATION_REPLACEMENTS,
        )
    except TypeError:  # pragma: no cover - compatibility with older shared wheels
        localized = _base_localize_notification_text(text, translator=translator)
        if not _translator_uses_zh(translator):
            return localized
        for source, target in _LONG_BRIDGE_ZH_NOTIFICATION_REPLACEMENTS:
            localized = localized.replace(source, target)
        return localized


def _localize_source_input_status(status, *, translator) -> str:
    value = str(status or "").strip()
    if not value:
        return ""
    label = _SOURCE_INPUT_STATUS_LABELS.get(value)
    if label is not None:
        return label[0] if _translator_uses_zh(translator) else label[1]
    if _translator_uses_zh(translator):
        return _localize_notification_text(value, translator=translator)
    return value.replace("_", " ")


def _localize_timing_contract(contract: str, *, translator) -> str:
    """Thin wrapper — adds LB-specific notification localisation fallback."""
    result = _localize_timing_contract_shared(contract, translator=translator)
    if result and result not in ("当日执行", "same trading day", "次一交易日执行", "next trading day"):
        if "个交易日后执行" not in result and "next " not in result:
            return _localize_notification_text(result, translator=translator)
    return result


def _append_labeled_text(lines, template_key, value, *, translator, value_key):
    parts = _split_labeled_text(value)
    if not parts:
        return
    lines.append(translator(template_key, **{value_key: parts[0]}))
    lines.extend(f"  - {part}" for part in parts[1:])


def _build_timing_audit_lines(execution, *, translator):
    return _build_timing_audit_lines_shared(execution, translator=translator)


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


def _build_risk_control_lines(execution, *, translator):
    return _build_tqqq_risk_control_lines_shared(
        execution if isinstance(execution, Mapping) else {},
        translator=translator,
    )


def _format_dashboard_text(text, *, translator=None, cash_only_execution: bool = True) -> str:
    lines = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if translator is not None and _translator_uses_zh(translator):
            line = _localize_notification_text(line, translator=translator)
        lines.append(line)
    result = "\n".join(lines)
    if translator is not None:
        result = _relabel_dashboard_cash_labels_shared(
            result,
            cash_only_execution=cash_only_execution,
            translator=translator,
        )
    return result


def _append_dashboard_block(lines, *, execution, separator, translator, compact: bool = False) -> None:
    cash_only_execution = bool(execution.get("cash_only_execution", True))
    dashboard_text = _format_dashboard_text(
        execution.get("dashboard_text"),
        translator=translator,
        cash_only_execution=cash_only_execution,
    )
    dashboard_lines = [
        line for line in dashboard_text.splitlines()
        if not (compact and _is_compact_dashboard_audit_line(line))
    ]
    if dashboard_lines:
        lines.append(separator)
        lines.extend(dashboard_lines)


def _append_timing_lines(lines, *, execution, translator) -> None:
    lines.extend(_build_timing_audit_lines(execution, translator=translator))


def _format_signal_snapshot_line(snapshot, *, translator) -> str:
    return _format_signal_snapshot_line_shared(
        snapshot,
        translator=translator,
        localize_text=_localize_notification_text,
    )


def _append_signal_snapshot_line(lines, *, execution, translator) -> None:
    line = _format_signal_snapshot_line(execution.get("signal_snapshot"), translator=translator)
    if line:
        lines.append(line)


def _is_truthy(value) -> bool:
    return is_truthy(value)


def _format_source_input_line(snapshot, *, translator) -> str:
    if not isinstance(snapshot, Mapping):
        return ""
    price_as_of = str(snapshot.get("price_as_of") or "").strip()
    universe_as_of = str(snapshot.get("universe_as_of") or "").strip()
    status = str(snapshot.get("source_input_status") or "").strip()
    localized_status = _localize_source_input_status(status, translator=translator)
    fallback_used = _is_truthy(snapshot.get("source_input_fallback_used"))
    fallback_streak = snapshot.get("source_input_fallback_streak")
    if not price_as_of and not universe_as_of and not status and not fallback_used:
        return ""
    if _translator_uses_zh(translator):
        parts = []
        if price_as_of:
            parts.append(f"价格 {price_as_of}")
        if universe_as_of:
            parts.append(f"股票池 {universe_as_of}")
        if fallback_used:
            fallback_text = "股票池复用"
            if fallback_streak not in (None, "", 0, "0"):
                fallback_text += f" 连续{fallback_streak}次"
            parts.append(fallback_text)
        elif localized_status:
            parts.append(f"状态 {localized_status}")
        return "🧩 输入状态: " + " | ".join(parts)
    parts = []
    if price_as_of:
        parts.append(f"price {price_as_of}")
    if universe_as_of:
        parts.append(f"universe {universe_as_of}")
    if fallback_used:
        fallback_text = "universe fallback"
        if fallback_streak not in (None, "", 0, "0"):
            fallback_text += f" streak={fallback_streak}"
        parts.append(fallback_text)
    elif localized_status:
        parts.append(f"status {localized_status}")
    return "🧩 Inputs: " + " | ".join(parts)


def _append_source_input_line(lines, *, execution, translator) -> None:
    line = _format_source_input_line(execution.get("signal_snapshot"), translator=translator)
    if line:
        lines.append(line)


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

    lines.extend(_build_risk_control_lines(execution, translator=translator))
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

    lines.extend(_build_risk_control_lines(execution, translator=translator))

def _append_strategy_line(lines, *, strategy_display_name, translator):
    name = str(strategy_display_name or "").strip()
    if name:
        lines.append(translator("strategy_label", name=name))


def _append_extra_notification_lines(lines, extra_notification_lines) -> None:
    for line in extra_notification_lines or ():
        text = str(line or "").strip()
        if text:
            lines.append(text)


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
    extra_notification_lines=(),
    title_key="rebalance_title",
) -> RenderedNotification:
    formatted_logs = "\n".join(f"  - {log}" for log in [*logs, *skip_logs, *note_logs])
    detailed_lines = [translator(title_key or "rebalance_title")]
    _append_strategy_line(detailed_lines, strategy_display_name=strategy_display_name, translator=translator)
    if dry_run_only:
        detailed_lines.append(translator("dry_run_banner"))
    _append_extra_notification_lines(detailed_lines, extra_notification_lines)
    _append_dashboard_block(detailed_lines, execution=execution, separator=separator, translator=translator)
    _append_timing_lines(detailed_lines, execution=execution, translator=translator)
    _append_signal_snapshot_line(detailed_lines, execution=execution, translator=translator)
    _append_source_input_line(detailed_lines, execution=execution, translator=translator)
    _append_status_lines(
        detailed_lines,
        execution=execution,
        translator=translator,
        signal_key="signal",
    )
    detailed_lines.extend([separator, translator("order_logs_title"), formatted_logs])

    compact_lines = [translator(title_key or "rebalance_title")]
    _append_strategy_line(compact_lines, strategy_display_name=strategy_display_name, translator=translator)
    if dry_run_only:
        compact_lines.append(translator("dry_run_banner"))
    _append_extra_notification_lines(compact_lines, extra_notification_lines)
    _append_dashboard_block(compact_lines, execution=execution, separator=separator, translator=translator, compact=True)
    compact_lines.append(separator)
    compact_lines.append(formatted_logs)
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
    extra_notification_lines=(),
    title_key="heartbeat_title",
) -> RenderedNotification:
    detailed_lines = [translator(title_key or "heartbeat_title")]
    _append_strategy_line(detailed_lines, strategy_display_name=strategy_display_name, translator=translator)
    if dry_run_only:
        detailed_lines.append(translator("dry_run_banner"))
    _append_extra_notification_lines(detailed_lines, extra_notification_lines)
    _append_dashboard_block(detailed_lines, execution=execution, separator=separator, translator=translator)
    _append_timing_lines(detailed_lines, execution=execution, translator=translator)
    _append_signal_snapshot_line(detailed_lines, execution=execution, translator=translator)
    _append_source_input_line(detailed_lines, execution=execution, translator=translator)
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

    compact_lines = [translator(title_key or "heartbeat_title")]
    _append_strategy_line(compact_lines, strategy_display_name=strategy_display_name, translator=translator)
    if dry_run_only:
        compact_lines.append(translator("dry_run_banner"))
    _append_extra_notification_lines(compact_lines, extra_notification_lines)
    _append_dashboard_block(compact_lines, execution=execution, separator=separator, translator=translator, compact=True)
    compact_lines.extend(
        [
            separator,
            translator("no_executable_orders") if (skip_logs or note_logs) else translator("no_trades"),
        ]
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
