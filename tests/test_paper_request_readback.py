import json
import subprocess
import unittest

from scripts.paper_request_readback import inspect, validate_window


def entry(url="https://private.example/dry-run?account=secret", revision="longbridge-quant-paper-service-00001-abc", status=200):
    return {"timestamp": "2026-10-30T05:25:17Z", "resource": {"labels": {"revision_name": revision}}, "httpRequest": {"requestMethod": "POST", "requestUrl": url, "status": status}, "private": "secret"}


def runner(entries, *, label="a" * 40, fail=False):
    def run(args, **kwargs):
        if fail:
            return subprocess.CompletedProcess(args, 1, "", "secret error")
        payload = entries if args[1:3] == ["logging", "read"] else {"metadata": {"name": args[4], "labels": {"serving.knative.dev/service": "longbridge-quant-paper-service", "commit-sha": label}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
    return run


def test_matching_request_is_sanitized_and_source_is_labeled_not_proven_image():
    result = inspect("2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run=runner([entry()]))
    assert result["status"] == "observed"
    assert result["requests"][0]["source_commit_label"] == "a" * 40
    assert result["requests"][0]["http_status"] == 200
    assert result["business_cycle"] == "unknown" and result["submit_count"] is None
    assert result["scheduler_correlation"] == "unverified"
    assert "secret" not in json.dumps(result) and "private.example" not in json.dumps(result)


def test_filters_other_paths_and_rejects_untrusted_revision_and_label():
    result = inspect("2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run=runner([entry(url="https://x/run"), entry(revision="--bad")], label="leaked"))
    assert result["matching_count"] == 1
    assert result["requests"][0]["revision_fingerprint_sha256"] is None
    assert result["requests"][0]["source_commit_label"] is None
    assert "leaked" not in json.dumps(result)


def test_bounded_and_fail_closed_without_leaking_errors():
    result = inspect("2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run=runner([entry()] * 21))
    assert result["status"] == "truncated" and len(result["requests"]) == 20
    failed = inspect("2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run=runner([], fail=True))
    assert failed["status"] == "unavailable" and "secret" not in json.dumps(failed)


def test_no_match_and_unknown_commit_are_not_proof():
    empty = inspect("2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run=runner([]))
    assert empty["status"] == "no_match" and empty["matching_count"] == 0
    result = inspect("2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run=runner([entry()], label="not-a-sha"))
    assert result["requests"][0]["revision_fingerprint_sha256"] is not None
    assert result["requests"][0]["source_commit_label"] is None
    assert result["source_binding_verified"] is False


def test_query_is_bounded_to_path_and_uses_no_shell():
    calls = []
    delegate = runner([entry(status=500)])

    def capture(args, **kwargs):
        calls.append((args, kwargs))
        return delegate(args, **kwargs)

    result = inspect("2026-10-30T05:25:00Z", "2026-10-30T05:26:00Z", run=capture)
    query = calls[0][0][3]
    assert 'httpRequest.requestMethod="POST"' in query
    assert 'httpRequest.requestUrl =~ "^https://[^/?#]+/dry-run([?][^#]*)?$"' in query
    assert "--limit=21" in calls[0][0]
    assert all(isinstance(args, list) and not kwargs.get("shell", False) for args, kwargs in calls)
    assert result["status"] == "observed" and result["requests"][0]["http_status"] == 500
    assert result["scheduler_correlation"] == "unverified"


def test_invalid_window_rejected():
    for start, end in [("2026-10-30T05:25:00Z; touch /tmp/x", "2026-10-30T05:26:00Z"), ("2026-10-30T05:25:00Z", "2026-10-30T05:41:00Z"), ("2026-10-30T05:26:00Z", "2026-10-30T05:25:00Z")]:
        with unittest.TestCase().assertRaises(ValueError):
            validate_window(start, end)


if __name__ == "__main__":
    test_matching_request_is_sanitized_and_source_is_labeled_not_proven_image()
    test_filters_other_paths_and_rejects_untrusted_revision_and_label()
    test_bounded_and_fail_closed_without_leaking_errors()
    test_no_match_and_unknown_commit_are_not_proof()
    test_query_is_bounded_to_path_and_uses_no_shell()
    test_invalid_window_rejected()
