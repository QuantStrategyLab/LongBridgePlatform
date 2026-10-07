"""Pure, bounded LB PAPER evidence projection; never grants execution authority.

Callers supply already-read scheduler, page, report and baseline facts. This
module does not collect them, authenticate a caller, infer physical-account
continuity, run a strategy, persist state, or invoke a broker. The context must
come from the platform's existing trusted target/source admission boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from application.broker_reconciliation import (
    LongBridgeReconciliationCandidate,
    validate_reconciliation_candidate,
)
from quant_platform_kit.common.broker_reconciliation import (
    BrokerReconciliationEvidence,
    evaluate_broker_reconciliation_recovery,
)
from quant_platform_kit.common.execution_receipts import (
    EXECUTION_RECEIPT_OUTCOMES,
    attach_execution_receipt,
)

SCHEMA_VERSION = "qsl.runtime_cycle_health.v1"
MAX_CYCLES = 20
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT = re.compile(r"^execution-receipt\.[0-9a-f]{32}$")
_SUCCESS = frozenset({"no_action", "no_signal", "no_rebalance", "not_due", "filled"})
_DIGESTS = (
    "account_scope_sha256",
    "positions_sha256",
    "cash_sha256",
    "open_orders_sha256",
    "recent_executions_sha256",
    "local_execution_ledger_sha256",
)
_CYCLE_FIELDS = {
    "cycle_id",
    "scheduled_for",
    "receipt_ref",
    "completed_at",
    "outcome",
    "execution_state",
    "correlation",
}
_SCHEDULE_REASONS = {
    "not_due": {"no_cron_on_business_date", "before_schedule"},
    "market_closed": {"market_closed"},
    "outside_window": {"outside_expected_window"},
    "within_grace": {"publication_grace_open"},
    "due": {"publication_grace_ended"},
    "unevaluable": {
        "missing_timezone",
        "invalid_timezone",
        "missing_schedule",
        "invalid_schedule",
        "missing_market_calendar",
        "market_calendar_unavailable",
    },
}


def _instant(value):
    if not isinstance(value, (str, datetime)):
        raise ValueError("invalid instant")
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, str)
        else value
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("instant needs timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return _instant(value).isoformat().replace("+00:00", "Z")


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _hex(value):
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError("invalid digest")
    return value


def _fields(value, expected):
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise ValueError("invalid fields")
    return value


@dataclass(frozen=True)
class CycleContext:
    target_id: str
    service: str
    source_binding_id: str
    configuration_sha256: str
    strategy_profile: str
    strategy_revision: str
    scheduler_job_name: str

    def __post_init__(self):
        _hex(self.source_binding_id)
        _hex(self.configuration_sha256)
        if not re.fullmatch(r"[0-9a-f]{40}", self.strategy_revision):
            raise ValueError("invalid strategy revision")
        for value in (
            self.target_id,
            self.service,
            self.strategy_profile,
            self.scheduler_job_name,
        ):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 256
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("invalid target identity")


def cycle_id(context, scheduled_for):
    return "cycle." + _digest(
        [
            context.target_id,
            context.source_binding_id,
            context.configuration_sha256,
            _iso(scheduled_for),
        ]
    )


def _receipt(context, report, now):
    if not isinstance(report, Mapping):
        raise ValueError("report unavailable")
    if (
        report.get("platform") != "longbridge"
        or report.get("service_name") != context.service
        or report.get("strategy_profile") != context.strategy_profile
        or report.get("account_scope") not in {"PAPER", "paper"}
        or report.get("dry_run") is not False
        or report.get("validation_only") is not False
    ):
        raise ValueError("report target/lane mismatch")
    # This existing QPK function validates the canonical digest and report-bound
    # profile, release revision and lane. Copying avoids modifying caller facts.
    validated = attach_execution_receipt(
        deepcopy(dict(report)), report.get("execution_receipt")
    )
    receipt = validated["execution_receipt"]
    if (
        receipt["strategy_revision"] != context.strategy_revision
        or receipt["execution_mode"] != "paper"
    ):
        raise ValueError("receipt revision/lane mismatch")
    started, completed = (
        _instant(report.get("started_at")),
        _instant(report.get("finished_at")),
    )
    # The real LB error fallback attaches its receipt in finally, after the
    # report was finalized. Its observation may legitimately follow finished_at.
    if not (
        started <= completed <= _instant(now)
        and started <= _instant(receipt["observed_at"]) <= _instant(now)
    ):
        raise ValueError("invalid report chronology")
    errors, summary = report.get("errors"), report.get("summary") or {}
    if not isinstance(errors, list) or not isinstance(summary, Mapping):
        raise ValueError("report completeness missing")
    pending = summary.get("orders_pending_count")
    submitted = summary.get("broker_submission_done")
    outcome = receipt["outcome"]
    if outcome in _SUCCESS and (
        type(pending) is not int or pending < 0 or type(submitted) is not bool
    ):
        raise ValueError("submission evidence missing")
    if pending is not None and (type(pending) is not int or pending < 0):
        raise ValueError("invalid pending evidence")
    if submitted is not None and type(submitted) is not bool:
        raise ValueError("invalid submission evidence")
    if outcome in _SUCCESS and (
        errors or report.get("status") in {"error", "failed", "blocked"} or pending
    ):
        raise ValueError("receipt summary conflict")
    if receipt["broker_confirmation"] == "not_applicable" and (pending or submitted):
        raise ValueError("no-submission evidence conflict")
    return receipt


def derive_coverage(
    *, context, required_prefixes, pages, reads, read_from, read_through, aborted=False
):
    """Derive completeness from supplied provider pages and actual report reads.

    Each page includes the token used for that request as request_token and
    the provider's items/nextPageToken. The collector must not manufacture an
    empty last page after a timeout/cap. The module itself performs no listing.
    """
    if _instant(read_from) > _instant(read_through) or type(aborted) is not bool:
        raise ValueError("invalid read interval")
    if (
        not required_prefixes
        or len(required_prefixes) > 366
        or len(set(required_prefixes)) != len(required_prefixes)
    ):
        raise ValueError("invalid required prefixes")
    found, terminal, errors = set(), True, False
    limit_hit = aborted
    if set(pages) != set(required_prefixes):
        errors = True
    for prefix in required_prefixes:
        if limit_hit:
            terminal = False
            break
        chain = pages.get(prefix, ())
        if not isinstance(chain, Sequence) or not chain or len(chain) > MAX_CYCLES + 1:
            terminal = False
            errors = True
            limit_hit = limit_hit or (
                isinstance(chain, Sequence) and len(chain) > MAX_CYCLES + 1
            )
            continue
        token, seen_tokens = None, set()
        for index, page in enumerate(chain):
            if not isinstance(page, Mapping) or page.get("request_token") != token:
                errors = True
                terminal = False
                break
            items = page.get("items")
            if not isinstance(items, list) or len(items) > MAX_CYCLES + 1:
                limit_hit = limit_hit or (
                    isinstance(items, list) and len(items) > MAX_CYCLES + 1
                )
                errors = True
                terminal = False
                break
            for item in items:
                name = item.get("name") if isinstance(item, Mapping) else None
                if (
                    not isinstance(name, str)
                    or not name.startswith(prefix)
                    or len(name) > 1024
                ):
                    errors = True
                else:
                    found.add(name)
            if len(found) > MAX_CYCLES:
                limit_hit = True
                terminal = False
                break
            token = page.get("nextPageToken")
            if token is not None and (
                not isinstance(token, str) or not token or token in seen_tokens
            ):
                errors = True
                terminal = False
                break
            if token is None and index != len(chain) - 1:
                errors = True
                terminal = False
                break
            seen_tokens.add(token)
        if token is not None:
            terminal = False
    successful = 0
    for name in sorted(found)[:MAX_CYCLES]:
        try:
            _receipt(context, reads.get(name), read_through)
            successful += 1
        except (ValueError, TypeError, KeyError):
            errors = True
    return {
        "from": _iso(read_from),
        "through": _iso(read_through),
        "listed_count": len(found),
        "read_count": successful,
        "terminal_page_seen": terminal,
        "limit_hit": limit_hit
        or len(found) > MAX_CYCLES
        or (len(found) == MAX_CYCLES and not terminal),
        "errors_present": errors,
    }


def coverage_complete(coverage, *, since=None, through=None):
    _fields(
        coverage,
        {
            "from",
            "through",
            "listed_count",
            "read_count",
            "terminal_page_seen",
            "limit_hit",
            "errors_present",
        },
    )
    start, end = _instant(coverage["from"]), _instant(coverage["through"])
    if start > end:
        raise ValueError("invalid coverage interval")
    for name in ("listed_count", "read_count"):
        if type(coverage[name]) is not int or coverage[name] < 0:
            raise ValueError("invalid coverage count")
    for name in ("terminal_page_seen", "limit_hit", "errors_present"):
        if type(coverage[name]) is not bool:
            raise ValueError("invalid coverage flag")
    return (
        coverage["terminal_page_seen"]
        and not coverage["limit_hit"]
        and not coverage["errors_present"]
        and coverage["listed_count"] == coverage["read_count"] <= MAX_CYCLES
        and (since is None or start <= _instant(since))
        and (through is None or end >= _instant(through))
    )


def project_cycle(context, report, *, scheduled_for, now):
    receipt = _receipt(context, report, now)
    scheduled = _instant(scheduled_for)
    if not scheduled <= _instant(report["started_at"]):
        raise ValueError("run precedes expected cycle")
    invocation = report.get("invocation")
    correlation = "window_matched"
    if invocation is not None:
        _fields(invocation, {"scheduler_job_name", "scheduled_for"})
        if (
            invocation["scheduler_job_name"] != context.scheduler_job_name
            or _instant(invocation["scheduled_for"]) != scheduled
        ):
            raise ValueError("invocation mismatch")
        correlation = "explicit"
    confirmation = receipt["broker_confirmation"]
    state = (
        "no_submission"
        if confirmation == "not_applicable"
        else "terminal_confirmed"
        if confirmation == "filled"
        else "unresolved"
    )
    return {
        "cycle_id": cycle_id(context, scheduled),
        "scheduled_for": _iso(scheduled),
        "receipt_ref": receipt["receipt_id"],
        "completed_at": _iso(report["finished_at"]),
        "outcome": receipt["outcome"],
        "execution_state": state,
        "correlation": correlation,
    }


def _check_cycle(context, cycle):
    _fields(cycle, _CYCLE_FIELDS)
    if cycle["cycle_id"] != cycle_id(context, cycle["scheduled_for"]):
        raise ValueError("cycle source/configuration mismatch")
    if cycle["receipt_ref"] is not None and (
        not isinstance(cycle["receipt_ref"], str)
        or not _RECEIPT.fullmatch(cycle["receipt_ref"])
    ):
        raise ValueError("invalid receipt reference")
    if cycle["execution_state"] not in {
        "no_submission",
        "terminal_confirmed",
        "unresolved",
    }:
        raise ValueError("invalid execution certainty")
    if cycle["correlation"] not in {"explicit", "window_matched", "unconfirmed"}:
        raise ValueError("invalid correlation")
    outcome = cycle["outcome"]
    if outcome not in EXECUTION_RECEIPT_OUTCOMES | {"missing_report"}:
        raise ValueError("invalid outcome")
    if outcome == "missing_report":
        if (
            cycle["receipt_ref"] is not None
            or cycle["completed_at"] is not None
            or cycle["execution_state"] != "unresolved"
        ):
            raise ValueError("invalid missing report")
    elif cycle["receipt_ref"] is None or cycle["completed_at"] is None:
        raise ValueError("missing receipt evidence")
    expected = (
        {"no_submission", "unresolved"}
        if outcome == "failed"
        else {"terminal_confirmed"}
        if outcome == "filled"
        else {"no_submission"}
        if outcome in _SUCCESS | {"risk_blocked"}
        else {"unresolved"}
    )
    if cycle["execution_state"] not in expected:
        raise ValueError("outcome certainty mismatch")
    if cycle["completed_at"] is not None and _instant(cycle["completed_at"]) < _instant(
        cycle["scheduled_for"]
    ):
        raise ValueError("invalid cycle completion")


def incident_category(cycle):
    if cycle["execution_state"] == "unresolved" or cycle["outcome"] == "missing_report":
        return "execution_uncertainty"
    return None if cycle["outcome"] in _SUCCESS else "operational"


def project_resolution(
    context,
    *,
    incident,
    kind,
    coverage,
    now,
    successful=None,
    reconciliation_candidate=None,
    baseline=None,
):
    """Validate raw existing evidence; neither grants execution nor collects it."""
    _check_cycle(context, incident)
    if incident_category(incident) is None:
        raise ValueError("not an incident")
    if not coverage_complete(coverage, since=incident["scheduled_for"]):
        raise ValueError("incomplete recovery coverage")
    if successful is not None:
        invocation = (
            successful.get("invocation") if isinstance(successful, Mapping) else None
        )
        if not isinstance(invocation, Mapping):
            raise ValueError("retry requires a raw explicitly correlated report")
        # Do not accept a caller-supplied success/verified flag or projected
        # tuple as recovery proof; rerun the actual receipt/report validator.
        successful = project_cycle(
            context, successful, scheduled_for=invocation.get("scheduled_for"), now=now
        )
    resolved_by = None
    if kind == "same_cycle_retry_succeeded":
        if incident_category(incident) != "operational":
            raise ValueError("uncertain attempt requires reconciliation")
        _check_cycle(context, successful)
        if (
            successful["cycle_id"] != incident["cycle_id"]
            or incident["correlation"] != "explicit"
            or successful["correlation"] != "explicit"
            or incident_category(successful) is not None
            or successful["receipt_ref"] == incident["receipt_ref"]
            or _instant(successful["completed_at"])
            <= _instant(incident["completed_at"])
        ):
            raise ValueError("retry is not a later successful same-cycle receipt")
        reference, verified = successful["receipt_ref"], successful["completed_at"]
        resolved_by = successful["cycle_id"]
    elif kind == "current_state_reconciled":
        _fields(
            baseline,
            {
                "source_binding_id",
                "configuration_sha256",
                "baseline_id",
                "runtime_target_sha256",
                "digests",
            },
        )
        if (
            baseline["source_binding_id"] != context.source_binding_id
            or baseline["configuration_sha256"] != context.configuration_sha256
        ):
            raise ValueError("recovery source/configuration mismatch")
        _fields(baseline["digests"], _DIGESTS)
        for value in baseline["digests"].values():
            _hex(value)
        _hex(baseline["runtime_target_sha256"])
        _fields(
            reconciliation_candidate,
            {
                "schema_version",
                "permits_active_lkg",
                "expected_digests_configured",
                "execution_ledger_records_count",
                "recovery_blockers",
                "evidence",
            },
        )
        evidence = BrokerReconciliationEvidence.from_dict(
            reconciliation_candidate["evidence"]
        )
        blockers = evaluate_broker_reconciliation_recovery(
            evidence,
            now=_instant(now),
            expected_platform_id="longbridge",
            expected_strategy_profile=context.strategy_profile,
            expected_baseline_id=baseline["baseline_id"],
            expected_runtime_target_sha256=baseline["runtime_target_sha256"],
            **{
                f"expected_{name}": value for name, value in baseline["digests"].items()
            },
        )
        count = reconciliation_candidate["execution_ledger_records_count"]
        if type(count) is not int or count < 0:
            raise ValueError("invalid ledger count")
        canonical = validate_reconciliation_candidate(
            LongBridgeReconciliationCandidate(
                evidence=evidence,
                recovery_blockers=blockers,
                expected_digests_configured=True,
                execution_ledger_records_count=count,
            )
        )
        if blockers or dict(reconciliation_candidate) != canonical:
            raise ValueError("reconciliation rejected")
        verified = _iso(evidence.observed_at)
        if _instant(verified) < _instant(
            incident["completed_at"] or incident["scheduled_for"]
        ):
            raise ValueError("reconciliation precedes incident")
        reference = evidence.evidence_sha256
        if successful is not None:
            _check_cycle(context, successful)
            if incident_category(successful) is not None or _instant(
                successful["completed_at"]
            ) > _instant(verified):
                raise ValueError("later cycle not covered by reconciliation")
            resolved_by = successful["cycle_id"]
    else:
        raise ValueError("unsupported resolution")
    if _instant(verified) > _instant(now) or not coverage_complete(
        coverage, since=incident["scheduled_for"], through=verified
    ):
        raise ValueError("recovery exceeds coverage")
    return {
        "incident_cycle_id": incident["cycle_id"],
        "kind": kind,
        "evidence_ref": reference,
        "resolved_by_cycle_id": resolved_by,
        "verified_through": verified,
    }


def _schedule(value, now):
    _fields(
        value,
        {"state", "reason", "timezone", "latest_due_at", "next_due_at", "deadline_at"},
    )
    if (
        value["state"] not in _SCHEDULE_REASONS
        or value["reason"] not in _SCHEDULE_REASONS[value["state"]]
    ):
        raise ValueError("invalid schedule reason")
    result = dict(value)
    timezone_name = value["timezone"]
    unavailable_timezone = value["state"] == "unevaluable" and value["reason"] in {
        "missing_timezone",
        "invalid_timezone",
    }
    if timezone_name is not None and not isinstance(timezone_name, str):
        raise ValueError("invalid timezone field")
    try:
        if not timezone_name:
            raise ValueError("timezone unavailable")
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        if not unavailable_timezone:
            raise ValueError("timezone unavailable for evaluable schedule") from None
        result["timezone"] = None
    for name in ("latest_due_at", "next_due_at", "deadline_at"):
        result[name] = None if value[name] is None else _iso(value[name])
    latest, upcoming, deadline = (
        result[k] for k in ("latest_due_at", "next_due_at", "deadline_at")
    )
    if latest is not None and _instant(latest) > _instant(now):
        raise ValueError("latest due in future")
    if upcoming is not None and _instant(upcoming) <= _instant(now):
        raise ValueError("next due elapsed")
    if (
        deadline is not None
        and latest is not None
        and _instant(deadline) < _instant(latest)
    ):
        raise ValueError("deadline before due")
    return result


def build_cycle_health(
    context, *, schedule, coverage, observations=(), resolution_requests=(), now
):
    """Project the six-member nested contract. Incomplete reads stay explicit."""
    selected = _schedule(schedule, now)
    coverage_complete(coverage)
    if _instant(coverage["through"]) > _instant(now):
        raise ValueError("future coverage")
    if len(observations) > MAX_CYCLES or len(resolution_requests) > MAX_CYCLES:
        raise ValueError("cycle limit exceeded")
    cycles = [
        project_cycle(
            context, item["report"], scheduled_for=item["scheduled_for"], now=now
        )
        for item in observations
    ]
    # Exact duplicates are idempotent; different receipts remain distinct attempts.
    cycles = list({_digest(item): item for item in cycles}.values())
    latest, deadline = selected["latest_due_at"], selected["deadline_at"]
    if (
        selected["state"] == "due"
        and latest is not None
        and deadline is not None
        and _instant(deadline) <= _instant(now)
        and coverage_complete(coverage, since=latest, through=deadline)
        and not any(item["scheduled_for"] == latest for item in cycles)
    ):
        cycles.append(
            {
                "cycle_id": cycle_id(context, latest),
                "scheduled_for": latest,
                "receipt_ref": None,
                "completed_at": None,
                "outcome": "missing_report",
                "execution_state": "unresolved",
                "correlation": "unconfirmed",
            }
        )
    if len(cycles) > MAX_CYCLES:
        raise ValueError("cycle limit exceeded")
    resolutions = [
        project_resolution(context, coverage=coverage, now=now, **request)
        for request in resolution_requests
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "configuration_sha256": context.configuration_sha256,
        "schedule": selected,
        "coverage": deepcopy(dict(coverage)),
        "cycles": cycles,
        "resolutions": resolutions,
    }


def _fault_time(cycle):
    return _instant(cycle["completed_at"] or cycle["scheduled_for"])


def _attempt_key(cycle):
    return cycle["receipt_ref"] or "missing:" + cycle["cycle_id"]


def _refresh_incident(item):
    """Keep severity, current unresolved status and chronology independent."""
    attempts = item["fault_attempts"]
    attempts.sort(
        key=lambda attempt: (
            _fault_time(attempt["cycle"]),
            _attempt_key(attempt["cycle"]),
        )
    )
    active = [attempt for attempt in attempts if attempt["resolution_ref"] is None]
    representative = max(
        attempts,
        key=lambda attempt: (
            incident_category(attempt["cycle"]) == "execution_uncertainty",
            _fault_time(attempt["cycle"]),
            _attempt_key(attempt["cycle"]),
        ),
    )["cycle"]
    item["cycle"] = deepcopy(representative)
    item["category"] = incident_category(representative)
    item["latest_fault_at"] = _iso(
        max(_fault_time(attempt["cycle"]) for attempt in attempts)
    )
    item["active_category"] = (
        "execution_uncertainty"
        if any(
            incident_category(attempt["cycle"]) == "execution_uncertainty"
            for attempt in active
        )
        else "operational"
        if active
        else None
    )
    item["resolution_history"].sort(
        key=lambda resolution: (
            _instant(resolution["verified_through"]),
            resolution["evidence_ref"],
        )
    )
    item["resolution"] = None if active else deepcopy(item["resolution_history"][-1])


def reduce_incidents(context, cycles, *, resolutions=(), previous=None):
    """Reference reducer for already admitted projections, not an ingress API.

    Every retained attempt has stable receipt identity and explicit resolution
    coverage. No prior attempt or resolution is forgotten to meet a size cap.
    DO ordering/freshness and persistence remain outside this local increment.
    """
    identity = {
        "target_id": context.target_id,
        "source_binding_id": context.source_binding_id,
    }
    state = (
        deepcopy(previous) if previous is not None else {**identity, "incidents": []}
    )
    if any(state.get(key) != value for key, value in identity.items()):
        raise ValueError("binding_changed_unconfirmed")
    incidents = state["incidents"]
    for item in incidents:
        # The earlier local-only prototype did not retain attempts. Never infer
        # missing replay history from its one representative record.
        if not item.get("fault_attempts") or "resolution_history" not in item:
            raise ValueError("checkpoint missing attempt history")
        _refresh_incident(item)
    for cycle in cycles:
        _check_cycle(context, cycle)
        if incident_category(cycle) is None:
            continue
        existing = next(
            (
                item
                for item in incidents
                if item["cycle"]["cycle_id"] == cycle["cycle_id"]
            ),
            None,
        )
        if existing is None:
            existing = {"fault_attempts": [], "resolution_history": []}
            incidents.append(existing)
        key = _attempt_key(cycle)
        known = next(
            (
                attempt
                for attempt in existing["fault_attempts"]
                if _attempt_key(attempt["cycle"]) == key
            ),
            None,
        )
        if known is not None:
            if known["cycle"] != cycle:
                raise ValueError("conflicting fault receipt replay")
            # A later scan re-listing this exact known attempt changes nothing,
            # including a resolution that already covered it.
            continue
        if len(existing["fault_attempts"]) >= MAX_CYCLES:
            raise ValueError("fault attempt history requires durable pagination")
        existing["fault_attempts"].append(
            {"cycle": deepcopy(cycle), "resolution_ref": None}
        )
        # A genuinely new fault always remains unresolved, even if late-arriving;
        # time alone does not prove an earlier reconciliation saw that evidence.
        _refresh_incident(existing)
    for resolution in resolutions:
        _fields(
            resolution,
            {
                "incident_cycle_id",
                "kind",
                "evidence_ref",
                "resolved_by_cycle_id",
                "verified_through",
            },
        )
        item = next(
            (
                item
                for item in incidents
                if item["cycle"]["cycle_id"] == resolution["incident_cycle_id"]
            ),
            None,
        )
        if item is None:
            raise ValueError("unknown incident")
        prior = next(
            (
                entry
                for entry in item["resolution_history"]
                if entry["evidence_ref"] == resolution["evidence_ref"]
            ),
            None,
        )
        if prior is not None:
            if prior != resolution:
                raise ValueError("conflicting recovery evidence replay")
            # A prior accepted proof resolves only the attempts it originally
            # covered, never a subsequently discovered or newer fault.
            continue
        active = [
            attempt
            for attempt in item["fault_attempts"]
            if attempt["resolution_ref"] is None
        ]
        for attempt in active:
            _check_cycle(context, attempt["cycle"])
        if _instant(resolution["verified_through"]) < _instant(item["latest_fault_at"]):
            raise ValueError("resolution precedes retained fault watermark")
        if resolution["kind"] == "same_cycle_retry_succeeded":
            if (
                not active
                or any(
                    incident_category(attempt["cycle"]) != "operational"
                    or attempt["cycle"]["correlation"] != "explicit"
                    for attempt in active
                )
                or not any(
                    cycle["cycle_id"]
                    == resolution["resolved_by_cycle_id"]
                    == resolution["incident_cycle_id"]
                    and cycle["receipt_ref"] == resolution["evidence_ref"]
                    and incident_category(cycle) is None
                    and cycle["correlation"] == "explicit"
                    and _instant(cycle["completed_at"])
                    > _instant(item["latest_fault_at"])
                    and _instant(cycle["completed_at"])
                    <= _instant(resolution["verified_through"])
                    for cycle in cycles
                )
            ):
                raise ValueError("retry resolution mismatch")
        elif resolution["kind"] == "current_state_reconciled":
            _hex(resolution["evidence_ref"])
        else:
            raise ValueError("unsupported resolution")
        if len(item["resolution_history"]) >= MAX_CYCLES:
            raise ValueError("resolution history requires durable pagination")
        item["resolution_history"].append(deepcopy(resolution))
        for attempt in active:
            attempt["resolution_ref"] = resolution["evidence_ref"]
        _refresh_incident(item)
    if len(incidents) > MAX_CYCLES:
        raise ValueError("incident checkpoint requires durable history pagination")
    state["unresolved_count"] = sum(
        item["active_category"] is not None for item in incidents
    )
    state["execution_authority_granted"] = False
    return state
