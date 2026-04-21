import sys
import types
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.runtime_reporting_adapters import build_runtime_reporting_adapters


def test_runtime_reporting_adapters_start_run_builds_context_and_report():
    observed = {}

    def fake_report_builder(**kwargs):
        observed["report_builder"] = kwargs
        return {"run_id": kwargs["run_id"]}

    adapters = build_runtime_reporting_adapters(
        platform="longbridge",
        deploy_target="cloud_run",
        service_name="longbridge-platform",
        strategy_profile="soxl_soxx_trend_income",
        strategy_domain="us_equity",
        account_scope="HK",
        account_region="HK",
        project_id="project-1",
        extra_context_fields={"account_prefix": "HK"},
        managed_symbols=("SOXL", "SOXX"),
        account_prefix="HK",
        benchmark_symbol="QQQ",
        strategy_display_name="SOXL/SOXX Semiconductor Trend Income",
        strategy_display_name_localized="SOXL/SOXX 半导体趋势收益",
        dry_run=True,
        signal_effective_after_trading_days=1,
        report_base_dir="/tmp/reports",
        report_gcs_prefix_uri="gs://bucket/reports",
        run_id_builder=lambda: "run-001",
        event_logger=lambda *_args, **_kwargs: {},
        report_builder=fake_report_builder,
        report_persister=lambda *_args, **_kwargs: None,
        printer=lambda *_args, **_kwargs: None,
        clock=lambda: datetime(2026, 4, 21, tzinfo=timezone.utc),
    )

    log_context, report = adapters.start_run()

    assert log_context.run_id == "run-001"
    assert log_context.extra_fields["account_prefix"] == "HK"
    assert observed["report_builder"]["run_id"] == "run-001"
    assert observed["report_builder"]["dry_run"] is True
    summary = observed["report_builder"]["summary"]
    assert summary["managed_symbols"] == ["SOXL", "SOXX"]
    assert summary["account_prefix"] == "HK"
    assert summary["benchmark_symbol"] == "QQQ"
    assert summary["strategy_display_name"] == "SOXL/SOXX Semiconductor Trend Income"
    assert summary["strategy_display_name_localized"] == "SOXL/SOXX 半导体趋势收益"
    assert summary["signal_date"] == "2026-04-21"
    assert summary["effective_date"] == "2026-04-22"
    assert summary["execution_timing_contract"] == "next_trading_day"
    assert summary["signal_effective_after_trading_days"] == 1
    assert summary["execution_calendar_source"] in {
        "pandas_market_calendars",
        "business_day_fallback",
    }
    assert report == {"run_id": "run-001"}


def test_runtime_reporting_adapters_log_and_persist_route_to_dependencies():
    observed = {}

    def fake_report_persister(report, **kwargs):
        observed["persist"] = (report, kwargs)
        return types.SimpleNamespace(local_path="/tmp/report.json", gcs_uri=None)

    adapters = build_runtime_reporting_adapters(
        platform="longbridge",
        deploy_target="cloud_run",
        service_name="longbridge-platform",
        strategy_profile="soxl_soxx_trend_income",
        strategy_domain="us_equity",
        account_scope="HK",
        account_region="HK",
        project_id="project-1",
        managed_symbols=(),
        signal_effective_after_trading_days=1,
        run_id_builder=lambda: "run-001",
        event_logger=lambda context, event, **kwargs: observed.setdefault(
            "event",
            (context.run_id, event, kwargs),
        ),
        report_builder=lambda **kwargs: kwargs,
        report_persister=fake_report_persister,
        printer=lambda line, flush=True: observed.setdefault("printer", (line, flush)),
    )

    log_context, report = adapters.start_run()
    adapters.log_event(log_context, "strategy_cycle_started", message="Starting strategy execution")
    persisted = adapters.persist_execution_report(report)

    assert observed["event"][0] == "run-001"
    assert observed["event"][1] == "strategy_cycle_started"
    assert observed["event"][2]["printer"] is adapters.printer
    assert observed["persist"][1] == {
        "base_dir": None,
        "gcs_prefix_uri": None,
        "gcp_project_id": "project-1",
    }
    assert persisted == "/tmp/report.json"
