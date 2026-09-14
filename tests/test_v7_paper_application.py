from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.v7_paper_application import (
    _application_contract,
    _validate_application_record,
    V7_CONFIG_SHA256,
    V7_PAPER_PROFILE,
    V7_APPROVED_UES_REVISION,
    V7_UES_REVISION,
    V7PaperApplicationError,
    build_v7_loaded_version_readback,
    build_v7_runtime_binding,
    load_v7_paper_application_binding,
)
from decision_mapper import map_strategy_decision_to_plan
from quant_platform_kit.common.capital_base import (
    CapitalBaseBinding,
    CapitalScope,
    CapitalValuationBasis,
    build_capital_base_snapshot,
)
from quant_platform_kit.risk.contracts import CandidateRiskIdentity
from quant_platform_kit.common.strategy_contracts import PositionTarget, StrategyDecision


FIXED_FRIDAY = datetime(2026, 9, 11, 21, 0, tzinfo=timezone.utc)
FIXED_MONDAY = datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc)


def _record(**overrides):
    record = {
        "application_id": str(uuid.uuid4()),
        "ticket_id": "rpt_" + "b" * 64,
        "status": "approved",
        "platform_id": "longbridge",
        "account_key": "longbridge-paper",
        "account_scope": "PAPER",
        "account_selector": "PAPER",
        "service_name": "longbridge-quant-paper-service",
        "strategy_profile": V7_PAPER_PROFILE,
        "candidate_id": V7_PAPER_PROFILE,
        "config_sha256": V7_CONFIG_SHA256,
        "source_commit": "a" * 40,
        "approved_ues_revision": V7_APPROVED_UES_REVISION,
        "expected_revision": 42,
        "expected_strategy_profile": "soxl_soxx_trend_income",
        "desired_state": "paused",
        "claim": {
            "token": "claim-token",
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
        },
    }
    record.update(overrides)
    return record


def _protected_env():
    return {
        "CLOUD_RUN_SERVICE": "longbridge-quant-paper-service",
        "CLOUD_RUN_REGION": "asia-east1",
        "LONGPORT_SECRET_NAME": "longport_token_paper",
        "LONGPORT_APP_KEY_SECRET_NAME": "longport-app-key-paper",
        "LONGPORT_APP_SECRET_SECRET_NAME": "longport-app-secret-paper",
        "LONGBRIDGE_PHYSICAL_ACCOUNT_ID": "paper-account-1",
        "LONGBRIDGE_DRY_RUN_ONLY": "false",
        "RUNTIME_TARGET_ENABLED": "true",
        "RUNTIME_TARGET_JSON": json.dumps(
            {
                "platform_id": "longbridge",
                "strategy_profile": "soxl_soxx_trend_income",
                "dry_run_only": False,
                "execution_mode": "live",
                "account_scope": "PAPER",
                "account_selector": ["PAPER"],
                "service_name": "longbridge-quant-paper-service",
                "strategy_release": {
                    "release_id": "old-release",
                    "manifest_sha256": "1" * 64,
                    "strategy_revision": "2" * 40,
                    "config_sha256": "3" * 64,
                    "risk_policy_sha256": "4" * 64,
                    "evidence_sha256": "5" * 64,
                    "plugin_bundle_sha256": "6" * 64,
                    "effective_session": "2026-09-13T00:00:00Z",
                },
            }
        ),
    }


def _synthetic_v7_execution_materials(snapshot):
    candidate = CandidateRiskIdentity(
        strategy_profile=V7_PAPER_PROFILE,
        account_mode="longbridge_paper_v1",
        strategy_revision=V7_APPROVED_UES_REVISION,
        runner_revision="b" * 40,
        config_sha256=V7_CONFIG_SHA256,
        input_manifest_sha256="c" * 64,
        authority_receipt_sha256="a" * 64,
    )
    mandate = {
        "mandate_id": "soxl_v7_synthetic_paper_v1",
        "mandate_version": "synthetic-2026-09-11.1",
        "authority_receipt_sha256": candidate.authority_receipt_sha256,
        "authority_scope": "PAPER",
        "strategy_profile": candidate.strategy_profile,
        "account_mode": candidate.account_mode,
        "strategy_revision": candidate.strategy_revision,
        "runner_revision": candidate.runner_revision,
        "config_sha256": candidate.config_sha256,
        "input_manifest_sha256": candidate.input_manifest_sha256,
        "candidate_identity_sha256": candidate.candidate_sha256,
        "effective_at": "2026-09-01T00:00:00Z",
        "expires_at": "2026-09-30T00:00:00Z",
        "max_snapshot_age_seconds": 300,
        "effective_exposure_cap": 1.0,
        "loss_budget": 0.01,
        "product_caps": {"SOXL": 0.50, "SOXX": 0.50, "BOXX": 1.0},
        "nominal_caps": {"SOXL": 0.50, "SOXX": 0.50, "BOXX": 1.0},
        "product_leverage_factors": {"SOXL": 3, "SOXX": 1, "BOXX": 1},
        "allowed_nonzero_assets": ["BOXX", "SOXL", "SOXX"],
        "source_revision": V7_UES_REVISION,
    }
    binding = CapitalBaseBinding(
        account_scope="PAPER",
        runtime_scope="longbridge-quant-paper-service",
        strategy_scope=V7_PAPER_PROFILE,
        target_currency="USD",
        capital_scope=CapitalScope.ACCOUNT,
        valuation_basis=CapitalValuationBasis.BROKER_ACCOUNT_NET_LIQUIDATION,
    )
    capital = build_capital_base_snapshot(
        snapshot,
        account_scope=binding.account_scope,
        runtime_scope=binding.runtime_scope,
        strategy_scope=binding.strategy_scope,
        reported_currency="USD",
        target_currency="USD",
        fx_rate_to_target=1.0,
        source_digest_sha256="d" * 64,
        capital_scope=binding.capital_scope,
        valuation_basis=binding.valuation_basis,
    )
    return {
        "candidate_risk_identity": candidate,
        "mandate_provenance": mandate,
        "capital_base": capital,
        "capital_base_binding": binding,
        "strategy_release": {
            "release_id": "v7-synthetic-paper-20260913",
            "manifest_sha256": "e" * 64,
            "strategy_revision": V7_APPROVED_UES_REVISION,
            "config_sha256": V7_CONFIG_SHA256,
            "risk_policy_sha256": "f" * 64,
            "evidence_sha256": "1" * 64,
            "plugin_bundle_sha256": "2" * 64,
            "effective_session": "2026-09-14",
        },
    }


def test_parameterized_candidate_contract_is_fixture_only_and_does_not_select_runtime_adapter() -> None:
    contract = _application_contract(
        label="synthetic fixture",
        platform_id="longbridge",
        account_scope="PAPER",
        service_name="longbridge-quant-paper-service",
        strategy_profile="synthetic_candidate_fixture",
        candidate_id="synthetic_candidate_fixture",
        config_sha256="f" * 64,
        approved_ues_revision="synthetic-ues-revision",
    )
    record = _record(
        strategy_profile="synthetic_candidate_fixture",
        candidate_id="synthetic_candidate_fixture",
        config_sha256="f" * 64,
        approved_ues_revision="synthetic-ues-revision",
    )
    validated = _validate_application_record(record, contract=contract)
    assert validated["candidate_id"] == "synthetic_candidate_fixture"
    assert validated["desired_state"] == "paused"


def test_build_v7_runtime_binding_is_paused_and_broker_paper() -> None:
    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())

    assert binding["application_id"]
    assert binding["strategy_profile"] == V7_PAPER_PROFILE
    assert binding["config_sha256"] == V7_CONFIG_SHA256
    assert binding["source_commit"] == "a" * 40
    assert binding["runtime_target_enabled"] is False
    assert binding["runtime_target"]["execution_environment"] == "paper"
    assert binding["runtime_target"]["dry_run_only"] is False
    assert binding["runtime_target"]["execution_mode"] == "live"
    assert binding["account_target"]["physical_account_id"] == "paper-account-1"
    assert binding["expected_revision"] == 42
    assert binding["approved_ues_revision"] == V7_APPROVED_UES_REVISION
    assert binding["runtime_target"].get("strategy_release") is None
    assert binding["previous_strategy_release"]["release_id"] == "old-release"


def test_v7_binding_can_carry_validated_paper_execution_materials_without_unpausing() -> None:
    from quant_platform_kit.common.models import PortfolioSnapshot

    snapshot = PortfolioSnapshot(
        as_of=FIXED_FRIDAY,
        total_equity=100_000.0,
        buying_power=100_000.0,
        positions=(),
        metadata={"market_currency_cash": 100_000.0},
    )
    binding = build_v7_runtime_binding(
        _record(),
        protected_env=_protected_env(),
        execution_materials=_synthetic_v7_execution_materials(snapshot),
    )

    loaded = load_v7_paper_application_binding(
        {"LONGBRIDGE_V7_PAPER_APPLICATION_JSON": json.dumps(binding)}
    )
    assert loaded["runtime_target_enabled"] is False
    assert loaded["desired_state"] == "paused"
    assert loaded["execution_materials"]["mandate_provenance"]["authority_scope"] == "PAPER"


def test_formal_loader_consumes_json_execution_materials_but_keeps_paused_guard(monkeypatch) -> None:
    from quant_platform_kit.common.models import PortfolioSnapshot
    from quant_platform_kit.common.runtime_target import build_runtime_target
    from runtime_config_support import PlatformRuntimeSettings
    from strategy_runtime import load_strategy_runtime

    snapshot = PortfolioSnapshot(
        as_of=FIXED_FRIDAY,
        total_equity=100_000.0,
        buying_power=100_000.0,
        positions=(),
        metadata={"market_currency_cash": 100_000.0},
    )
    binding = build_v7_runtime_binding(
        _record(),
        protected_env=_protected_env(),
        execution_materials=_synthetic_v7_execution_materials(snapshot),
    )
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    target = build_runtime_target(
        platform_id="longbridge",
        strategy_profile=V7_PAPER_PROFILE,
        dry_run_only=False,
        account_selector=["PAPER"],
        account_scope="PAPER",
        service_name="longbridge-quant-paper-service",
        execution_environment="paper",
    )
    settings = PlatformRuntimeSettings(
        project_id=None,
        secret_name="paper",
        account_prefix="PAPER",
        strategy_profile=V7_PAPER_PROFILE,
        strategy_display_name="V7",
        strategy_domain="us_equity",
        account_region="PAPER",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        dry_run_only=False,
        runtime_target=target,
    )

    loaded = load_strategy_runtime(V7_PAPER_PROFILE, runtime_settings=settings)

    assert loaded.execution_materials["candidate_risk_identity"]["strategy_profile"] == V7_PAPER_PROFILE
    assert loaded.execution_materials["mandate_provenance"]["authority_scope"] == "PAPER"
    assert loaded.execution_entrypoint is None
    assert settings.runtime_target_enabled is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("platform_id", "other"),
        ("account_scope", "HK"),
        ("account_selector", "other"),
        ("strategy_profile", "soxl_soxx_trend_income"),
        ("candidate_id", "other"),
        ("config_sha256", "0" * 64),
        ("approved_ues_revision", "b" * 40),
        ("desired_state", "enabled"),
    ],
)
def test_build_v7_runtime_binding_rejects_scope_or_candidate_drift(field, value) -> None:
    with pytest.raises(V7PaperApplicationError):
        build_v7_runtime_binding(_record(**{field: value}), protected_env=_protected_env())


def test_build_v7_runtime_binding_rejects_stale_old_target_or_dry_run() -> None:
    protected = _protected_env()
    protected["RUNTIME_TARGET_JSON"] = json.dumps(
        {
            "platform_id": "longbridge",
            "strategy_profile": "other-old-profile",
            "dry_run_only": False,
            "account_scope": "PAPER",
            "account_selector": ["PAPER"],
            "service_name": "longbridge-quant-paper-service",
        }
    )
    with pytest.raises(V7PaperApplicationError, match="expected strategy"):
        build_v7_runtime_binding(_record(), protected_env=protected)

    protected = _protected_env()
    protected["LONGBRIDGE_DRY_RUN_ONLY"] = "true"
    with pytest.raises(V7PaperApplicationError, match="dry_run_only"):
        build_v7_runtime_binding(_record(), protected_env=protected)


def test_load_binding_requires_exact_application_json_and_never_accepts_enabled() -> None:
    record = _record()
    binding = build_v7_runtime_binding(record, protected_env=_protected_env())
    env = {"LONGBRIDGE_V7_PAPER_APPLICATION_JSON": json.dumps(binding)}

    loaded = load_v7_paper_application_binding(env)
    assert loaded["application_id"] == record["application_id"]
    assert loaded["runtime_target_enabled"] is False

    enabled = dict(binding)
    enabled["runtime_target_enabled"] = True
    with pytest.raises(V7PaperApplicationError, match="paused"):
        load_v7_paper_application_binding(
            {"LONGBRIDGE_V7_PAPER_APPLICATION_JSON": json.dumps(enabled)}
        )


def test_qsl_flat_claim_is_normalized_without_copying_claim_token() -> None:
    record = _record()
    claim = record.pop("claim")
    record.update(
        {
            "claim_token": claim["token"],
            "workflow_run_id": claim["workflow_run_id"],
            "workflow_run_attempt": claim["workflow_run_attempt"],
        }
    )
    binding = build_v7_runtime_binding(record, protected_env=_protected_env())
    assert "claim_token" not in json.dumps(binding)
    assert binding["claim"]["workflow_run_id"] == "123"


def test_qsl_application_record_uses_nested_claim_transport(monkeypatch) -> None:
    from scripts import v7_paper_application_http as transport

    record = _record()
    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "claimed": True}

    monkeypatch.setattr(transport, "_request", fake_request)
    transport.post_claim(
        base_url="https://qsl-strategy-switch-console.pigbibi.workers.dev",
        token="secret-token",
        record=record,
        run_id="200",
        attempt="1",
    )
    assert calls[0]["payload"]["claim"]["token"] == "claim-token"
    assert "claim_token" not in calls[0]["payload"]


def test_qsl_application_origin_is_fixed() -> None:
    from scripts.v7_paper_application_http import ApplicationTransportError, _endpoint

    with pytest.raises(ApplicationTransportError):
        _endpoint("https://attacker.example", str(uuid.uuid4()))


def test_loaded_version_readback_uses_process_identity_and_stays_paused(monkeypatch) -> None:
    from quant_platform_kit.common.runtime_target import build_runtime_target
    from strategy_loader import load_strategy_entrypoint_for_profile, load_strategy_runtime_adapter_for_profile
    from us_equity_strategies import get_strategy_entrypoint

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    target = build_runtime_target(
        platform_id="longbridge",
        strategy_profile=V7_PAPER_PROFILE,
        dry_run_only=False,
        account_selector=["PAPER"],
        account_scope="PAPER",
        service_name="longbridge-quant-paper-service",
        execution_environment="paper",
    )
    runtime = SimpleNamespace(
        entrypoint=get_strategy_entrypoint(V7_PAPER_PROFILE),
        runtime_adapter=load_strategy_runtime_adapter_for_profile(V7_PAPER_PROFILE),
    )
    settings = SimpleNamespace(runtime_target=target, runtime_target_enabled=False, dry_run_only=False)
    readback = build_v7_loaded_version_readback(
        binding,
        runtime_settings=settings,
        strategy_runtime=runtime,
        runtime_env={"V7_PAPER_SOURCE_COMMIT": "a" * 40, "K_REVISION": "paper-v7-00001"},
    )
    assert readback["activation_state"] == "applied_paused"
    assert readback["runtime_target_enabled"] is False
    assert readback["ues_revision"] == V7_APPROVED_UES_REVISION
    assert readback["revision_name"] == "paper-v7-00001"
    assert readback["runtime_loaded_receipt"]["schema_version"] == "runtime_loaded_receipt.v1"


def test_bound_v7_loader_is_opt_in_and_normal_loader_stays_fail_closed(monkeypatch) -> None:
    from strategy_loader import load_strategy_entrypoint_for_profile

    with pytest.raises(ValueError):
        load_strategy_entrypoint_for_profile(V7_PAPER_PROFILE)

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    entrypoint = load_strategy_entrypoint_for_profile(V7_PAPER_PROFILE)

    assert entrypoint.manifest.profile == V7_PAPER_PROFILE
    assert entrypoint.manifest.default_config["managed_symbols"] == ("SOXL", "SOXX", "BOXX")


def test_bound_v7_decision_mapper_preserves_research_block_markers(monkeypatch) -> None:
    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))

    plan = map_strategy_decision_to_plan(
        StrategyDecision(
            positions=(PositionTarget(symbol="SOXL", target_value=1000.0),),
            diagnostics={
                "trade_threshold_value": 100.0,
                "no_order": True,
                "execution_authorized": False,
            },
        ),
        account_state={
            "available_cash": 5000.0,
            "market_values": {"SOXL": 0.0},
            "quantities": {"SOXL": 0},
            "sellable_quantities": {"SOXL": 0},
            "total_strategy_equity": 5000.0,
        },
        strategy_profile=V7_PAPER_PROFILE,
    )

    assert plan["allocation"]["targets"]["SOXL"] == 1000.0
    assert plan["execution"]["no_execute"] is True
    assert plan["execution"]["no_order"] is True
    assert plan["execution"]["execution_authorized"] is False


def test_bound_v7_loaded_runtime_to_rebalance_isolated_validation_has_zero_writes(monkeypatch) -> None:
    from application import rebalance_service
    from application.runtime_dependencies import LongBridgeRebalanceConfig, LongBridgeRebalanceRuntime
    from application.runtime_strategy_adapters import build_runtime_strategy_adapters
    from quant_platform_kit.common.models import QuoteSnapshot
    from quant_platform_kit.common.port_adapters import (
        CallableExecutionPort,
        CallableMarketDataPort,
        CallableNotificationPort,
        CallablePortfolioPort,
    )
    from quant_platform_kit.common.runtime_inputs import build_strategy_evaluation_inputs
    from quant_platform_kit.common.runtime_target import build_runtime_target
    from runtime_config_support import PlatformRuntimeSettings
    from strategy_loader import (
        load_strategy_entrypoint_for_profile,
        load_strategy_runtime_adapter_for_profile,
    )
    from strategy_runtime import LoadedStrategyRuntime
    from test_v7_paper_preview import _available_inputs
    from notifications.telegram import build_translator

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    target = build_runtime_target(
        platform_id="longbridge",
        strategy_profile=V7_PAPER_PROFILE,
        dry_run_only=False,
        account_selector=["PAPER"],
        account_scope="PAPER",
        service_name="longbridge-quant-paper-service",
        execution_environment="paper",
    )
    settings = PlatformRuntimeSettings(
        project_id=None,
        secret_name="paper",
        account_prefix="PAPER",
        strategy_profile=V7_PAPER_PROFILE,
        strategy_display_name="V7",
        strategy_domain="us_equity",
        account_region="PAPER",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        dry_run_only=False,
        runtime_target=target,
    )
    entrypoint = load_strategy_entrypoint_for_profile(V7_PAPER_PROFILE)
    runtime_adapter = load_strategy_runtime_adapter_for_profile(V7_PAPER_PROFILE)
    loaded_runtime = LoadedStrategyRuntime(
        entrypoint=entrypoint,
        runtime_adapter=runtime_adapter,
        runtime_settings=settings,
    )
    inputs = _available_inputs()
    strategy_adapters = build_runtime_strategy_adapters(
        strategy_runtime=loaded_runtime,
        strategy_profile=V7_PAPER_PROFILE,
        strategy_runtime_config=loaded_runtime.merged_runtime_config,
        available_inputs=runtime_adapter.available_inputs,
        benchmark_symbol="SOXX",
        signal_text_fn=lambda icon: str(icon),
        translator=lambda key, **_kwargs: str(key),
        broker_adapters=type("BrokerAdapters", (), {})(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=build_strategy_evaluation_inputs,
        map_strategy_decision_to_plan_fn=map_strategy_decision_to_plan,
    )
    plan = strategy_adapters.resolve_rebalance_plan(
        indicators=inputs["derived_indicators"],
        snapshot=inputs["portfolio_snapshot"],
    )
    observed = {"submit": 0, "notify": 0}

    class NoWriteStore:
        cloud_prefix_uri = "gs://validation-only/blocked"
        local_dir = None

        def enqueue(self, *_args, **_kwargs):
            raise AssertionError("V7 validation must not enqueue a command")

        def append(self, *_args, **_kwargs):
            raise AssertionError("V7 validation must not write paper risk state")

    runtime = LongBridgeRebalanceRuntime(
        bootstrap=lambda: ("quote", "trade", inputs["derived_indicators"]),
        resolve_rebalance_plan=lambda **_kwargs: plan,
        market_data_port_factory=lambda _quote: CallableMarketDataPort(
            quote_loader=lambda _symbol: (_ for _ in ()).throw(
                AssertionError("blocked validation must not quote for an order")
            )
        ),
        execution_port_factory=lambda _trade: CallableExecutionPort(
            lambda _order: (
                observed.__setitem__("submit", observed["submit"] + 1),
                (_ for _ in ()).throw(AssertionError("V7 validation must not submit")),
            )[1]
        ),
        estimate_max_purchase_quantity=lambda *_args, **_kwargs: 0,
        notifications=CallableNotificationPort(
            lambda _message: observed.__setitem__("notify", observed["notify"] + 1)
        ),
        notify_issue=lambda *_args, **_kwargs: observed.__setitem__("notify", observed["notify"] + 1),
        portfolio_port_factory=lambda *_args: CallablePortfolioPort(
            lambda: inputs["portfolio_snapshot"]
        ),
    )
    config = LongBridgeRebalanceConfig(
        strategy_profile=V7_PAPER_PROFILE,
        dry_run_only=True,
        execution_dedup_enabled=False,
        execution_command_store=NoWriteStore(),
        durable_execution_command_paper_enabled=False,
        strategy_risk_state_store=NoWriteStore(),
        limit_sell_discount=1.0,
        limit_buy_premium=1.0,
        separator="-",
        translator=build_translator("en"),
        with_prefix=lambda message: message,
        notify_no_trade_cycles=False,
    )

    result = rebalance_service.run_strategy(runtime=runtime, config=config)

    assert plan["execution"]["risk_gate"] == "REJECT"
    assert plan["execution"]["no_execute"] is True
    assert plan["execution"]["no_order"] is True
    assert result.dry_run_orders == ()
    assert observed == {"submit": 0, "notify": 0}


def test_v7_evidence_runtime_to_rebalance_uses_real_risk_and_fake_broker(monkeypatch, tmp_path) -> None:
    from application import rebalance_service
    from application.runtime_dependencies import LongBridgeRebalanceConfig, LongBridgeRebalanceRuntime
    from application.runtime_strategy_adapters import build_runtime_strategy_adapters
    from quant_platform_kit.common.models import ExecutionReport, PortfolioSnapshot, QuoteSnapshot
    from quant_platform_kit.common.execution_commands import ExecutionCommandStore
    from application.execution_state import ExecutionMarkerStore
    from quant_platform_kit.common.port_adapters import (
        CallableExecutionPort,
        CallableMarketDataPort,
        CallableNotificationPort,
        CallablePortfolioPort,
    )
    from quant_platform_kit.common.runtime_inputs import build_strategy_evaluation_inputs
    from quant_platform_kit.common.runtime_target import build_runtime_target
    from runtime_config_support import PlatformRuntimeSettings
    from strategy_loader import load_strategy_entrypoint_for_profile, load_strategy_runtime_adapter_for_profile
    import strategy_runtime as strategy_runtime_module
    from strategy_runtime import LoadedStrategyRuntime
    from test_v7_paper_preview import _available_inputs
    from notifications.telegram import build_translator

    try:
        from us_equity_strategies.entrypoints import soxl_soxx_core_only_p2_v7_execution_entrypoint
    except ImportError:
        pytest.skip("installed UES revision predates the V7 execution entrypoint")

    clock = {"now": FIXED_FRIDAY}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = clock["now"]
            return value if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(strategy_runtime_module, "datetime", FrozenDateTime)
    from quant_platform_kit.risk import gate as risk_gate

    monkeypatch.setattr(risk_gate, "_utc_now", lambda: clock["now"])
    original_validate_capital_base = strategy_runtime_module.validate_capital_base
    monkeypatch.setattr(
        strategy_runtime_module,
        "validate_capital_base",
        lambda capital, *, binding: original_validate_capital_base(
            capital,
            binding=binding,
            now=clock["now"],
        ),
    )

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    target = build_runtime_target(
        platform_id="longbridge",
        strategy_profile=V7_PAPER_PROFILE,
        dry_run_only=False,
        account_selector=["PAPER"],
        account_scope="PAPER",
        service_name="longbridge-quant-paper-service",
        execution_environment="paper",
    )
    settings = PlatformRuntimeSettings(
        project_id=None,
        secret_name="paper",
        account_prefix="PAPER",
        strategy_profile=V7_PAPER_PROFILE,
        strategy_display_name="V7",
        strategy_domain="us_equity",
        account_region="PAPER",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        dry_run_only=False,
        runtime_target=target,
    )
    entrypoint = load_strategy_entrypoint_for_profile(V7_PAPER_PROFILE)
    runtime_adapter = load_strategy_runtime_adapter_for_profile(V7_PAPER_PROFILE)
    inputs = _available_inputs(high_volatility=True)
    snapshot = PortfolioSnapshot(
        as_of=FIXED_FRIDAY,
        total_equity=100_000.0,
        buying_power=100_000.0,
        positions=(),
        metadata={
            "market_currency_cash": 100_000.0,
            "observed_effective_exposure": 0.0,
            "account_hash": "PAPER",
            "broker_capital": {
                "currency": "USD",
                "net_assets": 100_000.0,
                "observed_at": FIXED_FRIDAY,
                "source_digest_sha256": "d" * 64,
            },
        },
    )
    materials = _synthetic_v7_execution_materials(snapshot)
    loaded_runtime = LoadedStrategyRuntime(
        entrypoint=entrypoint,
        execution_entrypoint=soxl_soxx_core_only_p2_v7_execution_entrypoint,
        execution_materials=materials,
        runtime_adapter=runtime_adapter,
        runtime_settings=settings,
    )
    strategy_adapters = build_runtime_strategy_adapters(
        strategy_runtime=loaded_runtime,
        strategy_profile=V7_PAPER_PROFILE,
        strategy_runtime_config=loaded_runtime.merged_runtime_config,
        available_inputs=runtime_adapter.available_inputs,
        benchmark_symbol="SOXX",
        signal_text_fn=lambda icon: str(icon),
        translator=lambda key, **_kwargs: str(key),
        broker_adapters=type("BrokerAdapters", (), {})(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=build_strategy_evaluation_inputs,
        map_strategy_decision_to_plan_fn=map_strategy_decision_to_plan,
        execution_materials=materials,
    )
    current_snapshot = {"value": snapshot}

    def resolve_plan(*, indicators, snapshot=None, account_state=None):
        return strategy_adapters.resolve_rebalance_plan(
            indicators=indicators,
            snapshot=snapshot or current_snapshot["value"],
            account_state=account_state,
        )

    plan = resolve_plan(indicators=inputs["derived_indicators"], snapshot=snapshot)
    assert plan["execution"]["risk_gate"] == "APPROVE"
    assert plan["execution"].get("no_order") is not True
    assert plan["execution"].get("no_execute") is not True
    command_store = ExecutionCommandStore(local_dir=tmp_path)
    marker_store = ExecutionMarkerStore(local_dir=tmp_path / "markers")

    submitted = []
    runtime = LongBridgeRebalanceRuntime(
        bootstrap=lambda: ("quote", "trade", inputs["derived_indicators"]),
        resolve_rebalance_plan=resolve_plan,
        resolve_frozen_rebalance_plan=lambda *, allocation, execution, snapshot: strategy_adapters.resolve_frozen_rebalance_plan(
            allocation=allocation,
            execution=execution,
            snapshot=snapshot,
        ),
        market_data_port_factory=lambda _quote: CallableMarketDataPort(
            quote_loader=lambda symbol: QuoteSnapshot(
                symbol=symbol,
                as_of=clock["now"].date().isoformat(),
                last_price=100.0,
            )
        ),
        execution_port_factory=lambda _trade: CallableExecutionPort(
            lambda order: (
                submitted.append(order),
                ExecutionReport(
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    status="filled",
                    broker_order_id=f"fake-{len(submitted)}",
                ),
            )[-1]
        ),
        estimate_max_purchase_quantity=lambda *_args, **_kwargs: 10_000,
        notifications=CallableNotificationPort(lambda _message: None),
        notify_issue=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("approved V7 validation must not notify an issue")
        ),
        portfolio_port_factory=lambda *_args: CallablePortfolioPort(
            lambda: current_snapshot["value"]
        ),
    )
    config = LongBridgeRebalanceConfig(
        strategy_profile=V7_PAPER_PROFILE,
        dry_run_only=False,
        execution_dedup_enabled=True,
        execution_state_store=marker_store,
        limit_sell_discount=1.0,
        limit_buy_premium=1.0,
        separator="-",
        translator=build_translator("en"),
        with_prefix=lambda message: message,
        notify_no_trade_cycles=False,
        execution_state_account_scope="PAPER",
        physical_account_id="fake-paper-account",
        durable_execution_command_live_enabled=True,
        durable_live_execution_session_authorized=True,
        durable_execution_runtime_identity_digest="a" * 64,
        execution_command_store=command_store,
        expected_strategy_release=materials["strategy_release"],
    )
    first = rebalance_service.run_strategy(runtime=runtime, config=config)
    assert first.action_done is False
    assert first.execution["durable_live_execution_command"]["status"] == "QUEUED"

    current_snapshot["value"] = PortfolioSnapshot(
        as_of=FIXED_MONDAY,
        total_equity=100_000.0,
        buying_power=100_000.0,
        positions=(),
        metadata={
            "market_currency_cash": 100_000.0,
            "observed_effective_exposure": 0.0,
            "account_hash": "PAPER",
            "broker_capital": {
                "currency": "USD",
                "net_assets": 100_000.0,
                "observed_at": FIXED_MONDAY,
                "source_digest_sha256": "d" * 64,
            },
        },
    )
    clock["now"] = FIXED_MONDAY
    result = rebalance_service.run_strategy(runtime=runtime, config=config)

    assert result.action_done is True, result.execution
    assert submitted


def test_runtime_settings_accept_only_bound_v7_and_force_disabled_paper_target(monkeypatch) -> None:
    from runtime_config_support import load_platform_runtime_settings

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    monkeypatch.setenv("RUNTIME_TARGET_JSON", json.dumps(binding["runtime_target"]))
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "false")
    monkeypatch.setenv("LONGBRIDGE_DRY_RUN_ONLY", "false")
    monkeypatch.setenv("ACCOUNT_PREFIX", "PAPER")
    monkeypatch.setenv("ACCOUNT_REGION", "PAPER")
    monkeypatch.setenv("LONGPORT_SECRET_NAME", "longport_token_paper")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.strategy_profile == V7_PAPER_PROFILE
    assert settings.runtime_target_enabled is False
    assert settings.dry_run_only is False
    assert settings.runtime_target.execution_environment.value == "paper"
