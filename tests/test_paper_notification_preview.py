from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import send_paper_notification_preview as preview

WORKFLOW = (ROOT / ".github/workflows/paper-notification-preview.yml").read_text(
    encoding="utf-8"
)
SCRIPT_PATH = ROOT / "scripts" / "send_paper_notification_preview.py"

_FORBIDDEN_IMPORT_ROOTS = (
    "longport",
    "application.longbridge_execution",
    "application.longbridge_portfolio",
    "application.execution_service",
    "application.broker_reconciliation",
)


class FakeRequests:
    def __init__(self, *, status_code=200, payload=None, raise_exc: Exception | None = None):
        self.calls = []
        self.status_code = status_code
        self.payload = {"ok": True} if payload is None else payload
        self.raise_exc = raise_exc

    def post(self, url, json, timeout):
        if self.raise_exc is not None:
            raise self.raise_exc
        self.calls.append((url, json, timeout))
        return FakeResponse(self.status_code, self.payload)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _classify_preview_text(text: str) -> str | None:
    if "订单状态未知" in text or "Unknown Order Status" in text:
        return "order_status_unknown"
    if "订单待确认" in text or "Order Status Unconfirmed" in text or "pending confirmation" in text.lower():
        return "order_unconfirmed"
    if "订单成交" in text or "Order Filled" in text:
        return "order_filled"
    if "订单异常" in text or "Order Error" in text or "Rejected" in text:
        return "order_rejected"
    if "调仓指令" in text or "Rebalance" in text:
        return "executable_rebalance"
    if "心跳" in text or "Heartbeat" in text:
        return "heartbeat_no_rebalance"
    return None


def test_build_preview_messages_covers_required_categories_with_safe_markers():
    messages = preview.build_preview_messages(locale="zh")
    assert 1 <= len(messages) <= 6
    assert len(messages) == 6

    categories = {_classify_preview_text(message) for message in messages}
    assert categories == {
        "heartbeat_no_rebalance",
        "executable_rebalance",
        "order_unconfirmed",
        "order_filled",
        "order_rejected",
        "order_status_unknown",
    }

    for message in messages:
        assert "PREVIEW" in message
        assert "synthetic" in message.lower() or "合成" in message
        assert "不会下单" in message or "No order will be" in message or "will not place" in message.lower()
        for forbidden in (
            "api.telegram.org",
            "https://",
            "Traceback",
            "LONGPORT_APP_KEY",
            "secret-token",
        ):
            assert forbidden not in message


def test_preview_copy_follows_selected_locale_without_mixed_safety_text():
    zh_messages = preview.build_preview_messages(locale="zh")
    en_messages = preview.build_preview_messages(locale="en")

    assert all("No order will be placed" not in message for message in zh_messages)
    assert all("不会下单" not in message for message in en_messages)
    assert all("PAPER 通知预览" in message for message in zh_messages[:2])
    assert all("PAPER Notification Preview" in message for message in en_messages[:2])


def test_send_preview_calls_sender_once_per_message_without_order_apis(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-preview")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "chat-preview")
    monkeypatch.setenv("NOTIFY_LANG", "zh")

    before_modules = {
        name
        for name in sys.modules
        if any(name == root or name.startswith(f"{root}.") for root in _FORBIDDEN_IMPORT_ROOTS)
    }

    fake_requests = FakeRequests()
    delivered = preview.send_preview(requests_module=fake_requests)

    assert delivered is True
    assert len(fake_requests.calls) == 6
    assert len(fake_requests.calls) <= 6

    texts = [payload["text"] for _url, payload, _timeout in fake_requests.calls]
    for text in texts:
        assert text.startswith("[PAPER] ")
        assert "PREVIEW" in text
        assert "token-preview" not in text
        assert "chat-preview" not in text

    categories = {_classify_preview_text(text) for text in texts}
    assert "heartbeat_no_rebalance" in categories
    assert "executable_rebalance" in categories
    assert "order_unconfirmed" in categories
    assert "order_filled" in categories
    assert "order_rejected" in categories
    assert "order_status_unknown" in categories

    after_modules = {
        name
        for name in sys.modules
        if any(name == root or name.startswith(f"{root}.") for root in _FORBIDDEN_IMPORT_ROOTS)
    }
    assert after_modules == before_modules


def test_preview_script_has_no_broker_or_execution_imports():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported.add(node.module)

    for forbidden in _FORBIDDEN_IMPORT_ROOTS:
        assert forbidden not in imported
        assert not any(
            name == forbidden or name.startswith(f"{forbidden}.") for name in imported
        )
    assert "longport" not in imported


def test_send_preview_fails_closed_without_telegram_target(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TG_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN_SECRET_NAME", raising=False)
    monkeypatch.delenv("GLOBAL_TELEGRAM_CHAT_ID", raising=False)
    fake_requests = FakeRequests()
    assert preview.send_preview(requests_module=fake_requests) is False
    assert fake_requests.calls == []


def test_main_returns_nonzero_when_delivery_fails(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-preview")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "chat-preview")
    monkeypatch.setattr(
        preview,
        "send_preview",
        lambda **_kwargs: False,
    )
    assert preview.main([]) == 1


def test_workflow_static_safety_constraints():
    assert "name: PAPER Notification Preview" in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW
    assert "environment: longbridge-paper" in WORKFLOW
    assert "schedule:" not in WORKFLOW
    assert "workflow_run:" not in WORKFLOW
    assert "scripts/send_paper_notification_preview.py" in WORKFLOW
    assert "secrets.GLOBAL_TELEGRAM_CHAT_ID" in WORKFLOW
    assert "secrets.TELEGRAM_TOKEN" in WORKFLOW
    assert "vars.TELEGRAM_TOKEN_SECRET_NAME" in WORKFLOW
    assert "uv sync --frozen --no-dev" in WORKFLOW
    assert "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9" in WORKFLOW

    for forbidden in (
        "gcloud run deploy",
        "gcloud run services",
        "gcloud run jobs",
        "gcloud scheduler",
        "invoke-cloud-run",
        "Cloud Run",
        "retry",
        "max-attempts",
        "continue-on-error: true",
        "secrets.LONGPORT",
        "LONGBRIDGE_",
        "strategy_profile",
        "send_paper_notification_canary.py",
    ):
        assert forbidden not in WORKFLOW

    assert WORKFLOW.count("google-github-actions/auth@v3") <= 1


def test_existing_canary_workflow_unchanged_reference():
    canary_workflow = (
        ROOT / ".github/workflows/paper-notification-canary.yml"
    ).read_text(encoding="utf-8")
    assert "scripts/send_paper_notification_canary.py" in canary_workflow
    assert "PAPER Notification Canary" in canary_workflow
