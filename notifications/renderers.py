"""Notification rendering helpers for LongBridgePlatform."""

from __future__ import annotations

import re

from notifications.events import RenderedNotification


_ZH_REASON_REPLACEMENTS = (
    ("feature snapshot guard blocked execution", "特征快照校验阻止执行"),
    ("feature snapshot required", "需要特征快照"),
    ("feature snapshot compute failed", "特征快照计算失败"),
    ("feature_snapshot_download_failed", "特征快照下载失败"),
    ("feature_snapshot_compute_failed", "特征快照计算失败"),
    ("feature_snapshot_path_missing", "缺少特征快照路径"),
    ("feature_snapshot_missing", "特征快照不存在"),
    ("feature_snapshot_stale", "特征快照过旧"),
    ("feature_snapshot_manifest_missing", "缺少快照清单"),
    ("feature_snapshot_profile_mismatch", "快照策略名不匹配"),
    ("feature_snapshot_config_name_mismatch", "快照配置名不匹配"),
    ("feature_snapshot_config_path_mismatch", "快照配置路径不匹配"),
    ("feature_snapshot_contract_version_mismatch", "快照契约版本不匹配"),
    ("soxl_soxx_trend_income", "SOXL/SOXX 半导体趋势收益"),
    ("tqqq_growth_income", "TQQQ 增长收益"),
    ("global_etf_rotation", "全球 ETF 轮动"),
    ("russell_1000_multi_factor_defensive", "罗素1000多因子"),
    ("tech_communication_pullback_enhancement", "科技通信回调增强"),
    ("qqq_tech_enhancement", "科技通信回调增强"),
    ("mega_cap_leader_rotation_aggressive", "Mega Cap 激进龙头轮动"),
    ("mega_cap_leader_rotation_dynamic_top20", "Mega Cap 动态 Top20 龙头轮动"),
    ("mega_cap_leader_rotation_top50_balanced", "Mega Cap Top50 平衡龙头轮动"),
    ("dynamic_mega_leveraged_pullback", "Mega Cap 2x 回调策略"),
    ("outside_monthly_execution_window", "当前不在月度执行窗口"),
    ("no_execution_window_after_snapshot", "快照后没有可用执行窗口"),
    ("no-op", "不执行"),
    ("monthly snapshot cadence", "月度快照节奏"),
    ("waiting inside execution window", "等待进入执行窗口"),
    ("small_account_warning=true", "小账户提示=是"),
    ("portfolio_equity=", "净值="),
    ("min_recommended_equity=", "建议最低净值="),
    (
        "integer_shares_min_position_value_may_prevent_backtest_replication",
        "整数股和最小仓位限制可能导致实盘无法完全复现回测",
    ),
    (
        "integer-share minimum position sizing may prevent backtest replication",
        "整数股和最小仓位限制可能导致实盘无法完全复现回测",
    ),
    ("small account warning: portfolio equity", "小账户提示：净值"),
    ("small account warning", "小账户提示"),
    ("is below the recommended", "低于建议"),
    ("is below recommended", "低于建议"),
    ("snapshot_as_of=", "快照日期="),
    ("snapshot=", "快照日期="),
    ("allowed=", "允许日期="),
    ("<unknown>", "未知"),
    ("<none>", "无"),
    ("RISK-ON", "风险开启"),
    ("DE-LEVER", "降杠杆"),
    ("regime=hard_defense", "市场阶段=强防御"),
    ("regime=soft_defense", "市场阶段=软防御"),
    ("regime=risk_on", "市场阶段=进攻"),
    ("benchmark_trend=down", "基准趋势=向下"),
    ("benchmark_trend=up", "基准趋势=向上"),
    ("benchmark=down", "基准趋势=向下"),
    ("benchmark=up", "基准趋势=向上"),
    ("breadth=", "市场宽度="),
    ("target_stock=", "目标股票仓位="),
    ("realized_stock=", "实际股票仓位="),
    ("stock_exposure=", "股票目标仓位="),
    ("safe_haven=", "避险仓位="),
    ("selected=", "入选标的数="),
    ("top=", "前排标的="),
    ("no_selection", "无入选标的"),
    ("outside_execution_window", "当前不在执行窗口"),
    ("insufficient_buying_power", "购买力不足"),
    ("missing_price", "缺少报价"),
    ("no_equity", "无净值"),
    ("fail_closed", "关闭执行"),
    ("reason=", "原因="),
)
_DETAIL_FIELD_SPLIT_RE = re.compile(r"\s+(?=[^\s=:：]+[=:：])")


def _translator_uses_zh(translator) -> bool:
    sample = str(translator("no_trades"))
    return any("\u4e00" <= ch <= "\u9fff" for ch in sample)


def _localize_notification_text(text, *, translator):
    value = str(text or "").strip()
    if not value or not _translator_uses_zh(translator):
        return value
    localized = value
    for source, target in _ZH_REASON_REPLACEMENTS:
        localized = localized.replace(source, target)
    return localized


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
