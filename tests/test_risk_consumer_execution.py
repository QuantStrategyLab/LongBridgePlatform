"""Synthetic QPK producer -> mapper -> claim -> execution-port regressions."""
from dataclasses import replace
from datetime import datetime, timezone
import os
import socket

import pytest

from application import rebalance_service
from application.account_new_risk_gate_support import set_cycle_snapshot
from application.execution_state import ExecutionMarkerStore
from application.durable_execution_commands import build_live_execution_command
from application.runtime_dependencies import LongBridgeRebalanceConfig, LongBridgeRebalanceRuntime
from application.paper_strategy_risk_state import record_paper_strategy_risk_state_transition
from decision_mapper import map_strategy_decision_to_plan
from notifications.telegram import build_translator
from quant_platform_kit.common.models import ExecutionReport, PortfolioSnapshot, Position, QuoteSnapshot
from quant_platform_kit.common.execution_commands import ExecutionCommandState, ExecutionCommandStore
from quant_platform_kit.common.port_adapters import (
    CallableExecutionPort, CallableMarketDataPort, CallableNotificationPort, CallablePortfolioPort,
)
from quant_platform_kit.common.strategy_contracts import PositionTarget, StrategyDecision
from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateChainError, StrategyRiskStateIdentity, StrategyRiskStateStore,
    build_strategy_risk_state_transition,
)
from quant_platform_kit.risk.gate import apply_risk_gate, assess_with_evidence


@pytest.fixture(autouse=True)
def offline_ports(monkeypatch, tmp_path):
    """Fail even when a production helper swallows an external-call exception."""
    import google.auth
    import google.cloud.secretmanager
    import google.cloud.storage
    import longport.openapi
    import requests

    attempts = []

    def forbidden(*_args, **_kwargs):
        attempts.append(True)
        raise AssertionError("synthetic test attempted an external operation")

    # Test the interception itself without connecting or resolving a host.
    monkeypatch.setattr(socket, "create_connection", forbidden)
    with pytest.raises(AssertionError):
        socket.create_connection(("example.invalid", 443))
    attempts.clear()
    for attr in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, attr, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(google.auth, "default", forbidden)
    monkeypatch.setattr(google.cloud.storage, "Client", forbidden)
    monkeypatch.setattr(google.cloud.secretmanager, "SecretManagerServiceClient", forbidden)
    monkeypatch.setattr(longport.openapi, "TradeContext", forbidden)
    monkeypatch.setattr(longport.openapi, "QuoteContext", forbidden)
    for key in tuple(os.environ):
        monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("ACCOUNT_NEW_RISK_GATE", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-disabled")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "synthetic-disabled")
    monkeypatch.setattr(
        "application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "application.execution_service.maybe_publish_attention_for_admission",
        lambda *_args, **_kwargs: {"sent": 0, "skipped": 0, "failed": 0},
    )
    monkeypatch.setattr(rebalance_service, "try_record_platform_execution", lambda *_a, **_k: None)
    yield
    set_cycle_snapshot(None)
    assert not attempts


class SyntheticCycle:
    def __init__(self, tmp_path, *, positions=(), cash=500.0, targets=None, projection=None):
        self.state_dir = tmp_path / "execution"
        self.store = ExecutionMarkerStore(local_dir=self.state_dir)
        self.orders = []
        self.issues = []
        self.snapshot = PortfolioSnapshot(
            as_of=datetime(2026, 4, 21, tzinfo=timezone.utc),
            total_equity=500.0, cash_balance=cash, buying_power=cash,
            positions=positions, metadata={"cash_by_currency": {"USD": cash}},
        )
        self.targets = {"SOXL": 400.0} if targets is None else targets
        self.projection = dict(projection or {})
        self.decision_transform = lambda decision: decision
        self.plan_transform = lambda plan: plan
        self.refresh_transform = lambda plan: plan
        self.resolve_count = 0
        self.config = LongBridgeRebalanceConfig(
            limit_sell_discount=.995, limit_buy_premium=1., separator="-",
            translator=build_translator("en"), with_prefix=lambda message: message,
            strategy_profile="soxl_soxx_trend_income", notify_no_trade_cycles=False,
            execution_dedup_enabled=True, execution_state_store=self.store,
            execution_state_account_scope="PAPER", physical_account_id="synthetic-lb-account",
        )
        self.runtime = LongBridgeRebalanceRuntime(
            bootstrap=lambda: (None, None, {}), resolve_rebalance_plan=self.resolve,
            portfolio_port_factory=lambda *_args: CallablePortfolioPort(lambda: self.snapshot),
            market_data_port_factory=lambda *_args: CallableMarketDataPort(
                quote_loader=lambda symbol: QuoteSnapshot(symbol=symbol, as_of="2026-04-21", last_price=100.),
            ),
            estimate_max_purchase_quantity=lambda *_args, **_kwargs: 5,
            execution_port_factory=lambda *_args: CallableExecutionPort(self.submit),
            notifications=CallableNotificationPort(lambda _message: None),
            notify_issue=lambda *args: self.issues.append(args),
        )

    def resolve(self, *, snapshot, **_kwargs):
        self.resolve_count += 1
        raw = StrategyDecision(
            positions=tuple(PositionTarget(symbol=symbol, target_value=value) for symbol, value in self.targets.items()),
            diagnostics={"trade_threshold_value": 10., "current_min_trade": 10.,
                         "signal_date": "2026-04-21", "effective_date": "2026-04-21"},
        )
        gated = apply_risk_gate(raw, portfolio_snapshot=snapshot)
        decision = self.decision_transform(gated)
        plan = map_strategy_decision_to_plan(decision, snapshot=snapshot, strategy_profile=self.config.strategy_profile)
        plan["portfolio"]["account_new_risk_snapshot"] = dict(self.projection)
        plan = self.plan_transform(plan)
        return self.refresh_transform(plan) if self.resolve_count > 1 else plan

    def submit(self, intent):
        self.orders.append(intent)
        if intent.side == "sell":
            symbol = intent.symbol.removesuffix(".US")
            self.snapshot = replace(
                self.snapshot, cash_balance=500., buying_power=500.,
                positions=tuple(position for position in self.snapshot.positions if position.symbol != symbol),
                metadata={"cash_by_currency": {"USD": 500.}},
            )
        return ExecutionReport(symbol=intent.symbol, side=intent.side, quantity=intent.quantity,
                               status="filled", filled_quantity=intent.quantity,
                               average_fill_price=100., broker_order_id=f"synthetic-{len(self.orders)}")

    def run(self):
        return rebalance_service.run_strategy(runtime=self.runtime, config=self.config)

    def state_files(self):
        return {str(path.relative_to(self.state_dir)): path.read_bytes() for path in self.state_dir.rglob("*") if path.is_file()}


@pytest.mark.parametrize("action", [None, "", "UNKNOWN", "reject", True, {"outcome": "APPROVE"}])
def test_missing_or_invalid_final_approval_has_zero_claims_and_submissions(tmp_path, action):
    cycle = SyntheticCycle(tmp_path)

    def remove_approval(decision):
        diagnostics = {**decision.diagnostics}
        diagnostics.pop("risk_gate", None)
        if action is not None:
            diagnostics["risk_gate"] = action
        return replace(decision, diagnostics=diagnostics)

    cycle.decision_transform = remove_approval
    result = cycle.run()
    assert result.execution["no_execute"]
    assert not cycle.orders
    assert not cycle.state_files()


@pytest.mark.parametrize("conflict", [
    {"no_execute": True}, {"risk_flags": ("rejected:equity",)}, {"risk_gate": "REJECT"},
    {"no_order": True}, {"execution_authorized": False},
])
def test_approve_with_conflicting_execution_flags_has_zero_side_effects(tmp_path, conflict):
    cycle = SyntheticCycle(tmp_path)
    cycle.plan_transform = lambda plan: {**plan, "execution": {**plan["execution"], **conflict}}
    cycle.run()
    assert not cycle.orders
    assert not cycle.state_files()


def test_locked_evidence_gate_rejects_missing_materials_before_claim(tmp_path):
    cycle = SyntheticCycle(tmp_path)
    cycle.decision_transform = lambda decision: assess_with_evidence(
        decision, cycle.snapshot, scope="ACCOUNT", mandate_provenance=None, market_data={},
    ).decision
    result = cycle.run()
    assert result.execution["risk_gate"] == "REJECT"
    assert not cycle.orders
    assert not cycle.state_files()


@pytest.mark.parametrize("projection", [
    {"observation_status": "STALE"}, {"unknown_pending_orders": True},
    {"equity_usd": None, "observation_status": "UNAVAILABLE"},
    {"reconciliation_status": "UNVERIFIED"},
])
def test_unhealthy_new_risk_projection_does_not_claim_or_buy(tmp_path, projection):
    cycle = SyntheticCycle(tmp_path, projection=projection)
    cycle.run()
    assert not cycle.orders
    assert not cycle.state_files()


def test_approve_buy_is_claimed_once_across_repeat_and_store_reopen(tmp_path):
    cycle = SyntheticCycle(tmp_path)
    cycle.run()
    assert [(order.side, order.quantity) for order in cycle.orders] == [("buy", 4)]
    assert cycle.state_files()
    cycle.run()
    cycle.store = ExecutionMarkerStore(local_dir=cycle.state_dir)
    cycle.config = replace(cycle.config, execution_state_store=cycle.store)
    cycle.run()
    assert len(cycle.orders) == 1


def test_cash_target_approve_sells_without_creating_a_buy(tmp_path):
    cycle = SyntheticCycle(tmp_path, positions=(Position(symbol="SOXL", quantity=4, market_value=400.),), cash=100., targets={"SOXL": 0.})
    cycle.run()
    assert [(order.side, order.quantity) for order in cycle.orders] == [("sell", 4)]


@pytest.mark.parametrize("equity", [None, 0., float("nan"), float("inf")])
def test_actual_risk_engine_rejects_bad_equity_before_mapper_claim(tmp_path, equity):
    cycle = SyntheticCycle(tmp_path)
    cycle.decision_transform = lambda decision: apply_risk_gate(
        decision, portfolio_snapshot=replace(cycle.snapshot, total_equity=equity),
    )
    result = cycle.run()
    assert result.execution["risk_gate"] == "REJECT"
    assert not cycle.orders
    assert not cycle.state_files()


def test_sell_refresh_applies_scale_without_restoring_exposure(tmp_path):
    cycle = SyntheticCycle(tmp_path, positions=(Position(symbol="SOXX", quantity=4, market_value=400.),),
                           cash=100., targets={"SOXL": 400., "SOXX": 0.}, projection={"drawdown_from_peak": .075})
    result = cycle.run()
    assert result.allocation["targets"]["SOXL"] == 200.
    assert [(order.side, order.quantity) for order in cycle.orders] == [("sell", 4), ("buy", 2)]


@pytest.mark.parametrize("rejection", ["REJECT", None, "INVALID"])
def test_rejected_refresh_preserves_sell_fact_and_never_buys(tmp_path, rejection):
    cycle = SyntheticCycle(tmp_path, positions=(Position(symbol="SOXX", quantity=4, market_value=400.),),
                           cash=100., targets={"SOXL": 400., "SOXX": 0.})
    cycle.refresh_transform = lambda plan: {**plan, "execution": {**plan["execution"], "risk_gate": rejection}}
    result = cycle.run()
    assert result.execution["no_execute"]
    assert [(order.side, order.quantity) for order in cycle.orders] == [("sell", 4)]
    assert result.pending_orders[0]["submission_status"] == "filled"
    assert result.pending_orders[0]["filled_quantity"] == 4
    # Rejected replan still retains the original filled sell and its claim.
    assert cycle.state_files()


@pytest.mark.parametrize("projection,expected_buys", [({}, 2), ({"drawdown_from_peak": .16}, 0), ({"unknown_pending_orders": True}, 0)])
def test_refresh_health_cannot_restore_exposure_or_buy_past_new_block(tmp_path, projection, expected_buys):
    cycle = SyntheticCycle(tmp_path, positions=(Position(symbol="SOXX", quantity=4, market_value=400.),),
                           cash=100., targets={"SOXL": 400., "SOXX": 0.}, projection={"drawdown_from_peak": .075})
    cycle.refresh_transform = lambda plan: {**plan, "portfolio": {**plan["portfolio"], "account_new_risk_snapshot": projection}}
    cycle.run()
    assert sum(order.quantity for order in cycle.orders if order.side == "buy") == expected_buys


def test_gate_release_without_new_target_does_not_create_an_intent(tmp_path):
    cycle = SyntheticCycle(tmp_path, targets={"SOXL": 0.}, projection={"unknown_pending_orders": True})
    cycle.run()
    cycle.projection = {}
    cycle.run()
    assert not cycle.orders
    assert not cycle.state_files()


def _configure_live_cycle(cycle, tmp_path):
    cycle.snapshot = replace(cycle.snapshot, as_of=datetime(2026, 7, 6, tzinfo=timezone.utc))
    cycle.plan_transform = lambda plan: {**plan, "execution": {
        **plan["execution"], "signal_date": "2026-07-02", "effective_date": "2026-07-06",
        "completed_session_date": "2026-07-02", "generated_at": "2026-07-06T19:45:00+00:00",
        "execution_timing_contract": "next_trading_day",
    }}
    command_store = ExecutionCommandStore(local_dir=tmp_path / "commands")
    cycle.config = replace(
        cycle.config, durable_execution_command_live_enabled=True,
        durable_live_execution_session_authorized=True, execution_command_store=command_store,
        durable_execution_runtime_identity_digest="a" * 64,
    )
    cycle.runtime = replace(cycle.runtime, resolve_frozen_rebalance_plan=lambda **kwargs: cycle.resolve(snapshot=kwargs["snapshot"]))
    return command_store


@pytest.mark.parametrize("action", [None, "REJECT", "INVALID"])
def test_unapproved_live_signal_does_not_enqueue_claim_or_submit(tmp_path, action):
    cycle = SyntheticCycle(tmp_path)
    command_store = _configure_live_cycle(cycle, tmp_path)
    cycle.decision_transform = lambda decision: replace(decision, diagnostics={**decision.diagnostics, "risk_gate": action})
    result = cycle.run()
    assert result.execution["no_execute"]
    assert not rebalance_service.list_live_execution_commands(command_store)
    assert not tuple((tmp_path / "commands").rglob("*"))
    assert not cycle.orders
    assert not cycle.state_files()


@pytest.mark.parametrize("action", [None, "REJECT", "INVALID"])
def test_queued_live_command_rejection_does_not_change_command_or_claim(tmp_path, action):
    cycle = SyntheticCycle(tmp_path)
    command_store = _configure_live_cycle(cycle, tmp_path)
    plan = cycle.resolve(snapshot=cycle.snapshot)
    command = build_live_execution_command(
        platform="longbridge", account_scope=cycle.config.execution_state_account_scope,
        strategy_profile=cycle.config.strategy_profile, physical_account_id=cycle.config.physical_account_id,
        runtime_identity_digest="a" * 64, execution=plan["execution"], allocation=plan["allocation"],
    )
    command_store.enqueue(command)
    before = {path: path.read_bytes() for path in (tmp_path / "commands").rglob("*") if path.is_file()}
    cycle.decision_transform = lambda decision: replace(decision, diagnostics={**decision.diagnostics, "risk_gate": action})
    cycle.run()
    after = {path: path.read_bytes() for path in (tmp_path / "commands").rglob("*") if path.is_file()}
    assert command_store.current_state(command) is ExecutionCommandState.QUEUED
    assert before == after
    assert not cycle.orders
    assert not cycle.state_files()


def test_approved_live_command_keeps_next_session_claim_and_terminal_dedup(tmp_path):
    cycle = SyntheticCycle(tmp_path)
    command_store = _configure_live_cycle(cycle, tmp_path)
    cycle.run()
    cycle.run()
    command = rebalance_service.list_live_execution_commands(command_store)[0]
    assert command_store.current_state(command) is ExecutionCommandState.FILLED
    assert [(order.side, order.quantity) for order in cycle.orders] == [("buy", 4)]


def test_unapproved_paper_preview_does_not_enqueue_execution_state(tmp_path):
    cycle = SyntheticCycle(tmp_path)
    command_dir = tmp_path / "paper-commands"
    cycle.config = replace(cycle.config, dry_run_only=True, durable_execution_command_paper_enabled=True,
                           execution_command_store=ExecutionCommandStore(local_dir=command_dir))
    cycle.decision_transform = lambda decision: replace(decision, diagnostics={**decision.diagnostics, "risk_gate": None})
    cycle.run()
    assert not tuple(command_dir.rglob("*"))
    assert not cycle.orders
    assert not cycle.state_files()


@pytest.mark.parametrize("drawdown,quantity", [(.0749, 4), (.075, 2), (.15, 0)])
def test_locked_envelope_thresholds_are_consumed_without_changing_them(tmp_path, drawdown, quantity):
    cycle = SyntheticCycle(tmp_path, projection={"drawdown_from_peak": drawdown})
    cycle.run()
    assert sum(order.quantity for order in cycle.orders) == quantity
    if quantity == 0:
        assert not cycle.state_files()


def test_paper_state_missing_predecessor_repeat_restart_and_release_are_observation_only(tmp_path):
    identity = StrategyRiskStateIdentity(strategy_profile="soxl_soxx_trend_income", account_scope="paper",
                                         candidate_id="synthetic-candidate", config_sha256="a" * 64)
    root = build_strategy_risk_state_transition(identity=identity, effective_session="2026-08-24",
        input_sha256="b" * 64, state={"cooldown_remaining_sessions": 2, "reentry_allowed": False})
    release = build_strategy_risk_state_transition(identity=identity, effective_session="2026-08-25",
        input_sha256="c" * 64, state={"cooldown_remaining_sessions": 0, "reentry_allowed": True}, previous_transition=root)
    store_dir = tmp_path / "risk-state"
    store = StrategyRiskStateStore(local_dir=store_dir)

    def record(transition):
        return record_paper_strategy_risk_state_transition(enabled=True, dry_run_only=True, store=store,
            transition_payload=transition.to_dict(), expected_strategy_profile=identity.strategy_profile,
            expected_account_scope=identity.account_scope)

    with pytest.raises(StrategyRiskStateChainError, match="predecessor"):
        record(release)
    assert not tuple(store_dir.rglob("*"))
    assert record(root)["status"] == "created"
    assert record(root)["status"] == "already_appended"
    store = StrategyRiskStateStore(local_dir=store_dir)
    receipt = record(release)
    assert receipt["chain_length"] == 2
    assert receipt["consumer_authorized"] is False
    assert store.load_chain(identity) == (root, release)
    with pytest.raises(StrategyRiskStateChainError, match="advance"):
        build_strategy_risk_state_transition(identity=identity, effective_session=root.effective_session,
            input_sha256="d" * 64, state={"cooldown_remaining_sessions": 1}, previous_transition=root)
    cycle = SyntheticCycle(tmp_path, targets={"SOXL": 0.})
    cycle.run()
    assert not cycle.orders
    assert not cycle.state_files()


@pytest.mark.parametrize("flags", [
    "rejected:risk_engine", {"rejected:risk_engine": True}, {"rejected:risk_engine"},
    None, True, ["risk_gate:passed"], (None,), (True,), (1,), ({"flag": "rejected"},),
])
def test_malformed_producer_flags_are_rejected_before_mapper_conversion(tmp_path, flags):
    cycle = SyntheticCycle(tmp_path)
    cycle.decision_transform = lambda decision: replace(decision, risk_flags=flags)
    result = cycle.run()
    assert result.execution["risk_gate"] == "REJECT"
    assert result.execution["no_execute"]
    assert result.execution["risk_flags"] == ("rejected:invalid_risk_flags", "no_execute")
    assert not cycle.orders
    assert not cycle.state_files()


def test_valid_producer_tuple_flags_keep_the_approved_control(tmp_path):
    cycle = SyntheticCycle(tmp_path)
    cycle.decision_transform = lambda decision: replace(decision, risk_flags=("risk_gate:passed",))
    cycle.run()
    assert [(order.side, order.quantity) for order in cycle.orders] == [("buy", 4)]


@pytest.mark.parametrize("mode", [
    "later_reject_lower_cash", "later_bad_health_lower_cash", "earlier_zero_scale_then_recovery",
    "earlier_reject_then_recovery", "earlier_bad_health_then_recovery",
])
def test_every_refresh_attempt_latches_risk_even_when_cash_selection_discards_it(tmp_path, mode):
    cycle = SyntheticCycle(tmp_path, positions=(Position(symbol="SOXX", quantity=4, market_value=400.),),
                           cash=100., targets={"SOXL": 400., "SOXX": 0.})
    cycle.config = replace(cycle.config, post_sell_refresh_attempts=2)

    def refresh(plan):
        first = cycle.resolve_count == 2
        plan["execution"]["investable_cash"] = 100. if first else (500. if "recovery" in mode else 90.)
        if (mode == "later_reject_lower_cash" and not first) or (mode == "earlier_reject_then_recovery" and first):
            plan["execution"]["risk_gate"] = "REJECT"
        if (mode == "later_bad_health_lower_cash" and not first) or (mode == "earlier_bad_health_then_recovery" and first):
            plan["portfolio"]["account_new_risk_snapshot"] = {"unknown_pending_orders": True}
        if mode == "earlier_zero_scale_then_recovery" and first:
            plan["portfolio"]["account_new_risk_snapshot"] = {"drawdown_from_peak": .16}
        return plan

    cycle.refresh_transform = refresh
    result = cycle.run()
    assert cycle.resolve_count == 3
    assert [(order.side, order.quantity) for order in cycle.orders] == [("sell", 4)]
    assert result.pending_orders[0]["submission_status"] == "filled"
    assert result.pending_orders[0]["filled_quantity"] == 4
    assert cycle.state_files()


@pytest.mark.parametrize("constraint,expected_target", [("scale", 200.), ("target", 100.)])
def test_latest_refresh_risk_targets_survive_lower_cash_than_earlier_plan(tmp_path, constraint, expected_target):
    cycle = SyntheticCycle(tmp_path, positions=(Position(symbol="SOXX", quantity=4, market_value=400.),),
                           cash=100., targets={"SOXL": 400., "SOXX": 0.})
    cycle.config = replace(cycle.config, post_sell_refresh_attempts=2)

    def refresh(plan):
        latest = cycle.resolve_count == 3
        plan["execution"]["investable_cash"] = 90. if latest else 100.
        if latest and constraint == "scale":
            plan["portfolio"]["account_new_risk_snapshot"] = {"drawdown_from_peak": .075}
        if latest and constraint == "target":
            plan["allocation"]["targets"]["SOXL"] = 100.
        return plan

    cycle.refresh_transform = refresh
    result = cycle.run()
    assert result.allocation["targets"]["SOXL"] == expected_target
    assert sum(order.quantity for order in cycle.orders if order.side == "buy") <= expected_target / 100.
