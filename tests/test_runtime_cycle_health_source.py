from copy import deepcopy

import pytest

from scripts.runtime_cycle_health_source import (
    SourceContractError,
    validate_source_ack,
    validate_source_checkpoint,
)


SOURCE_ID = "longbridge.paper"
TARGET_ID = "longbridge.paper"
BINDING = "a" * 64
CONFIG = "b" * 64
OBSERVATION = "c" * 64


def checkpoint_payload():
    return {
        "ok": True,
        "schema_version": "qsl_runtime_cycle_health_checkpoint.v2",
        "status": "initialized",
        "source_id": SOURCE_ID,
        "target_id": TARGET_ID,
        "configuration_sha256": CONFIG,
        "source_binding_id": BINDING,
        "adopted": False,
        "no_order": True,
        "execution_authority_granted": False,
        "authority_revision": 3,
        "checkpoint_revision": 7,
        "observed_at": "2026-10-08T15:30:00Z",
        "observation_sha256": OBSERVATION,
        "received_at": "2026-10-08T15:30:01Z",
        "required_from": "2026-10-01T00:00:00Z",
        "covered_through": "2026-10-08T15:00:00Z",
        "coverage_complete": True,
        "coverage_incomplete_reason": None,
        "checkpoint": {
            "schema_version": "qsl_runtime_cycle_health_checkpoint_state.v2",
            "configuration_sha256": CONFIG,
            "required_from": "2026-10-01T00:00:00Z",
            "baseline_established": True,
            "covered_through": "2026-10-08T15:00:00Z",
            "state": {
                "schema_version": "qsl_runtime_cycle_health_state_summary.v1",
                "target_id": TARGET_ID,
                "source_binding_id": BINDING,
                "incident_count": 2,
                "fault_attempt_count": 3,
                "unresolved_count": 1,
                "highest_severity": "critical",
                "active_category": "execution_uncertainty",
                "latest_fault_at": "2026-10-07T15:00:00Z",
                "resolution_history_count": 0,
                "history_complete": False,
                "execution_authority_granted": False,
            },
        },
        "history_page": {"items": [], "next_cursor": None, "complete": True},
    }


def test_get_v2_returns_only_cursor_and_admission_facts_not_summary_state():
    facts = validate_source_checkpoint(
        checkpoint_payload(),
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        expected_configuration_sha256=CONFIG,
    )
    assert facts == {
        "source_id": SOURCE_ID,
        "target_id": TARGET_ID,
        "source_binding_id": BINDING,
        "configuration_sha256": CONFIG,
        "required_from": "2026-10-01T00:00:00Z",
        "covered_through": "2026-10-08T15:00:00Z",
        "observed_at": "2026-10-08T15:30:00Z",
        "authority_revision": 3,
        "checkpoint_revision": 7,
        "coverage_complete": True,
        "coverage_incomplete_reason": None,
        "baseline_established": True,
        "admission_status": "initialized",
    }


def test_get_and_ack_preserve_canonical_microsecond_required_from():
    required_from = "2026-10-01T00:00:00.123456Z"
    get_payload = checkpoint_payload()
    get_payload["required_from"] = required_from
    get_payload["checkpoint"]["required_from"] = required_from
    facts = validate_source_checkpoint(
        get_payload,
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        expected_configuration_sha256=CONFIG,
    )
    assert facts["required_from"] == required_from

    payload = ack_payload()
    payload["required_from"] = required_from
    payload["checkpoint"]["required_from"] = required_from
    payload["observed_at"] = "2026-10-08T15:00:00.654321Z"
    result = validate_source_ack(
        payload,
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        configuration_sha256=CONFIG,
        required_from=facts["required_from"],
        expected_observation_sha256=OBSERVATION,
        expected_observed_at="2026-10-08T15:00:00.654321Z",
        expected_coverage_through="2026-10-08T15:00:00Z",
        expected_previous_covered_through="2026-10-08T15:00:00Z",
    )
    assert result["result"] == "stored"


@pytest.mark.parametrize("stamp", [
    "2026-10-08T15:00:00.123Z",
    "2026-10-08T15:00:00.000000Z",
    "2026-10-08T15:00:00+00:00",
    "2026-02-30T15:00:00Z",
])
def test_ack_rejects_noncanonical_or_impossible_observation_timestamp(stamp):
    payload = ack_payload()
    payload["observed_at"] = stamp
    with pytest.raises(SourceContractError):
        validate_source_ack(
            payload,
            source_id=SOURCE_ID,
            target_id=TARGET_ID,
            source_binding_id=BINDING,
            configuration_sha256=CONFIG,
            required_from="2026-10-01T00:00:00Z",
            expected_observation_sha256=OBSERVATION,
            expected_observed_at=stamp,
            expected_coverage_through="2026-10-08T15:00:00Z",
            expected_previous_covered_through="2026-10-08T15:00:00Z",
        )
    get_payload = checkpoint_payload()
    get_payload["required_from"] = stamp
    get_payload["checkpoint"]["required_from"] = stamp
    with pytest.raises(SourceContractError):
        validate_source_checkpoint(
            get_payload,
            source_id=SOURCE_ID,
            target_id=TARGET_ID,
            source_binding_id=BINDING,
            expected_configuration_sha256=CONFIG,
        )


def test_uninitialized_get_accepts_only_admitted_fixed_baseline_without_claiming_coverage():
    payload = {
        "ok": True,
        "schema_version": "qsl_runtime_cycle_health_checkpoint.v2",
        "status": "uninitialized",
        "source_id": SOURCE_ID,
        "target_id": TARGET_ID,
        "configuration_sha256": CONFIG,
        "source_binding_id": BINDING,
        "authority_revision": 1,
        "checkpoint_revision": 0,
        "observed_at": None,
        "observation_sha256": None,
        "received_at": None,
        "required_from": "2026-10-01T00:00:00Z",
        "checkpoint": None,
        "covered_through": None,
        "coverage_complete": False,
        "coverage_incomplete_reason": None,
        "history_page": {"items": [], "snapshot_through_id": 0, "complete": True, "next_cursor": None},
        "prior_partitions": [],
        "continuity": "same_binding",
        "adopted": False,
        "no_order": True,
        "execution_authority_granted": False,
    }
    result = validate_source_checkpoint(
        payload,
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        expected_configuration_sha256=CONFIG,
    )
    assert result["required_from"] == "2026-10-01T00:00:00Z"
    assert result["covered_through"] is None
    assert result["baseline_established"] is False
    assert result["coverage_complete"] is False


def test_blocked_new_binding_preserves_prior_history_without_calling_it_admitted_health():
    payload = {
        "ok": True,
        "schema_version": "qsl_runtime_cycle_health_checkpoint.v2",
        "status": "blocked",
        "source_id": SOURCE_ID,
        "target_id": TARGET_ID,
        "configuration_sha256": CONFIG,
        "source_binding_id": BINDING,
        "authority_revision": 2,
        "checkpoint_revision": 0,
        "observed_at": None,
        "observation_sha256": None,
        "received_at": None,
        "required_from": "2026-10-01T00:00:00Z",
        "checkpoint": None,
        "covered_through": None,
        "coverage_complete": False,
        "coverage_incomplete_reason": None,
        "history_page": {"items": [{"attempt_id": "old-binding-attempt"}], "snapshot_through_id": 5, "complete": True, "next_cursor": None},
        "prior_partitions": [{"source_binding_id": "old-binding"}],
        "continuity": "unconfirmed",
        "adopted": False,
        "no_order": True,
        "execution_authority_granted": False,
    }
    result = validate_source_checkpoint(
        payload,
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        expected_configuration_sha256=CONFIG,
    )
    assert result["admission_status"] == "blocked"
    assert result["continuity"] == "unconfirmed"
    assert result["covered_through"] is None
    assert result["coverage_complete"] is False


def test_partial_checkpoint_without_established_baseline_is_admissible_but_has_no_cursor():
    payload = checkpoint_payload()
    payload["checkpoint"]["baseline_established"] = False
    payload["checkpoint"]["covered_through"] = None
    payload["covered_through"] = None
    payload["coverage_complete"] = False
    payload["coverage_incomplete_reason"] = "scan_incomplete"
    result = validate_source_checkpoint(
        payload,
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        expected_configuration_sha256=CONFIG,
    )
    assert result["baseline_established"] is False
    assert result["covered_through"] is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source_id",), "longbridge.other"),
        (("checkpoint", "configuration_sha256"), "d" * 64),
        (("checkpoint", "covered_through"), "not-a-timestamp"),
        (("checkpoint", "state", "history_complete"), True),
    ],
)
def test_get_v2_rejects_wrong_identity_or_untrusted_summary(path, value):
    payload = deepcopy(checkpoint_payload())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(SourceContractError):
        validate_source_checkpoint(
            payload,
            source_id=SOURCE_ID,
            target_id=TARGET_ID,
            source_binding_id=BINDING,
            expected_configuration_sha256=CONFIG,
        )


def ack_payload():
    payload = checkpoint_payload()
    return {
        "ok": True,
        "schema_version": "qsl_runtime_cycle_health_ack.v1",
        "result": "stored",
        "source_id": SOURCE_ID,
        "target_id": TARGET_ID,
        "configuration_sha256": CONFIG,
        "source_binding_id": BINDING,
        "authority_revision": 4,
        "checkpoint_revision": 8,
        "observed_at": "2026-10-08T15:00:00Z",
        "observation_sha256": OBSERVATION,
        "received_at": "2026-10-08T15:40:01Z",
        "required_from": payload["required_from"],
        "covered_through": payload["checkpoint"]["covered_through"],
        "coverage_complete": True,
        "coverage_incomplete_reason": None,
        "adopted": False,
        "no_order": True,
        "execution_authority_granted": False,
        "checkpoint": payload["checkpoint"],
    }


def test_post_ack_must_match_exact_published_digest_and_identity():
    result = validate_source_ack(
        ack_payload(),
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        configuration_sha256=CONFIG,
        required_from="2026-10-01T00:00:00Z",
        expected_observation_sha256=OBSERVATION,
        expected_observed_at="2026-10-08T15:00:00Z",
        expected_coverage_through="2026-10-08T15:00:00Z",
        expected_previous_covered_through="2026-10-08T15:00:00Z",
    )
    assert result["result"] == "stored"
    assert result["covered_through"] == "2026-10-08T15:00:00Z"
    assert result["coverage_complete"] is True


def test_incomplete_coverage_ack_is_stored_but_does_not_advance_cursor():
    payload = ack_payload()
    old_cursor = "2026-10-07T15:00:00Z"
    payload["covered_through"] = old_cursor
    payload["checkpoint"]["covered_through"] = old_cursor
    payload["coverage_complete"] = False
    payload["coverage_incomplete_reason"] = "coverage_gap"
    payload["observed_at"] = "2026-10-08T15:40:00Z"
    result = validate_source_ack(
        payload,
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        configuration_sha256=CONFIG,
        required_from="2026-10-01T00:00:00Z",
        expected_observation_sha256=OBSERVATION,
        expected_observed_at="2026-10-08T15:40:00Z",
        expected_coverage_through="2026-10-08T15:40:00Z",
        expected_previous_covered_through=old_cursor,
    )
    assert result["result"] == "stored"
    assert result["coverage_complete"] is False
    assert result["covered_through"] == old_cursor


def test_first_incomplete_ack_is_confirmed_without_creating_baseline_or_cursor():
    payload = ack_payload()
    payload["covered_through"] = None
    payload["checkpoint"]["covered_through"] = None
    payload["checkpoint"]["baseline_established"] = False
    payload["coverage_complete"] = False
    payload["coverage_incomplete_reason"] = "scan_incomplete"
    result = validate_source_ack(
        payload,
        source_id=SOURCE_ID,
        target_id=TARGET_ID,
        source_binding_id=BINDING,
        configuration_sha256=CONFIG,
        required_from="2026-10-01T00:00:00Z",
        expected_observation_sha256=OBSERVATION,
        expected_observed_at="2026-10-08T15:00:00Z",
        expected_coverage_through="2026-10-08T15:00:00Z",
        expected_previous_covered_through=None,
    )
    assert result["result"] == "stored"
    assert result["coverage_complete"] is False
    assert result["covered_through"] is None


def test_incomplete_ack_must_still_match_submitted_observation_time():
    payload = ack_payload()
    payload["covered_through"] = "2026-10-07T15:00:00Z"
    payload["checkpoint"]["covered_through"] = "2026-10-07T15:00:00Z"
    payload["coverage_complete"] = False
    payload["coverage_incomplete_reason"] = "coverage_gap"
    payload["observed_at"] = "2026-10-08T15:01:00Z"
    with pytest.raises(SourceContractError, match="source_ack_unconfirmed"):
        validate_source_ack(
            payload,
            source_id=SOURCE_ID,
            target_id=TARGET_ID,
            source_binding_id=BINDING,
            configuration_sha256=CONFIG,
            required_from="2026-10-01T00:00:00Z",
            expected_observation_sha256=OBSERVATION,
            expected_observed_at="2026-10-08T15:00:00Z",
            expected_coverage_through="2026-10-08T15:00:00Z",
            expected_previous_covered_through="2026-10-07T15:00:00Z",
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("result", "unknown"),
        ("observation_sha256", "d" * 64),
        ("configuration_sha256", "d" * 64),
        ("source_binding_id", "other-binding"),
        ("adopted", True),
        ("no_order", False),
        ("authority_revision", 0),
        ("checkpoint_revision", 0),
        ("observed_at", "2026-10-08T15:01:00Z"),
    ],
)
def test_post_ack_unknown_or_mismatched_result_is_not_accepted(key, value):
    payload = ack_payload()
    payload[key] = value
    with pytest.raises(SourceContractError):
        validate_source_ack(
            payload,
            source_id=SOURCE_ID,
            target_id=TARGET_ID,
            source_binding_id=BINDING,
            configuration_sha256=CONFIG,
            required_from="2026-10-01T00:00:00Z",
            expected_observation_sha256=OBSERVATION,
            expected_observed_at="2026-10-08T15:00:00Z",
            expected_coverage_through="2026-10-08T15:00:00Z",
            expected_previous_covered_through="2026-10-08T15:00:00Z",
        )
