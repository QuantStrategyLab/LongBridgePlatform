import unittest


class StrategyLoaderTests(unittest.TestCase):
    def test_load_allocation_module_resolves_semiconductor_rotation_income(self):
        try:
            from strategy_loader import load_allocation_module

            module = load_allocation_module("semiconductor_rotation_income")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(
            module.__name__,
            "us_equity_strategies.strategies.semiconductor_rotation_income",
        )

    def test_load_allocation_module_resolves_semiconductor_rotation_income_alias(self):
        try:
            from strategy_loader import load_allocation_module

            module = load_allocation_module("semiconductor_trend_income")
        except ModuleNotFoundError as exc:
            if exc.name in {"numpy", "pandas"}:
                self.skipTest(f"{exc.name} is not installed")
            raise

        self.assertEqual(
            module.__name__,
            "us_equity_strategies.strategies.semiconductor_rotation_income",
        )


if __name__ == "__main__":
    unittest.main()
