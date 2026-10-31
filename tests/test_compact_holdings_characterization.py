"""Characterize compact holdings through the public notification adapter."""

import unittest

from notifications.compact_adapter import adapt_compact_sections


class CompactHoldingsCharacterizationTests(unittest.TestCase):
    def test_each_supported_header_starts_holdings(self):
        for header in (
            "💼 持仓", "💼 策略持仓", "💼 当前持仓", "💼 Holdings",
            "💼 Current Holdings", "💼 Strategy Holdings", "💼 Strategy holdings",
        ):
            with self.subTest(header=header):
                self.assertEqual(
                    adapt_compact_sections(f"  {header}  \n- DEMO: 2 shares", locale="en"),
                    ("💼 Holdings", "- DEMO: 2 shares"),
                )

    def test_unsupported_headers_do_not_start_holdings(self):
        for header in ("", "💼 holdings", "💼 HOLDINGS", "Holdings", "💼 Holdings:"):
            with self.subTest(header=header):
                self.assertEqual(adapt_compact_sections(f"{header}\nDEMO: 2 shares", locale="en"), ())

    def test_section_terminators_stop_before_later_holdings(self):
        for marker in ("━", "📌", "💵", "📊", "🎯", "🧾", "⏱", "🧩"):
            with self.subTest(marker=marker):
                dashboard = f"💼 Holdings\nFIRST: 2 shares\n {marker} Next\n💼 Holdings\nSECOND: 3 shares"
                self.assertEqual(
                    adapt_compact_sections(dashboard, locale="en"),
                    ("💼 Holdings", "- FIRST: 2 shares"),
                )

    def test_blank_and_repeated_headers_keep_the_current_section(self):
        dashboard = "\n".join((
            "IGNORED: 1 share", "💼 Holdings", "FIRST: 2 shares", "", " \t",
            "unknown header", "💼 策略持仓", "SECOND: 3 shares", "🎲 THIRD: 4 shares",
        ))
        self.assertEqual(adapt_compact_sections(dashboard, locale="en"), (
            "💼 Holdings", "- FIRST: 2 shares", "- SECOND: 3 shares", "- 🎲 THIRD: 4 shares",
        ))

    def test_symbols_and_separator_precedence_preserve_original_text(self):
        for raw, rendered in (
            (" --•  MiXeD  :  2 shares  ", "- MiXeD: 2 shares"),
            (" DEMO： 2 shares : note", "- DEMO: 2 shares : note"),
            (" A:B： 2 shares", "- A:B: 2 shares"),
            (" A:B: 2 shares", "- A: B: 2 shares"),
            ("•- DEMO: 2股", "- DEMO: 2 shares"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(
                    adapt_compact_sections(f"💼 Holdings\n{raw}", locale="en"),
                    ("💼 Holdings", rendered),
                )

    def test_missing_or_zero_details_are_omitted(self):
        for detail in ("", "0 shares", "-0.00", "+0,000.00", "0.000000000001", "-0.000000000001", "no amount", "NaN", "Infinity"):
            with self.subTest(detail=detail):
                self.assertEqual(adapt_compact_sections(f"💼 Holdings\nDEMO: {detail}", locale="en"), ())
        self.assertEqual(adapt_compact_sections("💼 Holdings\n: 2 shares\n：2股\n2 shares", locale="en"), ())

    def test_nonzero_detection_and_numeric_text_are_preserved(self):
        for detail in (
            "0.0000000000011", "-0.0000000000011", "+1,000.25", "1,2,3", "0,0,0 / 2 shares",
            "$0.00 / 2 shares", "$2.00 / 0 shares", "code 2026", "-2 shares",
        ):
            with self.subTest(detail=detail):
                self.assertEqual(
                    adapt_compact_sections(f"💼 Holdings\nDEMO: {detail}", locale="en"),
                    ("💼 Holdings", f"- DEMO: {detail}"),
                )

    def test_localization_keeps_existing_quantity_and_locale_rules(self):
        for locale, detail, heading, rendered in (
            ("en", "1股", "💼 Holdings", "1 share"),
            ("en", "-1股", "💼 Holdings", "-1 share"),
            ("en", "+1.0股", "💼 Holdings", "+1.0 share"),
            ("en", "1,000股", "💼 Holdings", "1,000 shares"),
            ("en", "0股 / $2", "💼 Holdings", "0 shares / $2"),
            ("zh", "1 share / 2 SHARES", "💼 持仓", "1股 / 2股"),
            ("ZH-Hant", "2 shares", "💼 持仓", "2股"),
            ("zh-extra", "2shares", "💼 持仓", "2shares"),
            ("fr", "2股", "💼 Holdings", "2 shares"),
            (None, "2股", "💼 Holdings", "2 shares"),
        ):
            with self.subTest(locale=locale, detail=detail):
                self.assertEqual(
                    adapt_compact_sections(f"💼 Holdings\nDEMO: {detail}", locale=locale),
                    (heading, f"- DEMO: {rendered}"),
                )

    def test_supplements_keep_order_and_deduplicate_against_rendered_holdings(self):
        supplements = [None, False, 0, "", " \t", " 💼 Holdings ", " - DEMO: 2 shares ", " note ", "note", 7, "7"]
        original = list(supplements)
        self.assertEqual(
            adapt_compact_sections("💼 Holdings\nDEMO: 2 shares\nDEMO: 2 shares", locale="en", supplemental_lines=supplements),
            ("💼 Holdings", "- DEMO: 2 shares", "- DEMO: 2 shares", "note", "7"),
        )
        self.assertEqual(supplements, original)

    def test_falsey_dashboards_skip_string_conversion_and_still_accept_supplements(self):
        class FalseDashboard:
            def __bool__(self):
                return False

            def __str__(self):
                raise AssertionError("falsey dashboard must not be stringified")

        for dashboard in (None, "", False, 0, FalseDashboard()):
            with self.subTest(dashboard_type=type(dashboard).__name__):
                self.assertEqual(adapt_compact_sections(dashboard, locale="en", supplemental_lines=("note",)), ("note",))

    def test_dashboard_locale_and_supplement_evaluation_order(self):
        events = []

        class Dashboard:
            def __bool__(self):
                events.append("dashboard-bool")
                return True

            def __str__(self):
                events.append("dashboard-str")
                return "💼 Holdings\nFIRST: 1股\nSECOND: 2股"

        class Locale:
            def __str__(self):
                events.append("locale-str")
                return "en"

        class Supplement:
            def __bool__(self):
                events.append("supplement-bool")
                return True

            def __str__(self):
                events.append("supplement-str")
                return " note "

        def supplements():
            events.append("supplements-start")
            yield Supplement()
            events.append("supplements-end")

        self.assertEqual(adapt_compact_sections(Dashboard(), locale=Locale(), supplemental_lines=supplements()), (
            "💼 Holdings", "- FIRST: 1 share", "- SECOND: 2 shares", "note",
        ))
        self.assertEqual(events, [
            "dashboard-bool", "dashboard-str", "locale-str", "locale-str", "locale-str",
            "supplements-start", "supplement-bool", "supplement-str", "supplements-end",
        ])

    def test_locale_is_not_evaluated_without_nonzero_holdings(self):
        class Locale:
            def __str__(self):
                raise AssertionError("unused locale must not be evaluated")

        self.assertEqual(adapt_compact_sections("💼 Holdings\nDEMO: 0股", locale=Locale(), supplemental_lines=("note",)), ("note",))

    def test_dashboard_failure_preserves_exception_identity_and_stops_later_inputs(self):
        for stage in ("bool", "str"):
            with self.subTest(stage=stage):
                error = ValueError("synthetic dashboard failure")
                events = []

                class Dashboard:
                    def __bool__(self):
                        events.append("bool")
                        if stage == "bool":
                            raise error
                        return True

                    def __str__(self):
                        events.append("str")
                        raise error

                def supplements():
                    events.append("supplements")
                    yield "note"

                with self.assertRaises(ValueError) as observed:
                    adapt_compact_sections(Dashboard(), locale="en", supplemental_lines=supplements())
                self.assertIs(observed.exception, error)
                self.assertEqual(events, ["bool"] if stage == "bool" else ["bool", "str"])

    def test_locale_failure_precedes_supplement_consumption(self):
        for failure_call in (1, 2, 3):
            with self.subTest(failure_call=failure_call):
                error = RuntimeError("synthetic locale failure")
                events = []

                class Locale:
                    def __str__(self):
                        events.append("locale")
                        if len(events) == failure_call:
                            raise error
                        return "en"

                def supplements():
                    events.append("supplements")
                    yield "note"

                with self.assertRaises(RuntimeError) as observed:
                    adapt_compact_sections("💼 Holdings\nA: 1股\nB: 2股", locale=Locale(), supplemental_lines=supplements())
                self.assertIs(observed.exception, error)
                self.assertEqual(events, ["locale"] * failure_call)

    def test_supplement_iteration_failure_is_propagated(self):
        error = RuntimeError("synthetic iteration failure")

        def supplements():
            yield "note"
            raise error

        with self.assertRaises(RuntimeError) as observed:
            adapt_compact_sections("💼 Holdings\nDEMO: 2 shares", locale="en", supplemental_lines=supplements())
        self.assertIs(observed.exception, error)
        with self.assertRaises(TypeError):
            adapt_compact_sections("", locale="en", supplemental_lines=None)

    def test_supplement_conversion_failure_is_propagated(self):
        for stage in ("bool", "str"):
            with self.subTest(stage=stage):
                error = ValueError("synthetic supplement failure")

                class Supplement:
                    def __bool__(self):
                        if stage == "bool":
                            raise error
                        return True

                    def __str__(self):
                        raise error

                with self.assertRaises(ValueError) as observed:
                    adapt_compact_sections("", locale="en", supplemental_lines=(Supplement(),))
                self.assertIs(observed.exception, error)


if __name__ == "__main__":
    unittest.main()
