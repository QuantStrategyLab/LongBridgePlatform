"""Strict contracts for the existing QRS LongBridge cycle-health source API."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


class SourceContractError(ValueError):
    """The source admission or durable ACK could not be verified."""


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GET_SCHEMA = "qsl_runtime_cycle_health_checkpoint.v2"
_CHECKPOINT_SCHEMA = "qsl_runtime_cycle_health_checkpoint_state.v2"
_SUMMARY_SCHEMA = "qsl_runtime_cycle_health_state_summary.v1"
_ACK_SCHEMA = "qsl_runtime_cycle_health_ack.v1"


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceContractError("source_contract_invalid")
    return value


def _revision(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise SourceContractError("source_contract_invalid")
    return value


def _timestamp(value: Any, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > 64:
        raise SourceContractError("source_contract_invalid")
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SourceContractError("source_contract_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceContractError("source_contract_invalid")
    return value


def _canonical_utc(value: Any) -> str:
    stamp = _timestamp(value)
    if stamp is None or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(?!000000)\d{6})?Z",
        stamp,
    ):
        raise SourceContractError("source_contract_invalid")
    return stamp


def validate_source_checkpoint(
    payload: Any,
    *,
    source_id: str,
    target_id: str,
    source_binding_id: str,
    expected_configuration_sha256: str,
) -> dict[str, Any]:
    """Validate GET v2 admission and return only source-owned cursor facts.

    Summary state and history pages are deliberately not projected into the LB
    reducer. QRS owns durable attempt history and recovery state.
    """
    body = _mapping(payload)
    if (
        body.get("schema_version") != _GET_SCHEMA
        or body.get("ok") is not True
        or body.get("source_id") != source_id
        or body.get("target_id") != target_id
        or body.get("configuration_sha256") != expected_configuration_sha256
        or body.get("source_binding_id") != source_binding_id
        or body.get("adopted") is not False
        or body.get("no_order") is not True
        or body.get("execution_authority_granted") is not False
    ):
        raise SourceContractError("source_admission_unavailable")
    if body.get("status") in {"uninitialized", "blocked"} and body.get("checkpoint") is None:
        authority_revision = _revision(body.get("authority_revision"))
        checkpoint_revision = _revision(body.get("checkpoint_revision"))
        history_page = _mapping(body.get("history_page"))
        history_items = history_page.get("items")
        snapshot_through_id = history_page.get("snapshot_through_id")
        if (
            authority_revision < 1
            or checkpoint_revision != 0
            or body.get("observed_at") is not None
            or body.get("observation_sha256") is not None
            or body.get("received_at") is not None
            or body.get("covered_through") is not None
            or body.get("coverage_complete") is not False
            or body.get("coverage_incomplete_reason") is not None
            or not isinstance(history_items, list)
            or len(history_items) > 50
            or type(snapshot_through_id) is not int
            or snapshot_through_id < 0
            or type(history_page.get("complete")) is not bool
            or history_page.get("next_cursor") is not None
            and not isinstance(history_page.get("next_cursor"), str)
            or body.get("prior_partitions") is None
            or not isinstance(body.get("prior_partitions"), list)
            or body.get("continuity") not in {"same_binding", "unconfirmed"}
            or (
                body.get("status") == "uninitialized"
                and (
                    history_items != []
                    or snapshot_through_id != 0
                    or history_page.get("complete") is not True
                    or history_page.get("next_cursor") is not None
                )
            )
            or (
                body.get("status") == "blocked"
                and body.get("continuity") != "unconfirmed"
            )
        ):
            raise SourceContractError("source_contract_invalid")
        required_from = _canonical_utc(body.get("required_from"))
        return {
            "source_id": source_id,
            "target_id": target_id,
            "source_binding_id": source_binding_id,
            "configuration_sha256": expected_configuration_sha256,
            "required_from": required_from,
            "covered_through": None,
            "observed_at": None,
            "authority_revision": authority_revision,
            "checkpoint_revision": 0,
            "coverage_complete": False,
            "coverage_incomplete_reason": None,
            "baseline_established": False,
            "admission_status": body["status"],
            "continuity": body["continuity"],
        }
    checkpoint = _mapping(body.get("checkpoint"))
    state = _mapping(checkpoint.get("state"))
    if (
        not isinstance(body.get("observation_sha256"), str)
        or not _HEX64.fullmatch(body["observation_sha256"])
        or checkpoint.get("schema_version") != _CHECKPOINT_SCHEMA
        or type(checkpoint.get("baseline_established")) is not bool
        or not isinstance(checkpoint.get("configuration_sha256"), str)
        or not _HEX64.fullmatch(checkpoint["configuration_sha256"])
        or checkpoint.get("configuration_sha256") != expected_configuration_sha256
        or checkpoint.get("required_from") != body.get("required_from")
        or state.get("schema_version") != _SUMMARY_SCHEMA
        or state.get("target_id") != target_id
        or state.get("source_binding_id") != source_binding_id
        or state.get("history_complete") is not False
        or state.get("execution_authority_granted") is not False
    ):
        raise SourceContractError("source_admission_unavailable")
    state_fields = (
        "incident_count",
        "fault_attempt_count",
        "unresolved_count",
        "resolution_history_count",
    )
    if any(type(state.get(key)) is not int or state[key] < 0 for key in state_fields):
        raise SourceContractError("source_contract_invalid")
    covered_through = _timestamp(checkpoint.get("covered_through"), nullable=True)
    top_covered_through = _timestamp(body.get("covered_through"), nullable=True)
    coverage_complete = body.get("coverage_complete")
    coverage_reason = body.get("coverage_incomplete_reason")
    if (
        top_covered_through != covered_through
        or type(coverage_complete) is not bool
        or (coverage_complete and coverage_reason is not None)
        or (not coverage_complete and not isinstance(coverage_reason, str))
        or (
            checkpoint["baseline_established"] is False
            and (coverage_complete or covered_through is not None)
        )
    ):
        raise SourceContractError("source_contract_invalid")
    required_from = _canonical_utc(body.get("required_from"))
    _timestamp(body.get("observed_at"))
    _timestamp(body.get("received_at"))
    authority_revision = _revision(body.get("authority_revision"))
    checkpoint_revision = _revision(body.get("checkpoint_revision"))
    history_page = _mapping(body.get("history_page"))
    if not isinstance(history_page.get("items"), list):
        raise SourceContractError("source_contract_invalid")
    return {
        "source_id": source_id,
        "target_id": target_id,
        "source_binding_id": source_binding_id,
        "configuration_sha256": checkpoint["configuration_sha256"],
        "required_from": required_from,
        "covered_through": covered_through,
        "observed_at": body["observed_at"],
        "authority_revision": authority_revision,
        "checkpoint_revision": checkpoint_revision,
        "coverage_complete": coverage_complete,
        "coverage_incomplete_reason": coverage_reason,
        "baseline_established": checkpoint["baseline_established"],
        "admission_status": body.get("status"),
    }


def validate_source_ack(
    payload: Any,
    *,
    source_id: str,
    target_id: str,
    source_binding_id: str,
    configuration_sha256: str,
    required_from: str,
    expected_observation_sha256: str,
    expected_observed_at: str,
    expected_coverage_through: str,
    expected_previous_covered_through: str | None,
) -> dict[str, Any]:
    """Accept only a durable ACK for the exact submitted observation."""
    body = _mapping(payload)
    checkpoint = _mapping(body.get("checkpoint"))
    state = _mapping(checkpoint.get("state"))
    if (
        body.get("ok") is not True
        or body.get("schema_version") != _ACK_SCHEMA
        or body.get("result") not in {"stored", "unchanged"}
        or body.get("source_id") != source_id
        or body.get("target_id") != target_id
        or body.get("configuration_sha256") != configuration_sha256
        or body.get("source_binding_id") != source_binding_id
        or body.get("observation_sha256") != expected_observation_sha256
        or body.get("adopted") is not False
        or body.get("no_order") is not True
        or body.get("execution_authority_granted") is not False
        or body.get("required_from") != required_from
        or checkpoint.get("schema_version") != _CHECKPOINT_SCHEMA
        or type(checkpoint.get("baseline_established")) is not bool
        or checkpoint.get("configuration_sha256") != configuration_sha256
        or checkpoint.get("required_from") != required_from
        or state.get("schema_version") != _SUMMARY_SCHEMA
        or state.get("target_id") != target_id
        or state.get("source_binding_id") != source_binding_id
        or state.get("execution_authority_granted") is not False
    ):
        raise SourceContractError("source_ack_unconfirmed")
    observed_at = _canonical_utc(body.get("observed_at"))
    _timestamp(body.get("received_at"))
    if observed_at != _canonical_utc(expected_observed_at):
        raise SourceContractError("source_ack_unconfirmed")
    covered_through = _timestamp(checkpoint.get("covered_through"), nullable=True)
    top_covered_through = _timestamp(body.get("covered_through"), nullable=True)
    coverage_complete = body.get("coverage_complete")
    coverage_reason = body.get("coverage_incomplete_reason")
    if (
        top_covered_through != covered_through
        or type(coverage_complete) is not bool
        or (coverage_complete and coverage_reason is not None)
        or (not coverage_complete and coverage_reason not in {
            "scan_incomplete", "coverage_gap", "coverage_not_advanced"
        })
        or (coverage_complete and covered_through != expected_coverage_through)
        or (not coverage_complete and covered_through != expected_previous_covered_through)
        or (coverage_complete and checkpoint["baseline_established"] is not True)
    ):
        raise SourceContractError("source_ack_unconfirmed")
    authority_revision = _revision(body.get("authority_revision"))
    checkpoint_revision = _revision(body.get("checkpoint_revision"))
    if authority_revision == 0 or checkpoint_revision == 0:
        raise SourceContractError("source_ack_unconfirmed")
    return {
        "result": body["result"],
        "observation_sha256": body["observation_sha256"],
        "authority_revision": authority_revision,
        "checkpoint_revision": checkpoint_revision,
        "covered_through": covered_through,
        "coverage_complete": coverage_complete,
        "coverage_incomplete_reason": coverage_reason,
    }
