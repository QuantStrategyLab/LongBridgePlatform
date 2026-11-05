"""Exercise the installed UES package with synthetic facts, never a broker."""
import json
import tomllib
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from importlib import metadata
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


def test_installed_ues_matches_all_source_declarations():
    root = Path(__file__).resolve().parents[1]
    lock = tomllib.loads((root / "uv.lock").read_text())
    package, = [p for p in lock["package"] if p["name"] == "us-equity-strategies"]
    revision = package["source"]["git"].rsplit("#", 1)[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    requirement, = [d for d in project["project"]["dependencies"]
                    if d.startswith("us-equity-strategies @")]
    assert requirement.endswith("@" + revision)
    assert tomllib.loads((root / "qsl.toml").read_text())["qsl"]["requires"]["us_equity_strategies"] == revision
    installed = json.loads(metadata.distribution("us-equity-strategies").read_text("direct_url.json"))
    assert installed["vcs_info"]["commit_id"] == revision


@pytest.fixture(params=[("nasdaq100_buy_and_hold", "QQQ"),
                       ("sp500_buy_and_hold", "SPY")])
def installed_dca_case(request):
    from us_equity_strategies import buy_and_hold as p1
    from us_equity_strategies import plain_dca as api

    profile, symbol = request.param
    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 3, 11, tzinfo=zone)
    fresh = {"observed_at": now, "valid_until": now + timedelta(minutes=1)}
    instrument = p1.InstrumentBinding(symbol, symbol, "fake-contract", "XNAS", "USD",
                                      D(1), D(1), True)
    binding = p1.PlanBinding(profile, instrument, "fake-paper-account", "fake-mandate",
                            "fake-release", "fake-owner", "paper")
    mandate = p1.Mandate(binding, now-timedelta(days=1), now+timedelta(days=1),
                         D(100), D(10000), "CAPITAL_PRESERVATION", True)
    plan = api.DcaPlan(mandate, "fake-plan", 1, api.ScheduleSpec(anchor=3),
                       D(100), D(100), D(200))
    start, end = date(2026, 1, 1), date(2027, 2, 1)
    days = (start + timedelta(days=i) for i in range((end-start).days+1))
    sessions = tuple(api.ExchangeSession(day, datetime.combine(day, time(9, 30), zone),
                                         datetime.combine(day, time(16), zone))
                     for day in days if day.weekday() < 5)
    calendar = api.ExchangeCalendar("fake-calendar", "XNAS", "America/New_York", start,
                                    end, now-timedelta(days=1), now+timedelta(days=1),
                                    True, sessions, zone)
    period = api.PeriodFacts(binding, "fake-plan", 1, "2026-11", **fresh, records=(),
                             funded_carry=D(0), carry_verified=True,
                             year_new_headroom=D(1000), lifetime_new_headroom=D(1000),
                             year_spend_headroom=D(1000), lifetime_spend_headroom=D(1000))
    facts = p1.PortfolioFacts(binding, **fresh, reconciled=True, exclusive_ownership=True,
                              unrelated_exposure=False, holdings_adopted=True,
                              held_quantity=D(0), settled_cash_after_reservations=D(1000),
                              account_risk_capital=D(20000), risk_headroom=D(1000),
                              risk_approved=True, principal_spent_and_reserved=D(0),
                              contribution_ref="fake-contribution", allowance_revision=1)
    quote = p1.Quote(instrument, **fresh, mark_price=D(70), limit_price=D(70), fee_buffer=D(0))
    capabilities = p1.Capabilities(binding, verified=True, fractional_verified=False)
    return {"plan": plan, "calendar": calendar, "period": period, "facts": facts, "quote": quote,
                "capabilities": capabilities, "now": now}


def test_installed_api_preserves_final_tranche_and_preview_only(installed_dca_case):
    from us_equity_strategies.plain_dca import PeriodPreview, evaluate_dca_period

    result = evaluate_dca_period(**installed_dca_case)
    assert isinstance(result, PeriodPreview)
    assert result.status == "DUE"
    assert result.new_tranche == D(100)  # Already approved; never apply the tier twice.
    assert result.period_key == "2026-11"
    assert result.no_order is True and result.no_execute is True
    assert result.preview_only is True and result.live_ready is False
    assert result.decision.budgets == ()
    assert evaluate_dca_period(**installed_dca_case) == result


def test_adapter_consumes_installed_api_and_rejects_sg(installed_dca_case, monkeypatch):
    from us_equity_strategies.plain_dca import evaluate_dca_period

    from application.plain_dca_paper_adapter import (
        PlainDcaPaperError,
        consume_plain_dca_preview,
    )
    from types import SimpleNamespace

    service = "fake-paper-service"
    paper = SimpleNamespace(id="paper", environment="longbridge-paper", mode="paper", service=service)
    monkeypatch.setattr("application.plain_dca_paper_adapter.load_runtime_target_manifest",
                        lambda: SimpleNamespace(targets=(paper,)))
    now = installed_dca_case["now"]
    readback = {"observed_at": now, "valid_until": now+timedelta(minutes=1),
                    "target_id": "paper", "environment": "longbridge-paper",
                    "live_enabled": False, "kelly_enabled": False, "stop_loss_enabled": False,
                    "protected_env": {
                        "CLOUD_RUN_SERVICE": service, "RUNTIME_TARGET_ENABLED": "false",
                        "LONGBRIDGE_DRY_RUN_ONLY": "false",
                        "LONGBRIDGE_PHYSICAL_ACCOUNT_ID": "fake-paper-account",
                        "RUNTIME_TARGET_JSON": json.dumps({
                            "platform_id": "longbridge", "service_name": service,
                            "execution_environment": "paper", "execution_mode": "live", "dry_run_only": False,
                            "account_scope": "PAPER", "account_selector": ["PAPER"], "deployment_selector": "PAPER"}),
                    }}
    assert consume_plain_dca_preview(read_binding=lambda: readback, **installed_dca_case) == evaluate_dca_period(**installed_dca_case)
    with pytest.raises(PlainDcaPaperError, match="paper_target_environment_required"):
        consume_plain_dca_preview(read_binding=lambda: {**readback, "environment": "longbridge-sg"},
                                  **installed_dca_case)


def test_installed_api_rejects_missing_facts(installed_dca_case):
    from us_equity_strategies.plain_dca import evaluate_dca_period

    result = evaluate_dca_period(**{**installed_dca_case, "facts": None})
    assert result.status == "BLOCKED"
    assert result.decision.positions == result.decision.budgets == ()
    assert result.no_order and result.no_execute and result.preview_only
    assert result.live_ready is False
