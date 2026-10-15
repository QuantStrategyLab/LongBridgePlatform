"""Publisher regressions use a fake transport. No GitHub, QRS, or cloud calls."""

import io
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile
import unittest

from scripts.publish_hk_stop_result import PublishError, publish_hk_stop_result


ROOT = Path(__file__).resolve().parents[1]
HEAD = "c" * 40
NOW = datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)
SYNC = "https://control.example"
TOKEN = "github-token"
SYNC_TOKEN = "sync-token"


def _document():
    return {
        "schema_version": "qsl_hk_stop_result.v1",
        "request_id": "00112233-4455-6677-8899-aabbccddeeff",
        "source_revision": 7,
        "source_identity_sha256": "ab" * 32,
        "target_id": "longbridge/hk",
        "runtime_identity_sha256": "cd" * 32,
        "producer": {
            "head_sha": HEAD,
            "repository": "QuantStrategyLab/LongBridgePlatform",
            "run_attempt": 1,
            "run_id": "4821",
            "workflow_path": ".github/workflows/stop-hk-runtime.yml",
        },
        "observed_at": "2026-09-28T06:00:00Z",
        "readback": {
            "complete": True,
            "project": "longbridgequant",
            "region": "asia-east2",
            "revision_name": "synthetic-old",
            "runtime_enabled": False,
            "scheduler_count": 2,
            "scheduler_set_sha256": "ef" * 32,
            "scheduler_state": "paused",
            "service": "longbridge-quant-hk-service",
        },
        "no_order": True,
        "in_flight_state": "unknown",
        "retirement_complete": False,
    }


def _zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    return buffer.getvalue()


def _event(repository="QuantStrategyLab/LongBridgePlatform"):
    return {"repository": {"full_name": repository}, "workflow_run": {"id": 4821}}


def _environment():
    return {
        "GITHUB_TOKEN": TOKEN,
        "EXECUTION_EVIDENCE_SYNC_URL": SYNC,
        "EXECUTION_EVIDENCE_SYNC_TOKEN": SYNC_TOKEN,
    }


class Transport:
    def __init__(self, *, conclusion="success", head_branch="main", event="workflow_dispatch", attempt=1,
                 head_sha=HEAD, workflow_path=".github/workflows/stop-hk-runtime.yml", artifacts=None,
                 blob=None, post_status=204, timeout_post=False, redirect_again=False):
        self.calls = []
        self.conclusion = conclusion
        self.head_branch = head_branch
        self.event_name = event
        self.attempt = attempt
        self.head_sha = head_sha
        self.workflow_path = workflow_path
        self.artifacts = artifacts
        self.blob = blob if blob is not None else _zip([("result.json", json.dumps(_document()).encode())])
        self.post_status = post_status
        self.timeout_post = timeout_post
        self.redirect_again = redirect_again

    def exchange(self, url, *, method, headers, body, timeout):
        self.calls.append({"url": url, "method": method, "headers": dict(headers), "body": body, "timeout": timeout})
        if url.endswith("/api/internal/runtime-stop-result"):
            if self.timeout_post:
                raise TimeoutError()
            return self.post_status, {}, b""
        if url.endswith("/zip"):
            if TOKEN in url or headers.get("Authorization") != f"Bearer {TOKEN}":
                raise AssertionError("artifact api request lost its github token")
            return 302, {"Location": "https://objects.githubusercontent.com/artifact.zip"}, b""
        if url == "https://objects.githubusercontent.com/artifact.zip":
            if "Authorization" in headers or TOKEN in json.dumps(headers):
                raise AssertionError("redirect carried the github token")
            if self.redirect_again:
                return 302, {"Location": "https://evil.example/more.zip"}, self.blob
            return 200, {}, self.blob
        if url.endswith("/artifacts?per_page=100"):
            artifacts = self.artifacts
            if artifacts is None:
                artifacts = [{
                    "id": 9, "name": "hk-stop-result", "expired": False, "size_in_bytes": len(self.blob),
                    "expires_at": "2026-10-05T06:00:00Z",
                }]
            return 200, {}, json.dumps({"total_count": len(artifacts), "artifacts": artifacts}).encode()
        if "/actions/workflows/" in url:
            return 200, {}, json.dumps({"id": 77, "path": self.workflow_path}).encode()
        if url.endswith("/actions/runs/4821"):
            return 200, {}, json.dumps({
                "id": 4821, "event": self.event_name, "head_branch": self.head_branch,
                "head_sha": self.head_sha, "run_attempt": self.attempt, "status": "completed",
                "conclusion": self.conclusion, "workflow_id": 77,
            }).encode()
        raise AssertionError(url)

    def posts(self):
        return [call for call in self.calls if call["url"].endswith("/api/internal/runtime-stop-result")]


class PublishHkStopResultTests(unittest.TestCase):
    def test_success_posts_the_artifact_once_without_leaking_the_github_token(self):
        transport = Transport()
        published = publish_hk_stop_result(
            _event(), _environment(), exchange=transport.exchange, now=NOW,
        )
        self.assertTrue(published)
        self.assertEqual(len(transport.posts()), 1)
        post = transport.posts()[0]
        self.assertEqual(post["url"], "https://control.example/api/internal/runtime-stop-result")
        self.assertEqual(post["headers"]["Authorization"], f"Bearer {SYNC_TOKEN}")
        self.assertNotIn(TOKEN, json.dumps(post["headers"]))
        self.assertEqual(post["timeout"], 20)
        self.assertEqual(json.loads(post["body"]), _document())

    def test_rejected_runs_do_not_post(self):
        cases = [
            {"repository": "QuantStrategyLab/Other"},
            {"head_branch": "feature"},
            {"event": "schedule"},
            {"attempt": 2},
            {"conclusion": "failure"},
            {"workflow_path": ".github/workflows/other.yml"},
            {"artifacts": [{
                "id": 9, "name": "hk-stop-result", "expired": False, "size_in_bytes": 10,
            }, {
                "id": 10, "name": "other", "expired": False, "size_in_bytes": 10,
            }]},
            {"blob": _zip([("../result.json", b"{}"), ("result.json", b"{}")])},
            {"blob": _zip([("result.json", b"{}"), ("extra.txt", b"x")])},
        ]
        for case in cases:
            with self.subTest(case=case):
                repository = case.pop("repository", "QuantStrategyLab/LongBridgePlatform")
                transport = Transport(**case)
                outcome = "published"
                try:
                    outcome = publish_hk_stop_result(
                        _event(repository), _environment(), exchange=transport.exchange, now=NOW,
                    )
                except PublishError:
                    outcome = "rejected"
                self.assertNotEqual(outcome, True)
                self.assertEqual(transport.posts(), [])

    def test_timeout_does_not_retry_the_post(self):
        transport = Transport(timeout_post=True)
        with self.assertRaisesRegex(PublishError, "^stop_result_unpublished$"):
            publish_hk_stop_result(_event(), _environment(), exchange=transport.exchange, now=NOW)
        self.assertEqual(len(transport.posts()), 1)

    def test_second_redirect_is_not_followed(self):
        transport = Transport(redirect_again=True)
        with self.assertRaises(PublishError):
            publish_hk_stop_result(_event(), _environment(), exchange=transport.exchange, now=NOW)
        self.assertEqual(transport.posts(), [])
        self.assertFalse(any(call["url"].startswith("https://evil.example/") for call in transport.calls))

    def test_workflows_keep_the_result_independent_of_lifecycle_success(self):
        stop = (ROOT / ".github/workflows/stop-hk-runtime.yml").read_text()
        lifecycle = (ROOT / ".github/workflows/runtime-target-lifecycle.yml").read_text()
        self.assertIn("name: hk-stop-result", stop)
        self.assertIn("retention-days: 7", stop)
        self.assertIn("if-no-files-found: ignore", stop)
        job = lifecycle.split("  publish-hk-stop-result:\n", 1)[1]
        header = job.split("    steps:", 1)[0]
        self.assertNotIn("needs:", header)
        self.assertIn("environment: longbridge-hk", header)
        self.assertIn("python3 scripts/publish_hk_stop_result.py", job)
        self.assertIn("EXECUTION_EVIDENCE_SYNC_TOKEN", job)
        self.assertIn("EXECUTION_EVIDENCE_SYNC_URL", job)
        self.assertNotIn("github.event.workflow_run.conclusion", lifecycle)
        self.assertEqual(lifecycle.count("uses: astral-sh/setup-uv@"), 1)


if __name__ == "__main__":
    unittest.main()
