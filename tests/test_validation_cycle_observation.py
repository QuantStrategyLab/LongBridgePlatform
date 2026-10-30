import contextlib
import io
import json
import threading
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from application import validation_cycle_observation as observer


class ValidationCycleObservationTests(unittest.TestCase):
    def test_guard_counts_attempt_and_never_forwards_arguments(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), observer.observe("a" * 32 + "/1;o=1", "longbridgequant") as receipt:
            receipt.adapter_instrumented = True
            receipt.cycle_entered = True
            with self.assertRaises(observer.ValidationSubmitBlocked):
                receipt.block_submit("private account", symbol="private symbol")
            receipt.cycle_returned = True
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["adapter_submit_attempt_count"], 1)
        self.assertIsNone(payload["broker_submit_count"])
        self.assertEqual(payload["trace_correlation"], "unverified")
        self.assertEqual(payload["logging.googleapis.com/trace"], "projects/longbridgequant/traces/" + "a" * 32)
        self.assertNotIn("private", output.getvalue())
        self.assertIsNone(observer.current())

    def test_uninstrumented_scope_never_claims_zero_and_bad_trace_is_omitted(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), observer.observe("bad secret", "longbridgequant"):
            pass
        payload = json.loads(output.getvalue())
        self.assertIsNone(payload["adapter_submit_attempt_count"])
        self.assertNotIn("logging.googleapis.com/trace", payload)
        self.assertNotIn("secret", output.getvalue())

    def test_exception_emits_incomplete_state_without_exception_details(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(RuntimeError):
            with observer.observe(None, None) as receipt:
                receipt.adapter_instrumented = True
                receipt.cycle_entered = True
                raise RuntimeError("private diagnostic")
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["cycle_entered"])
        self.assertFalse(payload["cycle_returned"])
        self.assertEqual(payload["report_status"], "unknown")
        self.assertNotIn("private", output.getvalue())

    def test_sink_failure_never_changes_result_or_original_exception(self):
        with patch("builtins.print", side_effect=OSError("sink unavailable")):
            with observer.observe(None, None):
                result = "original"
            self.assertEqual(result, "original")
            with self.assertRaisesRegex(RuntimeError, "original error"):
                with observer.observe(None, None):
                    raise RuntimeError("original error")
        self.assertIsNone(observer.current())

    def test_scopes_do_not_share_state_even_when_nested(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), observer.observe(None, None) as outer:
            outer.adapter_instrumented = True
            with observer.observe(None, None) as inner:
                inner.adapter_instrumented = True
                with self.assertRaises(observer.ValidationSubmitBlocked):
                    inner.block_submit()
            self.assertIs(observer.current(), outer)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([row["adapter_submit_attempt_count"] for row in rows], [1, 0])

    def test_threaded_calls_count_on_bound_guard(self):
        receipt = observer.ValidationCycleObservation(adapter_instrumented=True)

        def call():
            try:
                receipt.block_submit()
            except observer.ValidationSubmitBlocked:
                pass

        threads = [threading.Thread(target=call) for _ in range(30)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(receipt.payload(None, None)["adapter_submit_attempt_count"], 30)

    def test_instrumentation_replaces_only_cycle_adapter_without_touching_original(self):
        @dataclass(frozen=True)
        class Adapter:
            submit_order_fn: object

        @dataclass(frozen=True)
        class Composer:
            broker_adapters: Adapter

        forwarded = []
        original = Composer(Adapter(lambda *args: forwarded.append(args)))
        observation = observer.ValidationCycleObservation()
        protected = observer.instrument_composer(original, observation)
        with self.assertRaises(observer.ValidationSubmitBlocked):
            protected.broker_adapters.submit_order_fn("sensitive")
        self.assertEqual(forwarded, [])
        self.assertEqual(observation.payload(None, None)["adapter_submit_attempt_count"], 1)
        original.broker_adapters.submit_order_fn("unchanged")
        self.assertEqual(forwarded, [("unchanged",)])


if __name__ == "__main__":
    unittest.main()
