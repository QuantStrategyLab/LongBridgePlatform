import unittest


class StrategyLoaderTests(unittest.TestCase):
    def test_load_strategy_entrypoint_resolves_global_etf_rotation(self):
        try:
            from strategy_loader import load_strategy_entrypoint_for_profile

            entrypoint = load_strategy_entrypoint_for_profile("global_etf_rotation")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(entrypoint.manifest.profile, "global_etf_rotation")
        self.assertEqual(entrypoint.manifest.required_inputs, frozenset({"market_history"}))

    def test_load_strategy_entrypoint_resolves_russell_strategy(self):
        try:
            from strategy_loader import load_strategy_entrypoint_for_profile

            entrypoint = load_strategy_entrypoint_for_profile("russell_1000_multi_factor_defensive")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(entrypoint.manifest.profile, "russell_1000_multi_factor_defensive")
        self.assertEqual(entrypoint.manifest.required_inputs, frozenset({"feature_snapshot"}))

    def test_load_strategy_entrypoint_resolves_soxl_soxx_trend_income(self):
        try:
            from strategy_loader import load_strategy_entrypoint_for_profile

            entrypoint = load_strategy_entrypoint_for_profile("soxl_soxx_trend_income")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(entrypoint.manifest.profile, "soxl_soxx_trend_income")
        self.assertEqual(
            entrypoint.manifest.default_config["managed_symbols"],
            ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        )

    def test_load_strategy_entrypoint_resolves_mega_cap_dynamic_top20(self):
        try:
            from strategy_loader import load_strategy_entrypoint_for_profile

            entrypoint = load_strategy_entrypoint_for_profile("mega_cap_leader_rotation_dynamic_top20")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(entrypoint.manifest.profile, "mega_cap_leader_rotation_dynamic_top20")
        self.assertEqual(entrypoint.manifest.required_inputs, frozenset({"feature_snapshot"}))

    def test_load_strategy_entrypoint_resolves_dynamic_mega_leveraged_pullback(self):
        try:
            from strategy_loader import load_strategy_entrypoint_for_profile

            entrypoint = load_strategy_entrypoint_for_profile("dynamic_mega_leveraged_pullback")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(entrypoint.manifest.profile, "dynamic_mega_leveraged_pullback")
        self.assertEqual(
            entrypoint.manifest.required_inputs,
            frozenset({"feature_snapshot", "market_history", "benchmark_history", "portfolio_snapshot"}),
        )

    def test_load_strategy_entrypoint_rejects_legacy_soxl_alias(self):
        try:
            from strategy_loader import load_strategy_entrypoint_for_profile

            with self.assertRaises(ValueError):
                load_strategy_entrypoint_for_profile("semiconductor_trend_income")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

    def test_load_strategy_runtime_adapter_declares_available_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("soxl_soxx_trend_income")

        self.assertEqual(
            adapter.available_inputs,
            frozenset({"derived_indicators", "portfolio_snapshot"}),
        )
        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

    def test_load_strategy_runtime_adapter_declares_global_etf_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("global_etf_rotation")

        self.assertEqual(
            adapter.available_inputs,
            frozenset({"market_history", "portfolio_snapshot"}),
        )
        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

    def test_load_strategy_runtime_adapter_declares_russell_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("russell_1000_multi_factor_defensive")

        self.assertEqual(
            adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")
        self.assertEqual(adapter.status_icon, "📏")

    def test_load_strategy_runtime_adapter_declares_mega_cap_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("mega_cap_leader_rotation_dynamic_top20")

        self.assertEqual(
            adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")
        self.assertTrue(adapter.require_snapshot_manifest)
        self.assertEqual(adapter.status_icon, "👑")

    def test_load_strategy_runtime_adapter_declares_dynamic_mega_leveraged_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("dynamic_mega_leveraged_pullback")

        self.assertEqual(
            adapter.available_inputs,
            frozenset({"feature_snapshot", "market_history", "benchmark_history", "portfolio_snapshot"}),
        )
        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")
        self.assertTrue(adapter.require_snapshot_manifest)
        self.assertEqual(adapter.status_icon, "2x")

    def test_load_strategy_runtime_adapter_declares_hybrid_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("tqqq_growth_income")

        self.assertEqual(
            adapter.available_inputs,
            frozenset({"benchmark_history", "portfolio_snapshot"}),
        )
        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")

    def test_load_strategy_runtime_adapter_declares_tech_snapshot_inputs(self):
        from strategy_loader import load_strategy_runtime_adapter_for_profile

        adapter = load_strategy_runtime_adapter_for_profile("tech_communication_pullback_enhancement")

        self.assertEqual(
            adapter.available_inputs,
            frozenset({"feature_snapshot", "portfolio_snapshot"}),
        )
        self.assertEqual(adapter.portfolio_input_name, "portfolio_snapshot")
        self.assertTrue(adapter.require_snapshot_manifest)


if __name__ == "__main__":
    unittest.main()
