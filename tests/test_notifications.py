import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notifications.telegram import build_issue_notifier, build_prefixer, build_sender, build_translator


class FakeRequests:
    def __init__(self):
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return object()


class NotificationTests(unittest.TestCase):
    def test_build_translator_supports_chinese(self):
        translate = build_translator("zh")
        self.assertEqual(translate("equity", value="123.45"), "💰 净值: $123.45")

    def test_build_prefixer_formats_account_and_service(self):
        with_prefix = build_prefixer("HK", "longbridge-quant-hk")
        self.assertEqual(with_prefix("hello"), "[HK/longbridge-quant-hk] hello")

    def test_build_sender_posts_prefixed_message(self):
        fake_requests = FakeRequests()
        sender = build_sender(
            "token-1",
            "chat-1",
            with_prefix_fn=build_prefixer("HK", "longbridge-quant-hk"),
            requests_module=fake_requests,
        )
        sender("hello")
        self.assertEqual(len(fake_requests.calls), 1)
        url, payload, timeout = fake_requests.calls[0]
        self.assertIn("token-1", url)
        self.assertEqual(payload["chat_id"], "chat-1")
        self.assertEqual(payload["text"], "[HK/longbridge-quant-hk] hello")
        self.assertEqual(timeout, 10)

    def test_build_issue_notifier_logs_and_sends(self):
        sent = []
        notifier = build_issue_notifier(
            with_prefix_fn=build_prefixer("SG", "longbridge-quant-sg"),
            send_tg_message_fn=sent.append,
        )
        notifier("Problem", "details")
        self.assertEqual(sent, ["Problem\ndetails"])


if __name__ == "__main__":
    unittest.main()
