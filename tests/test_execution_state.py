import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.execution_state import (  # noqa: E402
    ExecutionMarkerStore,
    resolve_execution_dedup_enabled,
)
from quant_platform_kit.common.execution_state import (  # noqa: E402
    _report_matches_execution,
)


def _env_with_dedup(raw_value):
    def reader(name, default=None):
        if name == "LONGBRIDGE_EXECUTION_DEDUP_ENABLED":
            return raw_value
        return default

    return reader


def test_execution_dedup_defaults_to_enabled_for_paper_account_scope():
    assert (
        resolve_execution_dedup_enabled(
            env_reader=_env_with_dedup(None),
            dry_run_only=False,
            account_scope="PAPER",
        )
        is True
    )


def test_execution_dedup_defaults_to_disabled_for_live_non_paper_scope():
    assert (
        resolve_execution_dedup_enabled(
            env_reader=_env_with_dedup(None),
            dry_run_only=False,
            account_scope="HK",
        )
        is False
    )


def test_execution_dedup_env_override_wins_for_paper_scope():
    assert (
        resolve_execution_dedup_enabled(
            env_reader=_env_with_dedup("false"),
            dry_run_only=False,
            account_scope="PAPER",
        )
        is False
    )


def test_prior_report_match_treats_successful_no_action_as_completed():
    payload = {
        "platform": "longbridge",
        "strategy_profile": "russell_top50_leader_rotation",
        "account_scope": "PAPER",
        "dry_run": False,
        "status": "ok",
        "summary": {
            "signal_date": "2026-06-04",
            "action_done": False,
            "orders_previewed_count": 0,
            "order_events_count": 0,
            "orders_skipped_count": 0,
        },
        "diagnostics": {
            "signal_snapshot": {
                "signal_as_of": "2026-06-01",
                "market_date": "2026-06-01",
            },
        },
    }

    assert (
        _report_matches_execution(
            payload,
            platform="longbridge",
            strategy_profile="russell_top50_leader_rotation",
            account_scope="PAPER",
            signal_date="2026-06-01",
            effective_date="",
            dry_run_only=False,
        )
        is True
    )


def test_prior_report_match_does_not_treat_blocked_no_action_as_completed():
    payload = {
        "platform": "longbridge",
        "strategy_profile": "russell_top50_leader_rotation",
        "account_scope": "PAPER",
        "dry_run": False,
        "status": "ok",
        "summary": {
            "signal_date": "2026-06-04",
            "action_done": False,
            "orders_skipped_count": 1,
        },
    }

    assert (
        _report_matches_execution(
            payload,
            platform="longbridge",
            strategy_profile="russell_top50_leader_rotation",
            account_scope="PAPER",
            signal_date="2026-06-04",
            effective_date="",
            dry_run_only=False,
        )
        is False
    )


def test_prior_report_scan_is_scoped_to_signal_month():
    observed = {}

    class FakeClient:
        def list_blobs(self, bucket_name, *, prefix):
            observed["bucket_name"] = bucket_name
            observed["prefix"] = prefix
            return ()

    store = ExecutionMarkerStore(
        local_dir=None,
        gcs_prefix_uri="gs://bucket/execution-reports",
        client_factory=lambda **_kwargs: FakeClient(),
    )

    assert (
        store.has_prior_execution_report(
            platform="longbridge",
            strategy_profile="russell_top50_leader_rotation",
            account_scope="PAPER",
            signal_date="2026-06-04",
            effective_date="",
            dry_run_only=False,
        )
        is False
    )
    assert observed == {
        "bucket_name": "bucket",
        "prefix": (
            "execution-reports/longbridge/"
            "russell_top50_leader_rotation/PAPER/2026-06"
        ),
    }
