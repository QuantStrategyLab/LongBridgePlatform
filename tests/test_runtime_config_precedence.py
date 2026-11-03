"""Pin current ambient configuration behavior before any input-boundary change.

All identities are synthetic. These offline loader tests do not authorize or
exercise broker execution; a parsed risk dictionary is not verified risk.
"""

import json
import math
import os
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import runtime_config_support as config
from test_runtime_config_support import SAMPLE_STRATEGY_PROFILE, runtime_target_json


Q = "QSL_RUNTIME_TARGET_JSON"
U = "RUNTIME_TARGET_JSON"


def target_payload(**updates):
    payload = json.loads(runtime_target_json(
        SAMPLE_STRATEGY_PROFILE,
        dry_run_only=True,
        deployment_selector="fake-deployment",
        account_selector=["fake-account"],
        account_scope="fake-scope",
        service_name="fake-service",
    ))
    payload.update(updates)
    return payload


@pytest.fixture(autouse=True)
def isolated_environment():
    with patch.dict(os.environ, {U: json.dumps(target_payload())}, clear=True):
        yield


@pytest.fixture
def resolver():
    return Mock(return_value="fake-project")


@pytest.fixture
def load_settings(resolver):
    return partial(config.load_platform_runtime_settings, project_id_resolver=resolver)


@pytest.mark.parametrize("aliases", [(Q,), (U,), (Q, U)])
def test_single_or_identical_aliases_preserve_target_and_policy(aliases, load_settings, resolver):
    os.environ.clear()
    payload = target_payload(runtime_risk_limits={"fake_marker": "same"})
    os.environ.update({name: json.dumps(payload) for name in aliases})
    settings = load_settings()
    assert settings.runtime_target.account_scope == "fake-scope"
    assert settings.trusted_runtime_risk_policy == {"fake_marker": "same"}
    assert settings.project_id == "fake-project"
    assert settings.runtime_target_enabled is False
    resolver.assert_called_once_with()


def test_conflicting_aliases_select_qsl_target_and_plain_risk_without_merging(load_settings):
    os.environ[Q] = json.dumps(target_payload(runtime_risk_limits={"qsl_only": 1}))
    os.environ[U] = json.dumps(target_payload(
        strategy_profile="global_etf_rotation",
        account_scope="fake-other-scope",
        service_name="fake-other-service",
        runtime_risk_limits={"plain_only": 2},
    ))
    settings = load_settings()
    assert settings.runtime_target.account_scope == "fake-scope"
    assert settings.runtime_target.service_name == "fake-service"
    assert settings.strategy_profile == SAMPLE_STRATEGY_PROFILE
    assert settings.trusted_runtime_risk_policy == {"plain_only": 2}


@pytest.mark.parametrize("raw", [None, "", " \t "])
def test_legacy_profile_cannot_replace_missing_target(raw, load_settings, resolver):
    os.environ.clear()
    os.environ["STRATEGY_PROFILE"] = SAMPLE_STRATEGY_PROFILE
    if raw is not None:
        os.environ[Q] = raw
    with pytest.raises(EnvironmentError) as error:
        load_settings()
    assert str(error.value) == "RUNTIME_TARGET_JSON (or QSL_RUNTIME_TARGET_JSON) is required"
    resolver.assert_not_called()


@pytest.mark.parametrize("raw,exception,message", [
    (" \t ", EnvironmentError, "RUNTIME_TARGET_JSON (or QSL_RUNTIME_TARGET_JSON) is required"),
    ("{", ValueError, "RUNTIME_TARGET_JSON must contain valid JSON"),
    ("null", ValueError, "RUNTIME_TARGET_JSON must decode to an object"),
    ("[]", ValueError, "RUNTIME_TARGET_JSON must decode to an object"),
])
def test_nonempty_qsl_target_never_falls_back(raw, exception, message, load_settings, resolver):
    os.environ[Q] = raw
    with pytest.raises(exception) as error:
        load_settings()
    assert type(error.value) is exception
    assert str(error.value) == message
    resolver.assert_not_called()


def test_empty_qsl_target_falls_through_to_plain(load_settings):
    os.environ[Q] = ""
    assert load_settings().runtime_target.account_scope == "fake-scope"


@pytest.mark.parametrize("field,value,message", [
    ("platform_id", None, "RUNTIME_TARGET_JSON.platform_id is required"),
    ("platform_id", "fake-other-platform", "RUNTIME_TARGET_JSON.platform_id does not match the runtime platform"),
    ("strategy_profile", " ", "RUNTIME_TARGET_JSON.strategy_profile is required"),
    ("dry_run_only", None, "RUNTIME_TARGET_JSON.dry_run_only is required"),
    ("dry_run_only", "maybe", "invalid boolean value: 'maybe'"),
    ("execution_mode", "live", "RUNTIME_TARGET_JSON.execution_mode does not match dry_run_only"),
    ("market", "HK", "market metadata must include market, market_calendar, and market_timezone together"),
])
def test_selected_target_validation_precedes_alternate_alias(field, value, message, load_settings, resolver):
    os.environ[Q] = json.dumps(target_payload(**{field: value}))
    with pytest.raises(ValueError) as error:
        load_settings()
    assert str(error.value) == message
    resolver.assert_not_called()


@pytest.mark.parametrize("field", [
    "execution_windows", "scheduler", "strategy_release", "account_identity", "live_continuity", "decision_data",
])
def test_nested_target_objects_are_checked_before_risk(field, load_settings, resolver):
    os.environ[Q] = json.dumps(target_payload(**{field: []}))
    os.environ[U] = "{"
    with pytest.raises(ValueError) as error:
        load_settings()
    assert str(error.value) == f"RUNTIME_TARGET_JSON.{field} must be an object when present"
    resolver.assert_not_called()


def test_invalid_target_timezone_is_not_hidden_by_platform_override(load_settings, resolver):
    os.environ[U] = json.dumps(target_payload(
        market="HK", market_calendar="XHKG", market_timezone="Fake/Nowhere",
    ))
    os.environ["LONGBRIDGE_MARKET_TIMEZONE"] = "UTC"
    with pytest.raises(ValueError) as error:
        load_settings()
    assert str(error.value) == "invalid market_timezone: 'Fake/Nowhere'"
    resolver.assert_not_called()


def test_explicit_paper_environment_is_distinct_from_legacy_live_mode(load_settings):
    os.environ[U] = json.dumps(target_payload(
        dry_run_only=False, execution_mode="live", execution_environment="paper",
        strategy_release={
            "release_id": "fake-release", "manifest_sha256": "a" * 64,
            "strategy_revision": "b" * 40, "config_sha256": "c" * 64,
            "risk_policy_sha256": "d" * 64, "evidence_sha256": "e" * 64,
            "plugin_bundle_sha256": "f" * 64, "effective_session": "2026-01-01",
        },
        account_identity={"fake_identity": "retained"},
    ))
    settings = load_settings()
    target = settings.runtime_target
    assert (target.deployment_selector, target.account_selector) == ("fake-deployment", ("fake-account",))
    assert target.account_identity == {"fake_identity": "retained"}
    assert target.strategy_release.release_id == "fake-release"
    assert target.strategy_release.strategy_revision == "b" * 40
    assert target.execution_mode == "live"
    assert target.execution_environment.value == "paper"
    assert target.dry_run_only is False
    assert settings.dry_run_only is True
    assert settings.runtime_target_enabled is False


@pytest.mark.parametrize("plain", [" ", "{}", '{"runtime_risk_limits":null}'])
def test_selected_plain_risk_does_not_fall_back_to_qsl_policy(plain, load_settings):
    os.environ[Q] = json.dumps(target_payload(runtime_risk_limits={"qsl_only": 1}))
    os.environ[U] = plain
    assert load_settings().trusted_runtime_risk_policy is None


@pytest.mark.parametrize("plain", [None, ""])
def test_missing_or_empty_plain_risk_falls_back_to_qsl(plain, load_settings):
    os.environ[Q] = json.dumps(target_payload(runtime_risk_limits={"qsl_only": 1}))
    os.environ.pop(U)
    if plain is not None:
        os.environ[U] = plain
    assert load_settings().trusted_runtime_risk_policy == {"qsl_only": 1}


@pytest.mark.parametrize("raw,message", [
    ("{", "RUNTIME_TARGET_JSON must be valid JSON"),
    ("null", "RUNTIME_TARGET_JSON must decode to an object"),
    ("[]", "RUNTIME_TARGET_JSON must decode to an object"),
    ('"fake-scalar"', "RUNTIME_TARGET_JSON must decode to an object"),
    ('{"runtime_risk_limits":[]}', "RUNTIME_TARGET_JSON.runtime_risk_limits must be an object"),
    ('{"runtime_risk_limits":"fake"}', "RUNTIME_TARGET_JSON.runtime_risk_limits must be an object"),
    ('{"runtime_risk_limits":3}', "RUNTIME_TARGET_JSON.runtime_risk_limits must be an object"),
])
def test_risk_errors_are_distinct_from_target_errors(raw, message, load_settings, resolver):
    os.environ[Q] = json.dumps(target_payload())
    os.environ[U] = raw
    with pytest.raises(EnvironmentError) as error:
        load_settings()
    assert type(error.value) is EnvironmentError
    assert str(error.value) == message
    resolver.assert_not_called()


@pytest.mark.parametrize("policy", [None, {}, {"unknown": {"nested": [1]}}, {"max_positions": -1}])
def test_loader_only_checks_policy_shape(policy, load_settings):
    os.environ[U] = json.dumps(target_payload(runtime_risk_limits=policy))
    assert load_settings().trusted_runtime_risk_policy == policy


def test_risk_helper_makes_top_level_copy_only():
    policy = {"unknown": {"nested": [1]}}
    # JSON normally allocates fresh objects: spy on the decoded payload to pin
    # this helper's shallow-copy contract separately from whole-loader behavior.
    with patch.object(config.json, "loads", return_value={"runtime_risk_limits": policy}):
        loaded = config._load_trusted_runtime_risk_policy()
    assert loaded == policy
    assert loaded is not policy
    assert loaded["unknown"] is policy["unknown"]


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("qsl,plain,expected", [
    (None, None, True), (None, "true", True), (None, "false", False),
    ("true", "false", True), ("false", "true", False),
    ("", "false", False), (" ", "false", True), ("maybe", "true", False),
    ("YES", None, True), ("1", None, True), ("on", None, True), ("y", None, True),
])
def test_dry_run_alias_truth_table(dry_run, qsl, plain, expected, load_settings, resolver):
    os.environ[U] = json.dumps(target_payload(
        dry_run_only=dry_run, execution_mode="paper" if dry_run else "live",
    ))
    for name, value in [("QSL_LONGBRIDGE_DRY_RUN_ONLY", qsl), ("LONGBRIDGE_DRY_RUN_ONLY", plain)]:
        if value is not None:
            os.environ[name] = value
    if dry_run and not expected:
        with pytest.raises(EnvironmentError) as error:
            load_settings()
        assert str(error.value) == "LONGBRIDGE_DRY_RUN_ONLY=false conflicts with RUNTIME_TARGET_JSON.dry_run_only=true"
        resolver.assert_not_called()
    else:
        settings = load_settings()
        assert settings.dry_run_only is expected
        assert settings.runtime_target.dry_run_only is dry_run
        resolver.assert_called_once_with()


@pytest.mark.parametrize("qsl,plain,expected", [
    (None, None, False), ("true", "false", True), ("false", "true", False),
    ("", "true", True), (" ", "true", False), (None, "off", False),
])
def test_enablement_alias_and_short_circuit(qsl, plain, expected, load_settings):
    for name, value in [("QSL_RUNTIME_TARGET_ENABLED", qsl), ("RUNTIME_TARGET_ENABLED", plain)]:
        if value is not None:
            os.environ[name] = value
    with patch.object(config, "runtime_target_permits_standard_execution", return_value=True) as permits:
        assert load_settings().runtime_target_enabled is expected
    assert permits.call_count == int(expected)


def test_enabled_parser_is_strict_even_though_dry_run_parser_is_permissive(load_settings, resolver):
    os.environ["RUNTIME_TARGET_ENABLED"] = "maybe"
    with pytest.raises(ValueError) as error:
        load_settings()
    assert str(error.value) == "RUNTIME_TARGET_ENABLED must be boolean, got 'maybe'"
    resolver.assert_called_once_with()


def test_paused_continuity_prevents_standard_execution(load_settings):
    from quant_platform_kit.common.live_continuity import runtime_target_fingerprint

    payload = target_payload(dry_run_only=False, execution_mode="live", live_continuity={
        "state": "PAUSED", "baseline_kind": "legacy_authorized",
        "baseline_id": "fake-baseline", "baseline_target_sha256": "a" * 64,
        "captured_at": "2026-01-01",
    })
    payload["live_continuity"]["baseline_target_sha256"] = runtime_target_fingerprint(payload)
    os.environ[U] = json.dumps(payload)
    os.environ["RUNTIME_TARGET_ENABLED"] = "true"
    settings = load_settings()
    assert settings.runtime_target.live_continuity.state == "PAUSED"
    assert settings.runtime_target_enabled is False
    assert settings.dry_run_only is True


@pytest.mark.parametrize("region,prefix,market,expected_region,expected_market", [
    (None, None, None, "DEFAULT", "US"), (" hk ", "us", None, "HK", "HK"),
    (" ", " hk ", None, "HK", "HK"), (None, "", None, "DEFAULT", "US"),
    ("fake-region", "hk", None, "FAKE-REGION", "US"),
    ("HK", None, "NASDAQ", "HK", "US"), ("US", None, "HONG_KONG", "US", "HK"),
])
def test_region_and_market_normalization(region, prefix, market, expected_region, expected_market, load_settings):
    for name, value in [("ACCOUNT_REGION", region), ("ACCOUNT_PREFIX", prefix), ("LONGBRIDGE_MARKET", market)]:
        if value is not None:
            os.environ[name] = value
    settings = load_settings()
    assert (settings.account_region, settings.market) == (expected_region, expected_market)
    assert settings.account_prefix == ("DEFAULT" if prefix is None else prefix)
    defaults = ("XHKG", "Asia/Hong_Kong", ".HK", "HKD") if expected_market == "HK" else ("NYSE", "America/New_York", ".US", "USD")
    assert (settings.market_calendar, settings.market_timezone, settings.symbol_suffix, settings.trading_currency) == defaults


@pytest.mark.parametrize("market,expected", [(None, "HK"), (" ", "HK"), ("US", "US"), ("fake-invalid", "US")])
def test_nonempty_invalid_market_blocks_target_but_not_region_fallback(market, expected, load_settings):
    os.environ[U] = json.dumps(target_payload(market="HK", market_calendar="XHKG", market_timezone="Asia/Hong_Kong"))
    os.environ["ACCOUNT_REGION"] = "US"
    if market is not None:
        os.environ["LONGBRIDGE_MARKET"] = market
    settings = load_settings()
    assert settings.market == expected
    assert (settings.market_calendar, settings.market_timezone) == ("XHKG", "Asia/Hong_Kong")


@pytest.mark.parametrize("suffix,expected", [(None, ".HK"), ("", ""), (" ", ""), ("us", ".US"), (".us", ".US")])
def test_field_specific_market_overrides(suffix, expected, load_settings):
    os.environ[U] = json.dumps(target_payload(
        market="HK", market_calendar="XHKG", market_timezone="Asia/Hong_Kong",
        symbol_suffix=".FAKE", trading_currency="FAKE",
    ))
    os.environ.update({"LONGBRIDGE_MARKET_CALENDAR": " fake-calendar ", "LONGBRIDGE_MARKET_TIMEZONE": " UTC ", "LONGBRIDGE_TRADING_CURRENCY": " usd "})
    if suffix is not None:
        os.environ["LONGBRIDGE_SYMBOL_SUFFIX"] = suffix
    settings = load_settings()
    assert (settings.market_calendar, settings.market_timezone) == ("fake-calendar", "UTC")
    assert (settings.symbol_suffix, settings.trading_currency) == (expected, "USD")
    os.environ["LONGBRIDGE_TRADING_CURRENCY"] = " "
    assert load_settings().trading_currency == "HKD"


def test_market_helper_attribute_then_selected_json_without_alias_merge():
    os.environ[Q] = json.dumps({"market_calendar": "fake-qsl-calendar"})
    os.environ[U] = json.dumps({"market_timezone": "UTC"})
    assert config._runtime_target_market_value(SimpleNamespace(market_calendar=" fake-attribute "), "market_calendar") == "fake-attribute"
    assert config._runtime_target_market_value(SimpleNamespace(), "market_calendar") == "fake-qsl-calendar"
    assert config._runtime_target_market_value(SimpleNamespace(), "market_timezone") is None
    os.environ[Q] = "{"
    assert config._runtime_target_market_value(SimpleNamespace(), "market_calendar") is None


@pytest.mark.parametrize("name,field,default", [
    ("LONGBRIDGE_MIN_RESERVED_CASH_USD", "reserved_cash_floor_usd", 0.0),
    ("LONGBRIDGE_RESERVED_CASH_RATIO", "reserved_cash_ratio", 0.0),
    ("LONGBRIDGE_MIN_ORDER_NOTIONAL_USD", "min_order_notional_usd", 100.0),
])
@pytest.mark.parametrize("qsl,plain,expected", [("0.5", "0.7", 0.5), ("", "0.7", 0.7), (" ", "0.7", None), ("0", "0.7", 0.0)])
def test_reserve_and_minimum_alias_defaults(name, field, default, qsl, plain, expected, load_settings):
    os.environ.update({f"QSL_{name}": qsl, name: plain})
    assert getattr(load_settings(), field) == (default if expected is None else expected)


@pytest.mark.parametrize("name", ["LONGBRIDGE_MIN_RESERVED_CASH_USD", "LONGBRIDGE_RESERVED_CASH_RATIO", "LONGBRIDGE_MIN_ORDER_NOTIONAL_USD"])
@pytest.mark.parametrize("raw,reason", [("-1", "must be non-negative, got -1.0"), ("nan", "must be finite, got nan"), ("inf", "must be finite, got inf"), ("-inf", "must be finite, got -inf"), ("fake", "could not convert string to float: 'fake'")])
def test_reserve_and_minimum_validation(name, raw, reason, load_settings):
    os.environ[name] = raw
    with pytest.raises(ValueError) as error:
        load_settings()
    assert str(error.value) == (reason if raw == "fake" else f"{name} {reason}")


def test_reserve_ratio_accepts_one_but_rejects_larger(load_settings):
    os.environ["LONGBRIDGE_RESERVED_CASH_RATIO"] = "1"
    assert load_settings().reserved_cash_ratio == 1.0
    os.environ["LONGBRIDGE_RESERVED_CASH_RATIO"] = "1.1"
    with pytest.raises(ValueError) as error:
        load_settings()
    assert str(error.value) == "LONGBRIDGE_RESERVED_CASH_RATIO must be in [0,1], got 1.1"


@pytest.mark.parametrize("qsl,plain,expected", [(None, None, 1000.0), ("2", "3", 2.0), ("", "3", 3.0), (" ", "3", 1000.0), ("-1", None, 0.0), ("nan", None, 0.0), ("-inf", None, 0.0), ("inf", None, math.inf)])
def test_safe_haven_threshold_keeps_existing_clamp(qsl, plain, expected, load_settings):
    name = "LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD"
    for key, value in [(f"QSL_{name}", qsl), (name, plain)]:
        if value is not None:
            os.environ[key] = value
    assert load_settings().safe_haven_cash_substitute_threshold_usd == expected


@pytest.mark.parametrize("values,expected", [
    ({}, True), ({"CASH_ONLY_EXECUTION": "false"}, False),
    ({"QSL_CASH_ONLY_EXECUTION": "false", "CASH_ONLY_EXECUTION": "true"}, False),
    ({"LONGBRIDGE_CASH_ONLY_EXECUTION": "true", "QSL_CASH_ONLY_EXECUTION": "false"}, True),
    ({"QSL_LONGBRIDGE_CASH_ONLY_EXECUTION": "false", "LONGBRIDGE_CASH_ONLY_EXECUTION": "true"}, False),
    ({"QSL_LONGBRIDGE_CASH_ONLY_EXECUTION": " ", "LONGBRIDGE_CASH_ONLY_EXECUTION": "false", "CASH_ONLY_EXECUTION": "true"}, True),
    ({"QSL_LONGBRIDGE_CASH_ONLY_EXECUTION": "", "LONGBRIDGE_CASH_ONLY_EXECUTION": "false"}, False),
    ({"LONGBRIDGE_CASH_ONLY_EXECUTION": "maybe"}, False),
])
def test_cash_only_platform_pair_precedes_generic_pair(values, expected, load_settings):
    os.environ.update(values)
    assert load_settings().cash_only_execution is expected


@pytest.mark.parametrize("name,field,qsl,plain,expected", [
    ("INCOME_THRESHOLD_USD", "income_threshold_usd", "12", "13", 12.0),
    ("INCOME_LAYER_ENABLED", "income_layer_enabled", "false", "true", False),
    ("INCOME_LAYER_MAX_RATIO", "income_layer_max_ratio", "0.2", "0.3", 0.2),
    ("DCA_BASE_INVESTMENT_USD", "dca_base_investment_usd", "10", "20", 10.0),
    ("DCA_MODE", "dca_mode", "ordinary_dca", "smart", "fixed"),
    ("IBIT_ZSCORE_EXIT_MODE", "ibit_zscore_exit_mode", "shadow", "disabled", "paper"),
    ("IBIT_ZSCORE_EXIT_PARKING_SYMBOL", "ibit_zscore_exit_parking_symbol", "sgov", "boxx", "SGOV"),
])
def test_shared_ambient_override_families_keep_qsl_precedence(name, field, qsl, plain, expected, load_settings):
    os.environ.update({f"QSL_{name}": qsl, name: plain})
    assert getattr(load_settings(), field) == expected
    os.environ[f"QSL_{name}"] = " "
    assert getattr(load_settings(), field) is None
    os.environ[f"QSL_{name}"] = ""
    assert getattr(load_settings(), field) is not None


@pytest.mark.parametrize("name,value,message", [
    ("INCOME_LAYER_ENABLED", "maybe", "INCOME_LAYER_ENABLED must be boolean, got 'maybe'"),
    ("INCOME_LAYER_MAX_RATIO", "nan", "INCOME_LAYER_MAX_RATIO must be in [0,1], got nan"),
    ("DCA_BASE_INVESTMENT_USD", "0", "DCA_BASE_INVESTMENT_USD must be positive, got 0.0"),
    ("DCA_MODE", "fake", "DCA_MODE must be fixed or smart, got 'fake'"),
    ("IBIT_ZSCORE_EXIT_MODE", "fake", "IBIT_ZSCORE_EXIT_MODE must be disabled, paper, or live, got 'fake'"),
    ("IBIT_ZSCORE_EXIT_PARKING_SYMBOL", "fake/symbol", "IBIT_ZSCORE_EXIT_PARKING_SYMBOL must be a symbol, got 'fake/symbol'"),
])
def test_shared_ambient_override_errors(name, value, message, load_settings):
    os.environ[name] = value
    with pytest.raises(ValueError) as error:
        load_settings()
    assert str(error.value) == message


@pytest.mark.parametrize("raw", ["nan", "inf"])
def test_positive_float_override_keeps_existing_nonfinite_behavior(raw, load_settings):
    os.environ["DCA_BASE_INVESTMENT_USD"] = raw
    value = load_settings().dca_base_investment_usd
    assert math.isnan(value) if raw == "nan" else value == math.inf


@pytest.mark.parametrize("profile,raw,expected", [
    (SAMPLE_STRATEGY_PROFILE, "{", None),
    (config.V7_LONGTERM_COMPOUNDING_PROFILE, "{", "V7 application binding is invalid JSON"),
    (config.V7_LONGTERM_COMPOUNDING_PROFILE, "[]", "V7 application binding must be an object"),
])
def test_v7_binding_is_only_read_for_v7(profile, raw, expected, load_settings, resolver):
    from application.v7_paper_application import V7PaperApplicationError

    os.environ[U] = json.dumps(target_payload(strategy_profile=profile))
    os.environ["LONGBRIDGE_V7_PAPER_APPLICATION_JSON"] = raw
    if expected is None:
        assert load_settings().strategy_profile == profile
    else:
        with pytest.raises(V7PaperApplicationError) as error:
            load_settings()
        assert str(error.value) == expected
        resolver.assert_not_called()


@pytest.mark.parametrize("raw", [None, "", " "])
def test_v7_without_binding_uses_normal_profile_rejection(raw, load_settings, resolver):
    profile = config.V7_LONGTERM_COMPOUNDING_PROFILE
    os.environ[U] = json.dumps(target_payload(strategy_profile=profile))
    if raw is not None:
        os.environ["LONGBRIDGE_V7_PAPER_APPLICATION_JSON"] = raw
    with pytest.raises(ValueError) as error:
        load_settings()
    assert str(error.value) == (
        f"Unsupported STRATEGY_PROFILE={profile!r}; supported values: "
        "global_etf_rotation, ibit_smart_dca, nasdaq_sp500_smart_dca, "
        "russell_top50_leader_rotation, soxl_soxx_trend_income, tqqq_growth_income"
    )
    resolver.assert_not_called()


@pytest.mark.parametrize("platform", ["/fake/platform.json", " "])
def test_runtime_path_precedence(platform, load_settings):
    os.environ.update({
        "LONGBRIDGE_STRATEGY_CONFIG_PATH": platform, "STRATEGY_CONFIG_PATH": "/fake/generic.json",
        "LONGBRIDGE_FEATURE_SNAPSHOT_PATH": platform, "FEATURE_SNAPSHOT_PATH": "/fake/generic.json",
        "LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH": platform, "FEATURE_SNAPSHOT_MANIFEST_PATH": "/fake/generic.json",
    })
    settings = load_settings()
    expected = platform if platform.strip() else "/fake/generic.json"
    assert (settings.strategy_config_path, settings.strategy_config_source) == (expected, "env")
    assert settings.feature_snapshot_path == settings.feature_snapshot_manifest_path == expected


def test_bundled_config_path_requires_existing_file(tmp_path):
    from quant_platform_kit.common.runtime_config import resolve_strategy_config_path

    bundled = tmp_path / "fake-bundled.json"
    assert resolve_strategy_config_path(explicit_path=None, bundled_path=str(bundled)) == (None, None)
    bundled.write_text("{}", encoding="utf-8")
    assert resolve_strategy_config_path(explicit_path=" ", bundled_path=str(bundled)) == (str(bundled), "bundled_canonical_default")
    assert resolve_strategy_config_path(explicit_path="/fake/explicit.json", bundled_path=str(bundled)) == ("/fake/explicit.json", "env")


@pytest.mark.parametrize("platform,qsl,generic,expected", [
    ("fake-platform", "fake-qsl", "fake-generic", "fake-platform"),
    ("", "fake-qsl", "fake-generic", "fake-qsl"),
    (" ", "fake-qsl", "fake-generic", " "),
    ("", "", "fake-generic", "fake-generic"),
])
def test_mount_aliases_use_raw_truthiness(platform, qsl, generic, expected, load_settings):
    os.environ.update({"LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON": platform, "QSL_STRATEGY_PLUGIN_MOUNTS_JSON": qsl, "STRATEGY_PLUGIN_MOUNTS_JSON": generic})
    assert load_settings().strategy_plugin_mounts_json == expected


def test_alert_list_aliases_trim_split_and_stably_deduplicate(load_settings):
    # Keep #664's alert helper/test file untouched; characterize only loader output.
    os.environ.update({"QSL_STRATEGY_PLUGIN_ALERT_CHANNELS": "fake-a;fake-b\nfake-a, fake-c", "STRATEGY_PLUGIN_ALERT_CHANNELS": "fake-other"})
    assert load_settings().strategy_plugin_alert_channels == ("fake-a", "fake-b", "fake-c")
    os.environ["QSL_STRATEGY_PLUGIN_ALERT_CHANNELS"] = " "
    assert load_settings().strategy_plugin_alert_channels == ()
    os.environ["QSL_STRATEGY_PLUGIN_ALERT_CHANNELS"] = ""
    assert load_settings().strategy_plugin_alert_channels == ("fake-other",)


@pytest.mark.parametrize("early,exception,message", [
    ({"LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD": "fake", Q: "{"}, ValueError, "could not convert string to float: 'fake'"),
    ({Q: "{", U: "[]"}, ValueError, "RUNTIME_TARGET_JSON must contain valid JSON"),
    ({Q: json.dumps(target_payload()), U: "{", "LONGBRIDGE_DRY_RUN_ONLY": "false"}, EnvironmentError, "RUNTIME_TARGET_JSON must be valid JSON"),
    ({"LONGBRIDGE_DRY_RUN_ONLY": "false", "RUNTIME_TARGET_ENABLED": "false"}, EnvironmentError, "LONGBRIDGE_DRY_RUN_ONLY=false conflicts with RUNTIME_TARGET_JSON.dry_run_only=true"),
])
def test_first_error_order_even_when_execution_disabled(early, exception, message, load_settings, resolver):
    os.environ.update(early)
    with pytest.raises(exception) as error:
        load_settings()
    assert str(error.value) == message
    resolver.assert_not_called()


def test_resolver_error_precedes_later_constructor_validation(load_settings, resolver):
    os.environ["LONGBRIDGE_MIN_ORDER_NOTIONAL_USD"] = "fake"
    resolver.side_effect = RuntimeError("fake-project-resolution-failure")
    with pytest.raises(RuntimeError, match="^fake-project-resolution-failure$"):
        load_settings()
    resolver.assert_called_once_with()


def test_resolver_mutations_preserve_early_values_and_affect_late_reads(load_settings, resolver):
    events = []

    def resolve_project():
        events.append("project")
        os.environ.update({
            U: json.dumps(target_payload(
                account_scope="fake-late-scope", runtime_risk_limits={"late": 1},
                market="HK", market_calendar="XHKG", market_timezone="Asia/Hong_Kong",
            )),
            "ACCOUNT_PREFIX": "fake-late-account", "ACCOUNT_REGION": "HK", "LONGBRIDGE_MARKET": "HK",
            "LONGBRIDGE_MARKET_CALENDAR": "fake-late-calendar", "LONGBRIDGE_MIN_ORDER_NOTIONAL_USD": "7",
            "LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD": "8", "LONGBRIDGE_DRY_RUN_ONLY": "false",
        })
        return "fake-project"

    resolver.side_effect = resolve_project
    original_target = config.resolve_runtime_target_from_env
    original_risk = config._load_trusted_runtime_risk_policy

    def resolve_target(**kwargs):
        events.append("target")
        return original_target(**kwargs)

    def resolve_risk():
        events.append("risk")
        return original_risk()

    with patch.object(config, "resolve_runtime_target_from_env", side_effect=resolve_target), patch.object(config, "_load_trusted_runtime_risk_policy", side_effect=resolve_risk):
        settings = load_settings()
    assert events == ["target", "risk", "project"]
    resolver.assert_called_once_with()
    assert (settings.account_prefix, settings.account_region, settings.market) == ("DEFAULT", "DEFAULT", "US")
    assert settings.runtime_target.account_scope == "fake-scope"
    assert settings.trusted_runtime_risk_policy is None
    assert settings.dry_run_only is True
    assert settings.safe_haven_cash_substitute_threshold_usd == 1000.0
    assert settings.market_calendar == "fake-late-calendar"
    assert settings.market_timezone == "Asia/Hong_Kong"
    assert settings.min_order_notional_usd == 7.0
