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


if __name__ == "__main__":
    unittest.main()
