"""B08 batch 1: explicit startup config normalization (no os.environ writes)."""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from application.startup_config import (
    normalize_monitor_dispatch_targets,
    normalize_plugin_mounts_strategy,
    overlay_env,
)

PROFILE = "soxl_soxx_trend_income"


# --- Verbatim copies of the pre-B08 inline logic in main.py (main@fe16142) ---
def _legacy_plugin_mounts(raw_mounts_json, strategy_profile, log):
    if not raw_mounts_json:
        return raw_mounts_json
    try:
        mounts = json.loads(raw_mounts_json)
    except json.JSONDecodeError:
        return raw_mounts_json
    if not isinstance(mounts, dict):
        return raw_mounts_json
    plugins = mounts.get("strategy_plugins")
    if not isinstance(plugins, list):
        return raw_mounts_json
    changed = False
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        old = entry.get("strategy")
        if old and old != strategy_profile:
            entry["strategy"] = strategy_profile
            changed = True
            log(f"[config-sync] Plugin mount strategy corrected: {old} → {strategy_profile}")
    return json.dumps(mounts, ensure_ascii=False) if changed else raw_mounts_json


def _legacy_monitor_env(environ, strategy_profile, log):
    """Returns the env after the legacy in-place mutation."""
    environ = dict(environ)
    for _env_name in ("MONITOR_DISPATCH_TARGETS_JSON", "LONGBRIDGE_MONITOR_DISPATCH_TARGETS_JSON"):
        _raw = environ.get(_env_name)
        if _raw:
            try:
                _targets = json.loads(_raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(_targets, dict):
                continue
            _entries = _targets.get("targets")
            if not isinstance(_entries, list):
                continue
            _changed = False
            for _entry in _entries:
                if isinstance(_entry, dict):
                    _old = _entry.get("strategy_profile")
                    if _old and _old != strategy_profile:
                        _entry["strategy_profile"] = strategy_profile
                        _changed = True
                        log(f"[config-sync] {_env_name} strategy_profile corrected: {_old} → {strategy_profile}")
            if _changed:
                environ[_env_name] = json.dumps(_targets, ensure_ascii=False)
    return environ


_PLUGIN_INPUTS = [
    None,
    "",
    "not json",
    "[]",
    json.dumps({"strategy_plugins": "x"}),
    json.dumps({"strategy_plugins": [{"strategy": PROFILE, "plugin": "a"}]}),
    json.dumps({"strategy_plugins": [{"strategy": "tqqq_growth_income", "plugin": "a"}, "junk", {"plugin": "b"}]}),
    json.dumps({"strategy_plugins": [{"strategy": "旧", "x": 1}, {"strategy": "other"}], "k": "v"}, ensure_ascii=False),
    '{"strategy_plugins": [{"strategy": "soxl_soxx_trend_income"}]}',
]

_MONITOR_INPUTS = [
    {},
    {"MONITOR_DISPATCH_TARGETS_JSON": ""},
    {"MONITOR_DISPATCH_TARGETS_JSON": "bad json"},
    {"MONITOR_DISPATCH_TARGETS_JSON": "[]"},
    {"MONITOR_DISPATCH_TARGETS_JSON": json.dumps({"targets": "x"})},
    {"MONITOR_DISPATCH_TARGETS_JSON": json.dumps({"targets": [{"strategy_profile": PROFILE}]})},
    {
        "MONITOR_DISPATCH_TARGETS_JSON": json.dumps(
            {"targets": [{"strategy_profile": "tqqq_growth_income", "service_name": "svc"}, 3, {"service_name": "b"}]}
        ),
        "LONGBRIDGE_MONITOR_DISPATCH_TARGETS_JSON": json.dumps({"targets": [{"strategy_profile": "旧"}]}, ensure_ascii=False),
        "UNRELATED": "1",
    },
    {"LONGBRIDGE_MONITOR_DISPATCH_TARGETS_JSON": json.dumps({"targets": [{"strategy_profile": "x"}], "extra": True})},
]


class StartupConfigParityTests(unittest.TestCase):
    def test_plugin_mount_normalization_matches_legacy(self):
        for raw in _PLUGIN_INPUTS:
            with self.subTest(raw=raw):
                legacy_logs, new_logs = [], []
                expected = _legacy_plugin_mounts(raw, PROFILE, legacy_logs.append)
                actual, corrections = normalize_plugin_mounts_strategy(
                    raw, strategy_profile=PROFILE, log=new_logs.append
                )
                self.assertEqual(actual, expected)
                self.assertEqual(new_logs, legacy_logs)
                self.assertEqual(len(corrections), len(legacy_logs))
                self.assertTrue(all(c.adopted == PROFILE for c in corrections))

    def test_monitor_dispatch_normalization_matches_legacy_without_env_mutation(self):
        for env in _MONITOR_INPUTS:
            with self.subTest(env=env):
                snapshot = dict(env)
                legacy_logs, new_logs = [], []
                expected_env = _legacy_monitor_env(env, PROFILE, legacy_logs.append)
                overrides, corrections = normalize_monitor_dispatch_targets(
                    env, strategy_profile=PROFILE, log=new_logs.append
                )
                self.assertEqual(env, snapshot, "input env must not be mutated")
                self.assertEqual(overlay_env(env, overrides), expected_env)
                self.assertEqual(new_logs, legacy_logs)
                self.assertEqual(len(corrections), len(legacy_logs))
                self.assertEqual(set(overrides), {k for k in env if expected_env[k] != env[k]})


class MainImportDoesNotMutateEnvTests(unittest.TestCase):
    def test_import_records_receipt_and_leaves_os_environ_untouched(self):
        from tests.test_request_handling import load_module

        raw = json.dumps({"targets": [{"strategy_profile": "tqqq_growth_income", "service_name": "svc"}]})
        with patch.dict(os.environ, {"MONITOR_DISPATCH_TARGETS_JSON": raw}, clear=False):
            module = load_module()
            self.assertEqual(os.environ["MONITOR_DISPATCH_TARGETS_JSON"], raw)
            self.assertEqual(tuple(module.MONITOR_DISPATCH_ENV_OVERRIDES), ("MONITOR_DISPATCH_TARGETS_JSON",))
            receipt = module.STARTUP_CONFIG_RECEIPT.as_dict()
            self.assertEqual(receipt["env_overrides"], ["MONITOR_DISPATCH_TARGETS_JSON"])
            self.assertEqual(
                receipt["corrections"],
                [
                    {
                        "source": "MONITOR_DISPATCH_TARGETS_JSON",
                        "field": "targets[].strategy_profile",
                        "original": "tqqq_growth_income",
                        "adopted": module.STRATEGY_PROFILE,
                    }
                ],
            )
            targets = module._load_monitor_dispatch_targets()
        self.assertEqual(targets, [{"strategy_profile": module.STRATEGY_PROFILE, "service_name": "svc"}])

    def test_import_without_corrections_has_empty_receipt(self):
        from tests.test_request_handling import load_module

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MONITOR_DISPATCH_TARGETS_JSON", None)
            os.environ.pop("LONGBRIDGE_MONITOR_DISPATCH_TARGETS_JSON", None)
            module = load_module()
        self.assertEqual(module.MONITOR_DISPATCH_ENV_OVERRIDES, {})
        self.assertEqual(module.STARTUP_CONFIG_RECEIPT.corrections, ())


@dataclass(frozen=True)
class _FrozenSettings:
    strategy_plugin_mounts_json: str | None


class AdoptStartupConfigSettingsTests(unittest.TestCase):
    def test_frozen_dataclass_settings_are_replaced_not_mutated(self):
        from tests.test_request_handling import load_module

        module = load_module()
        raw = json.dumps({"strategy_plugins": [{"strategy": "other"}]})
        original = _FrozenSettings(strategy_plugin_mounts_json=raw)
        adopted, overrides, receipt = module._adopt_startup_config(original, {})
        self.assertEqual(original.strategy_plugin_mounts_json, raw)
        self.assertIsNot(adopted, original)
        self.assertEqual(
            adopted.strategy_plugin_mounts_json,
            _legacy_plugin_mounts(raw, module.STRATEGY_PROFILE, lambda _m: None),
        )
        self.assertEqual(overrides, {})
        self.assertEqual(len(receipt.corrections), 1)
