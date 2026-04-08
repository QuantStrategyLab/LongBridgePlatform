import unittest
from unittest.mock import patch

import strategy_runtime as strategy_runtime_module
from quant_platform_kit.strategy_contracts import (
    StrategyDecision,
    StrategyManifest,
    StrategyRuntimeAdapter,
)


class _FakeEntrypoint:
    def __init__(self):
        self.manifest = StrategyManifest(
            profile="semiconductor_rotation_income",
            domain="us_equity",
            display_name="Semiconductor Rotation Income",
            description="test entrypoint",
            required_inputs=frozenset({"indicators", "account_state"}),
            default_config={"managed_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI")},
        )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_message": "ok"})


class StrategyRuntimeTests(unittest.TestCase):
    def test_runtime_exposes_managed_symbols_and_injects_translator(self):
        entrypoint = _FakeEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(),
            merged_runtime_config={"managed_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI")},
        )

        result = runtime.evaluate(
            indicators={"soxl": {"price": 1.0, "ma_trend": 2.0}},
            account_state={"available_cash": 100.0},
            translator=lambda key, **_kwargs: key,
        )

        self.assertEqual(runtime.managed_symbols, ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"))
        self.assertEqual(entrypoint.ctx.market_data["account_state"]["available_cash"], 100.0)
        self.assertIn("translator", entrypoint.ctx.runtime_config)
        self.assertEqual(result.metadata["strategy_profile"], "semiconductor_rotation_income")

    def test_load_strategy_runtime_uses_entrypoint_default_config(self):
        entrypoint = _FakeEntrypoint()

        with patch.object(strategy_runtime_module, "load_strategy_entrypoint_for_profile", return_value=entrypoint) as mock_loader:
            with patch.object(
                strategy_runtime_module,
                "load_strategy_runtime_adapter_for_profile",
                return_value=StrategyRuntimeAdapter(),
            ):
                runtime = strategy_runtime_module.load_strategy_runtime("semiconductor_rotation_income")

        mock_loader.assert_called_once_with("semiconductor_rotation_income")
        self.assertIs(runtime.entrypoint, entrypoint)
        self.assertEqual(runtime.managed_symbols, ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"))


if __name__ == "__main__":
    unittest.main()
