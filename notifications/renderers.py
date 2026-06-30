"""Notification rendering helpers for LongBridgePlatform."""

from __future__ import annotations

from collections.abc import Mapping
import re

from notifications.events import RenderedNotification
from quant_platform_kit.common.notification_localization import (
    localize_notification_text as _base_localize_notification_text,
    translator_uses_zh as _base_translator_uses_zh,
)

_PRICE_SOURCE_LABELS = {
    "longbridge_candlesticks": ("LongBridge 日线K线", "LongBridge daily candlesticks"),
    "schwab_daily_history_with_live_quote_overlay": ("Schwab 日线历史", "Schwab daily history"),
    "firstrade_ohlc_with_live_quote_overlay": ("Firstrade OHLC", "Firstrade OHLC"),
    "market_quote": ("实时行情报价", "market quote"),
    "mixed_market_quote_snapshot_close": (
        "实时行情报价 + 快照收盘价回补",
        "market quote + snapshot close fallback",
    ),
    "mixed_market_quote_historical_close": (
        "实时行情报价 + 历史收盘价回补",
        "market quote + historical close fallback",
    ),
    "snapshot_close": ("快照收盘价", "snapshot close"),
    "historical_close": ("历史收盘价", "historical close"),
    "market_data": ("市场数据", "market data"),
}

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

try:
    from quant_platform_kit.common.notification_localization import (
        localize_price_source_label as _shared_localize_price_source_label,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    _shared_localize_price_source_label = None


def _localize_price_source_label(value, *, translator=None, locale=None):
    source = str(value or "").strip()
    use_zh = _base_translator_uses_zh(translator) if translator is not None else str(locale or "").startswith("zh")
    if not source:
        return "未知" if use_zh else "unknown"
    label = _PRICE_SOURCE_LABELS.get(source)
    if label is not None:
        return label[0] if use_zh else label[1]
    if _shared_localize_price_source_label is not None:
        return _shared_localize_price_source_label(source, translator=translator, locale=locale)
    return source.replace("_", " ")

_DETAIL_FIELD_SPLIT_RE = re.compile(r"\s+(?=[^\s=:：]+[=:：])")


def _translator_uses_zh(translator) -> bool:
    return _base_translator_uses_zh(translator)


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
    value = str(contract or "").strip()
    if not value:
        return ""
    if value == "same_trading_day":
        return "当日执行" if _translator_uses_zh(translator) else "same trading day"
    if value == "next_trading_day":
        return "次一交易日执行" if _translator_uses_zh(translator) else "next trading day"
    match = re.fullmatch(r"next_(\d+)_trading_days", value)
    if match:
        count = int(match.group(1))
        if _translator_uses_zh(translator):
            return f"{count}个交易日后执行"
        return f"next {count} trading days"
    return _localize_notification_text(value, translator=translator)


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
    localized_contract = _localize_timing_contract(contract, translator=translator)
    if signal_date and effective_date:
        value = f"{signal_date} -> {effective_date}"
    else:
        value = signal_date or effective_date or localized_contract
    if localized_contract and localized_contract not in value:
        value = f"{value} ({localized_contract})" if value else localized_contract
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


def _format_percent(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _as_float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_percentile(value) -> str:
    try:
        percentile = float(value) * 100
    except (TypeError, ValueError):
        return "p?"
    if float(percentile).is_integer():
        return f"p{int(percentile)}"
    return f"p{percentile:.1f}"


def _format_sample_count(value) -> str:
    try:
        count = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if float(count).is_integer():
        return str(int(count))
    return f"{count:.1f}"


def _present(value) -> bool:
    return value not in (None, "")


def _effective_volatility_delever_threshold(execution, *, prefix: str):
    mode = str(execution.get(f"{prefix}_threshold_mode") or "").strip().lower()
    dynamic_threshold = execution.get(f"{prefix}_dynamic_threshold")
    if mode == "rolling_percentile" and _present(dynamic_threshold):
        return dynamic_threshold
    return execution.get(f"{prefix}_threshold")


def _format_volatility_delever_threshold_detail(execution, *, prefix: str, translator) -> str:
    mode = str(execution.get(f"{prefix}_threshold_mode") or "").strip().lower()
    fixed_threshold = execution.get(f"{prefix}_threshold")
    dynamic_threshold = execution.get(f"{prefix}_dynamic_threshold")
    if mode == "rolling_percentile":
        kwargs = {
            "percentile": _format_percentile(execution.get(f"{prefix}_dynamic_percentile")),
            "lookback": _format_sample_count(execution.get(f"{prefix}_dynamic_lookback")),
            "min_periods": _format_sample_count(execution.get(f"{prefix}_dynamic_min_periods")),
            "sample_count": _format_sample_count(execution.get(f"{prefix}_dynamic_sample_count")),
            "floor": _format_percent(execution.get(f"{prefix}_dynamic_floor")),
            "cap": _format_percent(execution.get(f"{prefix}_dynamic_cap")),
            "fixed_threshold": _format_percent(fixed_threshold),
        }
        if _present(dynamic_threshold):
            return translator("blend_gate_volatility_threshold_detail_dynamic", **kwargs)
        return translator("blend_gate_volatility_threshold_detail_dynamic_fallback", **kwargs)
    return translator(
        "blend_gate_volatility_threshold_detail_fixed",
        threshold=_format_percent(fixed_threshold),
    )


def _format_tqqq_volatility_delever_allocation_detail(
    execution,
    *,
    prefix: str,
    redirect_symbol: str,
    translator,
) -> str:
    retained_ratio = _as_float_or_none(execution.get(f"{prefix}_retained_ratio"))
    redirected_ratio = _as_float_or_none(execution.get(f"{prefix}_redirected_ratio"))
    if retained_ratio is None:
        retained_ratio = _as_float_or_none(execution.get(f"{prefix}_retention_ratio"))
    if redirected_ratio is None and retained_ratio is not None:
        redirected_ratio = max(0.0, min(1.0, 1.0 - retained_ratio))
    return translator(
        "tqqq_volatility_delever_allocation_detail",
        retained_ratio=_format_percent(retained_ratio),
        redirected_ratio=_format_percent(redirected_ratio),
        redirect_symbol=redirect_symbol or "QQQ",
    )


def _build_risk_control_lines(execution, *, translator):
    if _is_truthy(execution.get("dual_drive_volatility_delever_applied")):
        redirect_symbol = str(execution.get("dual_drive_volatility_delever_redirect_symbol") or "QQQ").strip().upper()
        window = str(execution.get("dual_drive_volatility_delever_window") or "5").strip()
        threshold = _effective_volatility_delever_threshold(
            execution,
            prefix="dual_drive_volatility_delever",
        )
        threshold_detail = _format_volatility_delever_threshold_detail(
            execution,
            prefix="dual_drive_volatility_delever",
            translator=translator,
        )
        allocation_detail = _format_tqqq_volatility_delever_allocation_detail(
            execution,
            prefix="dual_drive_volatility_delever",
            redirect_symbol=redirect_symbol or "QQQ",
            translator=translator,
        )
        if str(execution.get("dual_drive_volatility_delever_trigger_reason") or "").strip() == "hysteresis_hold":
            return [
                translator(
                    "risk_control_tqqq_volatility_delever_hysteresis_dynamic",
                    window=window,
                    volatility=_format_percent(execution.get("dual_drive_volatility_delever_metric")),
                    exit_threshold=_format_percent(execution.get("dual_drive_volatility_delever_exit_threshold")),
                    threshold=_format_percent(threshold),
                    threshold_detail=threshold_detail,
                    source_symbol="TQQQ",
                    redirect_symbol=redirect_symbol or "QQQ",
                    allocation_detail=allocation_detail,
                )
            ]
        return [
            translator(
                "risk_control_tqqq_volatility_delever_applied_dynamic",
                window=window,
                volatility=_format_percent(execution.get("dual_drive_volatility_delever_metric")),
                threshold=_format_percent(threshold),
                threshold_detail=threshold_detail,
                source_symbol="TQQQ",
                redirect_symbol=redirect_symbol or "QQQ",
                allocation_detail=allocation_detail,
            )
        ]
    return []


def _relabel_dashboard_buying_power(text: str, *, cash_only_execution: bool, translator) -> str:
    value = str(text or "")
    if cash_only_execution:
        target = translator("buying_power")
        for source in ("Buying power", "购买力"):
            if source != target:
                value = value.replace(source, target)
        return value
    target = translator("buying_power_margin")
    for source in ("Available cash", "可用现金"):
        if source != target:
            value = value.replace(source, target)
    return value


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
        result = _relabel_dashboard_buying_power(
            result,
            cash_only_execution=cash_only_execution,
            translator=translator,
        )
    return result


def _append_dashboard_block(lines, *, execution, separator, translator) -> None:
    cash_only_execution = bool(execution.get("cash_only_execution", True))
    dashboard_text = _format_dashboard_text(
        execution.get("dashboard_text"),
        translator=translator,
        cash_only_execution=cash_only_execution,
    )
    if dashboard_text:
        lines.append(separator)
        lines.extend(dashboard_text.splitlines())


def _append_timing_lines(lines, *, execution, translator) -> None:
    lines.extend(_build_timing_audit_lines(execution, translator=translator))


def _format_signal_snapshot_line(snapshot, *, translator) -> str:
    if not isinstance(snapshot, Mapping):
        return ""
    market_date = str(snapshot.get("market_date") or snapshot.get("signal_as_of") or "").strip()
    source = str(snapshot.get("latest_price_source") or "").strip()
    warning = snapshot.get("data_freshness_warning")
    if not market_date and not source and warning in (None, "", False):
        return ""
    if _translator_uses_zh(translator):
        parts = [
            f"日期 {market_date or '未知'}",
            f"数据源 {_localize_price_source_label(source, translator=translator)}",
        ]
        if warning not in (None, "", False):
            parts.append(f"提示 {_localize_notification_text(warning, translator=translator)}")
        return "🧾 信号快照: " + " | ".join(parts)
    parts = [
        f"date {market_date or 'unknown'}",
        f"source {_localize_price_source_label(source, translator=translator)}",
    ]
    if warning not in (None, "", False):
        parts.append(f"warning {warning}")
    return "🧾 Signal snapshot: " + " | ".join(parts)


def _append_signal_snapshot_line(lines, *, execution, translator) -> None:
    line = _format_signal_snapshot_line(execution.get("signal_snapshot"), translator=translator)
    if line:
        lines.append(line)


def _is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


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
    compact_lines.append(separator)
    _append_dashboard_block(compact_lines, execution=execution, separator=separator, translator=translator)
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
    _append_dashboard_block(compact_lines, execution=execution, separator=separator, translator=translator)
    _append_timing_lines(compact_lines, execution=execution, translator=translator)
    _append_signal_snapshot_line(compact_lines, execution=execution, translator=translator)
    _append_source_input_line(compact_lines, execution=execution, translator=translator)
    _append_compact_status_lines(
        compact_lines,
        execution=execution,
        translator=translator,
        signal_key="heartbeat_signal",
    )
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
