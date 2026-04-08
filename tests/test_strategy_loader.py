import unittest


class StrategyLoaderTests(unittest.TestCase):
    def test_load_strategy_entrypoint_resolves_semiconductor_rotation_income(self):
        try:
            from strategy_loader import load_strategy_entrypoint_for_profile

            entrypoint = load_strategy_entrypoint_for_profile("semiconductor_rotation_income")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(entrypoint.manifest.profile, "semiconductor_rotation_income")
        self.assertEqual(
            entrypoint.manifest.default_config["managed_symbols"],
            ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        )

    def test_load_strategy_entrypoint_resolves_semiconductor_rotation_income_alias(self):
        try:
            from strategy_loader import load_strategy_entrypoint_for_profile

            entrypoint = load_strategy_entrypoint_for_profile("semiconductor_trend_income")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(
            entrypoint.manifest.profile,
            "semiconductor_rotation_income",
        )

    def test_load_strategy_runtime_adapter_declares_available_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("semiconductor_rotation_income")

        self.assertEqual(adapter.available_inputs, frozenset({"indicators", "account_state"}))

    def test_load_strategy_runtime_adapter_declares_hybrid_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("hybrid_growth_income")

        self.assertEqual(adapter.available_inputs, frozenset({"qqq_history", "snapshot"}))
        self.assertEqual(adapter.portfolio_input_name, "snapshot")


if __name__ == "__main__":
    unittest.main()
