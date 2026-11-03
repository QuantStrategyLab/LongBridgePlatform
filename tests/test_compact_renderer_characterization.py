"""Characterize compact composition through the public offline renderers."""

from contextlib import ExitStack
import unittest
from unittest.mock import patch

from notifications import renderers


class CompactRendererCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.supplemental = ["synthetic supplemental line"]
        self.currencies = {"EXAMPLE": "USD"}
        events = self.events

        class RecordingExecution(dict):
            def get(self, key, default=None):
                events.append(("get", key, default))
                return super().get(key, default)

        self.execution = RecordingExecution({
            "dashboard_text": "raw dashboard",
            "cash_only_execution": True,
            "position_currency_by_symbol": self.currencies,
            "compact_supplemental_lines": self.supplemental,
        })
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in (
            "_append_dashboard_block", "_append_timing_lines",
            "_append_signal_snapshot_line", "_append_source_input_line",
            "_append_status_lines", "_append_heartbeat_account_lines",
        ):
            self.stack.enter_context(patch.object(renderers, name, return_value=None))
        self.stack.enter_context(patch.object(renderers, "_compact_total_assets_line", return_value="assets"))
        self.stack.enter_context(patch.object(renderers, "_heartbeat_account_equity_line", return_value="account assets"))
        self.stack.enter_context(patch.object(renderers, "_heartbeat_outcome_key", return_value="outcome"))
        self.stack.enter_context(patch.object(renderers, "_format_dashboard_text", side_effect=self.format_dashboard))
        self.stack.enter_context(patch.object(renderers, "_translator_uses_zh", side_effect=self.locale))
        self.stack.enter_context(patch.object(renderers, "adapt_compact_sections", side_effect=self.adapt))
        self.use_zh = False

    def translate(self, key, **kwargs):
        self.events.append(("translate", key, kwargs))
        return key + (":" + ",".join(f"{name}={value}" for name, value in kwargs.items()) if kwargs else "")

    def format_dashboard(self, text, *, translator, cash_only_execution, position_currency_by_symbol):
        self.events.append(("format", text, translator, cash_only_execution, position_currency_by_symbol))
        return "formatted dashboard"

    def locale(self, translator):
        self.events.append(("locale", translator))
        return self.use_zh

    def adapt(self, text, *, locale, supplemental_lines):
        self.events.append(("adapt", text, locale, supplemental_lines))
        return ["section A", "section B"]

    def invoke(self, kind, **overrides):
        kwargs = {
            "execution": self.execution, "skip_logs": [], "note_logs": [],
            "translator": self.translate, "separator": "---",
            "strategy_display_name": "Synthetic", "dry_run_only": True,
            "extra_notification_lines": ["  extra  "],
        }
        if kind == "rebalance":
            kwargs["logs"] = ["  executed  "]
        kwargs.update(overrides)
        return getattr(renderers, f"render_{kind}_notification")(**kwargs)

    def composition_events(self):
        return [event for event in self.events if event[0] != "translate"]

    def test_exact_public_outputs_and_composition_order(self):
        for kind, assets, suffix in (
            ("rebalance", "assets", "executed"),
            ("heartbeat", "account assets", "outcome"),
        ):
            with self.subTest(kind=kind):
                self.events.clear()
                result = self.invoke(kind)
                self.assertEqual(result.compact_text, (
                    f"{kind}_title\nstrategy_label:name=Synthetic\ndry_run_banner\n"
                    f"{assets}\nsection A\nsection B\n{suffix}"
                ))
                detailed_suffix = "order_logs_title\n  -   executed  " if kind == "rebalance" else "---\noutcome"
                self.assertEqual(result.detailed_text, (
                    f"{kind}_title\nstrategy_label:name=Synthetic\ndry_run_banner\nextra\n---\n{detailed_suffix}"
                ))
                self.assertEqual(self.composition_events(), [
                    ("get", "dashboard_text", None),
                    ("get", "cash_only_execution", True),
                    ("get", "position_currency_by_symbol", None),
                    ("format", "raw dashboard", self.translate, True, self.currencies),
                    ("locale", self.translate),
                    ("get", "compact_supplemental_lines", ()),
                    ("adapt", "formatted dashboard", "en", self.supplemental),
                ])
                self.assertIs(self.composition_events()[3][-1], self.currencies)
                self.assertIs(self.composition_events()[-1][-1], self.supplemental)

    def test_locale_selection_happens_after_dashboard_formatting(self):
        self.use_zh = True
        for kind in ("rebalance", "heartbeat"):
            with self.subTest(kind=kind):
                self.events.clear()
                self.invoke(kind)
                self.assertEqual(self.composition_events()[-1], ("adapt", "formatted dashboard", "zh", self.supplemental))
                self.assertEqual([event[0] for event in self.composition_events()], ["get", "get", "get", "format", "locale", "get", "adapt"])

    def test_missing_optional_fields_retain_original_defaults(self):
        self.execution.clear()
        for kind in ("rebalance", "heartbeat"):
            with self.subTest(kind=kind):
                self.events.clear()
                self.invoke(kind)
                self.assertEqual(self.composition_events()[3], ("format", None, self.translate, True, None))
                self.assertEqual(self.composition_events()[-1], ("adapt", "formatted dashboard", "en", ()))
                self.assertEqual(self.execution, {})

    def test_explicit_none_and_falsey_values_are_not_replaced_by_defaults(self):
        self.execution.update(cash_only_execution=None, position_currency_by_symbol=None, compact_supplemental_lines=None)
        for kind in ("rebalance", "heartbeat"):
            with self.subTest(kind=kind):
                self.events.clear()
                self.invoke(kind)
                self.assertEqual(self.composition_events()[3], ("format", "raw dashboard", self.translate, False, None))
                self.assertEqual(self.composition_events()[-1], ("adapt", "formatted dashboard", "en", None))

    def test_cash_flag_truth_conversion_occurs_before_currency_lookup(self):
        events = self.events

        class CashFlag:
            def __bool__(self):
                events.append(("bool", "cash"))
                return True

        self.execution["cash_only_execution"] = CashFlag()
        for kind in ("rebalance", "heartbeat"):
            with self.subTest(kind=kind):
                self.events.clear()
                self.invoke(kind)
                self.assertEqual(self.composition_events()[:4], [
                    ("get", "dashboard_text", None), ("get", "cash_only_execution", True),
                    ("bool", "cash"), ("get", "position_currency_by_symbol", None),
                ])

    def test_lazy_adapter_results_are_consumed_before_the_final_line(self):
        for kind in ("rebalance", "heartbeat"):
            with self.subTest(kind=kind):
                self.events.clear()

                def adapt(text, *, locale, supplemental_lines):
                    self.events.append(("adapt", text, locale, supplemental_lines))

                    def sections():
                        self.events.append(("yield", "first"))
                        yield "first"
                        self.events.append(("yield", "second"))
                        yield "second"

                    return sections()

                with patch.object(renderers, "adapt_compact_sections", side_effect=adapt):
                    result = self.invoke(kind)
                self.assertIn("\nfirst\nsecond\n", result.compact_text)
                self.assertEqual(self.composition_events()[-2:], [("yield", "first"), ("yield", "second")])
                if kind == "heartbeat":
                    self.assertEqual(self.events[-1], ("translate", "outcome", {}))

    def test_composition_errors_keep_exception_identity_and_stop_later_calls(self):
        for kind in ("rebalance", "heartbeat"):
            for failing in ("_format_dashboard_text", "_translator_uses_zh", "adapt_compact_sections"):
                with self.subTest(kind=kind, failing=failing):
                    self.events.clear()
                    error = ValueError("synthetic formatting failure")
                    original = dict(self.execution)
                    with patch.object(renderers, failing, side_effect=error):
                        with self.assertRaises(ValueError) as observed:
                            self.invoke(kind)
                    self.assertIs(observed.exception, error)
                    self.assertEqual(dict(self.execution), original)
                    if failing == "_format_dashboard_text":
                        self.assertFalse(any(event[0] in {"locale", "adapt"} for event in self.events))
                    elif failing == "_translator_uses_zh":
                        self.assertFalse(any(event[:2] == ("get", "compact_supplemental_lines") for event in self.events))

    def test_adapter_iteration_error_is_not_swallowed(self):
        for kind in ("rebalance", "heartbeat"):
            with self.subTest(kind=kind):
                error = RuntimeError("synthetic iteration failure")

                def sections():
                    yield "first"
                    raise error

                with patch.object(renderers, "adapt_compact_sections", side_effect=lambda *args, **kwargs: sections()):
                    with self.assertRaises(RuntimeError) as observed:
                        self.invoke(kind)
                self.assertIs(observed.exception, error)


if __name__ == "__main__":
    unittest.main()
