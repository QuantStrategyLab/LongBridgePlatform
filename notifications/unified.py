"""LongBridge adapter for opt-in, target-bound notification candidates.

Only notification facts and markers belong here. No broker, execution-state,
schedule, risk-admission or runtime enablement mutations are permitted.
"""
from __future__ import annotations

import json
import os
import hashlib
import re
from dataclasses import replace
from datetime import datetime, timezone
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from .digest_snapshot.unified_contract import (
    NotificationEvent, IncidentTracker, IncidentDecision, CandidatePersistenceError,
    effective_notification_mode, unified_notification_enabled, route_notification_event,
)
from .unified_store import GCSNotificationStore


FLAG = "LONGBRIDGE_UNIFIED_NOTIFICATION_ENABLED"
_TARGET_KEYS = ("platform_id", "strategy_profile", "service_name", "account_scope",
                "account_selector", "deployment_selector", "dry_run_only")
_REPORT = ContextVar("longbridge_notification_report", default=None)
_ROUTE = ContextVar("longbridge_notification_route", default=None)
_VALIDATION = ContextVar("longbridge_notification_validation", default=False)


@contextmanager
def bind_report(report, *, validation_only=False):
    token = _REPORT.set(report)
    validation_token = _VALIDATION.set(validation_only)
    try:
        yield
    finally:
        _VALIDATION.reset(validation_token)
        _REPORT.reset(token)


@contextmanager
def bind_route(route):
    token = _ROUTE.set(route)
    try:
        yield
    finally:
        _ROUTE.reset(token)


def enabled(env=None):
    env = os.environ if env is None else env
    return unified_notification_enabled(env, FLAG)


def routing_env(env=None):
    env = os.environ if env is None else env
    if not enabled(env):
        return env
    # The release flag selects the existing single routing mode. It does not
    # mutate the process environment or authorize any runtime target.
    return {**env, "QSL_ROUTINE_NOTIFICATION_MODE": effective_notification_mode(env, FLAG),
            "QSL_DIGEST_ROUTINE_ORDERS": "false"}


def target_binding(env=None, *, report=None):
    env = os.environ if env is None else env
    if env.get("RUNTIME_TARGET_ENABLED") != "true":
        raise ValueError("notification_target_not_enabled")
    target = json.loads(env.get("RUNTIME_TARGET_JSON") or "{}")
    if not isinstance(target, dict) or any(key not in target for key in _TARGET_KEYS):
        raise ValueError("notification_target_binding_missing")
    for key in _TARGET_KEYS[:-1]:
        if not target[key]:
            raise ValueError("notification_target_binding_missing")
    if (type(target["dry_run_only"]) is not bool
            or not isinstance(target["account_selector"], list)
            or any(not isinstance(value, str) or not value.strip() for value in target["account_selector"])
            or any(not isinstance(target[key], str) or not target[key].strip() for key in _TARGET_KEYS[:-1] if key != "account_selector")):
        raise ValueError("notification_target_binding_missing")
    if target.get("runtime_target_enabled") is False:
        raise ValueError("notification_target_not_enabled")
    if report is not None:
        source = report.get("runtime_target")
        if not isinstance(source, Mapping) or any(source.get(key) != target[key] for key in _TARGET_KEYS):
            raise ValueError("notification_source_binding_mismatch")
        if (report.get("platform") != target["platform_id"]
                or report.get("strategy_profile") != target["strategy_profile"]
                or report.get("service_name") != target["service_name"]
                or report.get("dry_run") is not target["dry_run_only"]):
            raise ValueError("notification_source_binding_mismatch")
    target_id = str(env.get("DIGEST_TARGET_ID") or "").strip()
    account_uid = str(env.get("DIGEST_OPAQUE_ACCOUNT_UID") or "").strip()
    matrix_id = str(env.get("DIGEST_MATRIX_TARGET_ID") or "").strip()
    matrix_scope = str(env.get("DIGEST_MATRIX_ACCOUNT_SCOPE") or "").strip()
    if not target_id or not account_uid:
        raise ValueError("notification_identity_unknown")
    if (matrix_id and target_id != matrix_id) or (matrix_scope and target["account_scope"] != matrix_scope):
        raise ValueError("notification_matrix_binding_mismatch")
    return {"platform_id": target["platform_id"], "strategy_profile": target["strategy_profile"],
            "target_id": target_id, "opaque_account_uid": account_uid}, target


def _stamp(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("notification_time_timezone_missing")
    return stamp.astimezone(timezone.utc)


def make_event(kind, *, env=None, report=None, incident=None, occurred_at=None, run_id=None, **counts):
    env = os.environ if env is None else env
    report = report if report is not None else _REPORT.get()
    identity, _ = target_binding(env, report=report)
    source_run = run_id or (report or {}).get("run_id")
    stamp = occurred_at or (report or {}).get("started_at")
    if not source_run or not stamp:
        raise ValueError("notification_source_run_unknown")
    return NotificationEvent(kind, _stamp(stamp), **identity, source_run_id=str(source_run),
                             incident_reason=incident, **counts)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def report_archive_confirmed(report, persisted_path, configured_prefix):
    """Require the existing persister's confirmed cloud URI, not a local path."""
    prefix = str(configured_prefix or "").rstrip("/")
    artifacts = report.get("artifacts") if isinstance(report, Mapping) else None
    return bool(prefix.startswith("gs://") and isinstance(persisted_path, str)
                and persisted_path.startswith(prefix + "/") and isinstance(artifacts, Mapping)
                and artifacts.get("runtime_report_cloud_uri") == persisted_path)


def _export_projection(store, state_store, key, state):
    version, previous = store.read(key)
    _, latest = state_store.read(f"candidate_state/{key}")
    if latest != state:
        raise CandidatePersistenceError("notification_candidate_concurrent_update")
    allowed = {"platform_id", "strategy_profile", "opaque_account_uid", "target_id", "business_day", "actually_ran",
               "as_of", "status", "execution_status", "order_count", "fill_count", "cycle_count", "field_status",
               "evidence_provenance", "reason_code", "execution_mode", "source_run_id", "notification_events",
               "scheduled_idle", "routine_observation_counts"}
    rows = [{key: value for key, value in row.items() if key in allowed} for row in state["runs"]]
    for row in rows:
        row.update(evidence_persistence_status="persisted", evidence_delivery_status="pending_central")
    payload = {"schema_version": "qsl.digest_candidates.v1", "producer_status": "projected", "runs": rows}
    if previous == payload:
        return True
    if not store.compare_and_set(key, version, payload):
        raise CandidatePersistenceError("notification_candidate_concurrent_update")
    return True


def persist_candidate(event, *, env=None, actually_ran=True, dry_run=False, preview_count=None, store=None, state_store=None, not_run=False, skip_reason=None, observation_counts=None):
    """Bridge private event facts into the existing per-target candidate object.

    Merge by source run, never by evidence-source count. Runtime and archive
    producers share the same run identity and must agree on its facts.
    """
    env = os.environ if env is None else env
    identity, target = target_binding(env)
    dry_run = target["dry_run_only"]
    if any(event.get(key) != value for key, value in identity.items()) or event.get("coverage_status") != "known":
        raise CandidatePersistenceError("notification_candidate_identity_unknown")
    slot = str(env.get("DIGEST_STORE_TARGET") or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", slot):
        raise ValueError("candidate_store_identity_missing")
    injected_store = store
    store = store or GCSNotificationStore(env.get("DIGEST_CANDIDATES_GCS_URI", ""))
    state_store = state_store or injected_store or GCSNotificationStore(env.get("QSL_NOTIFICATION_MARKER_GCS_URI", ""))
    key = f"{identity['platform_id']}/{event['business_day']}/{slot}"
    version, previous = state_store.read(f"candidate_state/{key}")
    previous = previous or {}
    if not previous and store.read(key)[1] is not None:
        raise ValueError("notification_candidate_history_unknown")
    scope_key = _hash(identity)
    if previous and previous.get("notification_scope_key") != scope_key:
        # An old-format or differently bound object cannot be silently replaced.
        raise ValueError("notification_candidate_scope_conflict")
    events = dict(previous.get("notification_events") or {})
    fact_key = event["dedupe_key"]
    conflicts = list(previous.get("notification_conflicts") or [])
    if fact_key in events and events[fact_key] != event:
        # Preserve different observations without interpreting event counters
        # as whole-run counters. Archive run facts below own those counters.
        fact_key = f"{fact_key}-{_hash(event)}"
    events[fact_key] = event
    sources = dict(previous.get("notification_source_runs") or {})
    if not_run:
        if actually_ran or event["order_count"] is not None or event["fill_count"] is not None:
            raise ValueError("candidate_non_execution_conflict")
        source = {"not_run": True, "as_of": event["occurred_at"], "skip_reason": skip_reason}
        old_source = sources.get(event["source_run_id"])
        if old_source is not None and old_source != source:
            raise ValueError("candidate_non_execution_conflict")
        sources[event["source_run_id"]] = source
    if actually_ran:
        source = {"order_count": event["order_count"], "fill_count": event["fill_count"],
                  "dry_run": dry_run, "preview_count": preview_count,
                  "as_of": event["occurred_at"], "event_kind": event["event_kind"]}
        run_id = event["source_run_id"]
        old_source = sources.get(run_id, {})
        if observation_counts is None and _REPORT.get() is not None:
            observation_counts = (_REPORT.get().get("summary") or {}).get("routine_observation_counts")
        if observation_counts is not None:
            allowed = {"watch", "opportunity_watch", "panic_reversal", "taco_rebound"}
            if not isinstance(observation_counts, Mapping) or any(k not in allowed or type(v) is not int or v < 0 for k, v in observation_counts.items()):
                raise ValueError("candidate_plugin_observations_invalid")
            if "routine_observation_counts" in old_source and old_source["routine_observation_counts"] != observation_counts:
                raise ValueError("candidate_plugin_observations_conflict")
            source["routine_observation_counts"] = dict(observation_counts)
        elif "routine_observation_counts" in old_source:
            source["routine_observation_counts"] = old_source["routine_observation_counts"]
        if old_source.get("not_run"):
            raise ValueError("candidate_non_execution_conflict")
        for name in ("order_count", "fill_count", "preview_count"):
            old, observed = old_source.get(name), source[name]
            if old is not None and observed is not None and old != observed:
                conflict = {"source_run_id": run_id, "field": name, "previous": old, "observed": observed}
                if conflict not in conflicts:
                    conflicts.append(conflict)
            if old is not None:
                source[name] = old
        sources[run_id] = source
    rows = []
    for run_id, source in sorted(sources.items()):
        run_events = [item for item in events.values() if item["source_run_id"] == run_id]
        if source.get("not_run"):
            rows.append({**identity, "source_run_id": run_id, "business_day": event["business_day"],
                         "actually_ran": False, "as_of": source["as_of"],
                         "status": "ok" if source["skip_reason"] == "market_closed" else "alert",
                         "execution_status": "not_run", "reason_code": source["skip_reason"],
                         "order_count": None, "fill_count": None, "cycle_count": 0,
                         "field_status": {"order_count": "unknown", "fill_count": "unknown", "cycle_count": "known"},
                         "notification_events": []})
            continue
        run_conflicts = [item for item in conflicts if item["source_run_id"] == run_id]
        counts = {name: source[name] if not any(item.get("field", name) == name for item in run_conflicts) else None
                  for name in ("order_count", "fill_count", "preview_count")}
        urgent = bool(run_conflicts) or any(item["event_kind"] not in {
            "routine_cycle", "healthy_heartbeat", "order_submitted", "fill"} for item in run_events)
        row = {**identity, "source_run_id": run_id, "business_day": event["business_day"],
               "actually_ran": True, "as_of": source["as_of"],
               "status": "alert" if urgent else "ok", "execution_status": "execution_alert" if urgent else
                   "executed" if counts["order_count"] else "ok_no_order",
               **counts, "cycle_count": 1, "dry_run": dry_run,
               "execution_mode": "dry_run" if dry_run else "live",
               "field_status": {name: "known" if counts[name] is not None else "unknown"
                                for name in ("order_count", "fill_count")},
               "evidence_provenance": "candidates", "persistence_status": "persisted", "delivery_status": "pending_central",
               "notification_events": run_events}
        row["field_status"]["cycle_count"] = "known"
        if "routine_observation_counts" in source:
            row["routine_observation_counts"] = source["routine_observation_counts"]
        if not urgent and row["order_count"] is None and not dry_run:
            row["execution_status"] = "execution_unknown"
        if urgent:
            row["reason_code"] = "source_cycle_alert"
        rows.append(row)
    payload = {"schema_version": "qsl.digest_candidates.v1", "producer_status": "projected", "runs": rows,
               "notification_scope_key": scope_key, "notification_events": events, "notification_source_runs": sources,
               "notification_conflicts": conflicts}
    if payload != previous and not state_store.compare_and_set(f"candidate_state/{key}", version, payload):
        # No retries after an uncertain write; an explicit CAS conflict is visible.
        raise CandidatePersistenceError("notification_candidate_concurrent_update")
    return _export_projection(store, state_store, key, payload)


class _StaleObservation(RuntimeError):
    pass


class _OrderedStore:
    def __init__(self, store, stamp):
        self.store, self.stamp = store, stamp

    def read(self, key):
        version, value = self.store.read(key)
        if value and value.get("observed_at") and _stamp(value["observed_at"]) > self.stamp:
            raise _StaleObservation("notification_observation_out_of_order")
        return version, value

    def compare_and_set(self, key, version, value):
        return self.store.compare_and_set(key, version, {**value, "observed_at": self.stamp.isoformat()})

    def advance(self, key):
        version, value = self.read(key)
        if value and value.get("observed_at") != self.stamp.isoformat():
            if not self.compare_and_set(key, version, value):
                raise RuntimeError("notification_marker_concurrent_update")


def observe_incident(event, *, state, severity=1, recovered=False, env=None, store=None):
    env = os.environ if env is None else env
    store = store or GCSNotificationStore(env.get("QSL_NOTIFICATION_MARKER_GCS_URI", ""))
    ordered = _OrderedStore(store, event.occurred_at)
    try:
        decision = IncidentTracker(ordered).observe(event, state=state, severity=severity, recovered=recovered)
        if decision.incident_key:
            ordered.advance(decision.incident_key)
        group = next((prefix for prefix in ("cycle_attention:", "new_risk_attention:", "runtime_issue:")
                      if str(event.incident_reason or "").startswith(prefix)), None)
        if not recovered and group:
            identity = [event.platform_id, event.target_id, event.opaque_account_uid, event.strategy_profile]
            index_key = f"incident_groups/{_hash([*identity, group])}"
            version, index = store.read(index_key)
            reasons = dict((index or {}).get("reasons") or {})
            if reasons.get(event.incident_reason) != event.source_run_id:
                reasons[event.incident_reason] = event.source_run_id
                if not store.compare_and_set(index_key, version, {"reasons": reasons}):
                    raise RuntimeError("notification_marker_concurrent_update")
        return decision, IncidentTracker(store)
    except _StaleObservation:
        return IncidentDecision("duplicate"), IncidentTracker(store)


def recover_incident_group(event, group, *, env=None, store=None, skip_current_run=False):
    env = os.environ if env is None else env
    store = store or GCSNotificationStore(env.get("QSL_NOTIFICATION_MARKER_GCS_URI", ""))
    identity = [event.platform_id, event.target_id, event.opaque_account_uid, event.strategy_profile]
    _, index = store.read(f"incident_groups/{_hash([*identity, group])}")
    for reason, source_run in (index or {}).get("reasons", {}).items():
        if skip_current_run and source_run == event.source_run_id:
            continue
        observe_incident(replace(event, incident_reason=reason), state="healthy", recovered=True, severity=0, env=env, store=store)


def recover_cycle_incidents(event, *, env=None, store=None):
    return recover_incident_group(event, "cycle_attention:", env=env, store=store)


def send_incident(event, send, *, state=None, severity=1, env=None, store=None):
    decision, tracker = observe_incident(event, state=state or event.event_kind, severity=severity, env=env, store=store)
    if decision.action != "send":
        return False
    try:
        result = send()
    except Exception:
        tracker.record_delivery(decision)
        raise
    # Existing boolean senders acknowledge transport only. A sender may supply
    # its actual unique send-history receipt, but we never manufacture one.
    receipt = result.get("receipt_id") if isinstance(result, Mapping) else None
    if (not isinstance(receipt, str) or not receipt.strip() or receipt.startswith("transport-ack:")
            or receipt in {decision.transition_key, event.to_candidate()["dedupe_key"]}):
        receipt = None
    tracker.record_delivery(decision, receipt_id=receipt)
    return result is True or receipt is not None


def coverage_locale():
    raw = str(os.environ.get("QSL_NOTIFY_LANG") or "").strip()
    raw = raw or str(os.environ.get("NOTIFY_LANG") or "").strip() or "zh"
    return "en" if raw.lower().startswith("en") else "zh"


def _storage_failure(notify_issue):
    if notify_issue is None:
        return
    try:
        if coverage_locale() == "en":
            return notify_issue("Notification evidence unavailable", "Candidate storage or incident history could not be confirmed.")
        return notify_issue("通知证据不可用", "无法确认候选记录或告警历史，请检查通知存储。")
    except Exception:
        print("notification_coverage_diagnostic_failed", flush=True)


def _safe_send_incident(event, send, **kwargs):
    try:
        return send_incident(event, send, **kwargs)
    except Exception as exc:
        # A missing/unreadable reservation cannot authorize an untracked send.
        print(f"notification_incident_unconfirmed error_type={type(exc).__name__}", flush=True)
        record_failure("incident_history_unconfirmed")
        return False


def record_failure(code, *, report=None):
    report = report if report is not None else _REPORT.get()
    if report is not None:
        failures = report.setdefault("notification_unified_errors", [])
        if code not in failures:
            failures.append(code)


def cycle_facts(result, *, dry_run=False, attention_reasons=()):
    from .digest_snapshot.digest_routing import defer_routine_cycle_to_digest
    evidence = {**dict(getattr(result, "allocation", {}) or {}), **dict(getattr(result, "execution", {}) or {})}
    action = bool(getattr(result, "action_done", False))
    pending = tuple(getattr(result, "pending_orders", ()) or ())
    evidence.setdefault("execution_status", "completed" if action else "no_action")
    evidence["broker_submission_done"] = action and not dry_run
    evidence["orders_pending_count"] = len(pending)
    evidence["notification_attention_reason_codes"] = tuple(getattr(result, "notification_attention_reason_codes", ()) or ()) + tuple(attention_reasons)
    # Passed diagnostics do not constitute a risk escalation. Keep all others.
    evidence["risk_flags"] = tuple(flag for flag in (evidence.get("risk_flags") or ()) if flag != "risk_gate:passed")
    healthy = defer_routine_cycle_to_digest(evidence, submitted_order_count=0, mode="daily_digest", include_orders=False)
    kind = "routine_cycle" if healthy else "real_error"
    status = str(evidence.get("execution_status") or "").lower()
    if pending or "unknown" in status or "pending" in status:
        kind = "order_status_unknown"
    elif evidence.get("auth_expired"):
        kind = "auth_failure"
    elif status in {"blocked", "rejected"} or evidence.get("execution_blocked_reason") or evidence.get("blocked_reason"):
        kind = "blocked_run"
    elif action and not dry_run and status in {"", "completed", "executed", "submitted"} and not any(evidence.get(key) for key in (
            "error", "errors", "fail_reason", "plugin_error", "persistence_error", "report_persistence_error",
            "notification_error", "notification_attention_reason_codes", "risk_flags", "execution_blocked",
            "execution_blocking_skips", "account_new_risk_buy_blocked", "small_account_buy_blocked",
            "outcome_unknown", "submission_halted", "reconciliation_required", "market_data_retry_required",
            "strategy_run_persistence_error", "strategy_plugin_error", "strategy_plugin_alert_error",
            "data_error", "failed_order_count", "notification_order_issue")) and str(evidence.get("risk_gate") or "").upper() not in {
                "BLOCK", "BLOCKED", "REJECT", "REJECTED", "DENY", "DENIED"}:
        kind = "order_submitted"
    known_zero = healthy and not dry_run and not action and not pending
    return kind, {"schema_version": "qsl.notification_cycle.v1", "order_count": 0 if known_zero else None,
                  "fill_count": 0 if known_zero else None, "routine": healthy and not dry_run, "dry_run": dry_run}


def cycle_incident(result, kind):
    if kind in {"routine_cycle", "order_submitted"}:
        return None
    evidence = dict(getattr(result, "execution", {}) or {})
    reason = next((str(evidence[key]) for key in ("blocked_reason", "execution_blocked_reason", "no_op_reason")
                   if isinstance(evidence.get(key), str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", evidence[key])), "unknown")
    identities = []
    from .digest_snapshot.unified_contract import order_identity_digest
    for order in getattr(result, "pending_orders", ()) or ():
        if isinstance(order, Mapping):
            identity = order.get("order_id") or order.get("broker_order_id")
            if identity:
                identities.append(order_identity_digest(str(identity)))
    return f"cycle_attention:{kind}:{reason}" + (f":{_hash(sorted(identities))}" if identities else "")


def prepare_cycle(result, *, config, publisher, notify_issue):
    """Persist routine facts before suppressing their original notification."""
    from .events import NotificationPublisher
    if _VALIDATION.get():
        return False, publisher
    kind = None
    try:
        kind, facts = cycle_facts(result, dry_run=config.dry_run_only,
                                  attention_reasons=getattr(config, "notification_attention_reason_codes", ()))
        if kind not in {"routine_cycle", "order_submitted"} and not enabled():
            return False, publisher
        event = make_event(kind, incident=cycle_incident(result, kind),
                           order_count=facts["order_count"], fill_count=facts["fill_count"])
        if kind == "order_submitted":
            # There is no complete order collection on ExecutionCycleResult.
            # This is a run fact, not an invented individual order event.
            fact = replace(event, event_kind="routine_cycle", incident_reason=None).to_candidate()
            if persist_candidate(fact, dry_run=False) is not True:
                raise CandidatePersistenceError("notification_candidate_write_unconfirmed")
            return True, publisher
        result_route = route_notification_event(event, env=routing_env(), flag_key=FLAG,
            persist_candidate=lambda fact: persist_candidate(fact, dry_run=config.dry_run_only,
                preview_count=len(getattr(result, "dry_run_orders", ()) or ()) if config.dry_run_only else None),
            legacy=lambda: None, immediate=lambda: None)
        if not result_route.candidate_persisted:
            record_failure("candidate_persistence_unconfirmed")
            failure = replace(event, event_kind="real_error", incident_reason="candidate_persistence_failed")
            _safe_send_incident(failure, lambda: _storage_failure(notify_issue))
        if kind == "routine_cycle":
            # Only this same source's clean terminal result proves recovery.
            try:
                recover_cycle_incidents(event)
                observe_incident(replace(event, incident_reason="candidate_persistence_failed"), state="persisted", recovered=True, severity=0)
            except Exception:
                print("notification_recovery_unconfirmed", flush=True)
                record_failure("incident_recovery_unconfirmed")
            return True, publisher
        wrapped = NotificationPublisher(log_message=publisher.log_message,
            send_message=lambda text: _safe_send_incident(event, lambda: publisher.send_message(text)))
        return False, wrapped
    except Exception as exc:
        print(f"notification_candidate_unconfirmed error_type={type(exc).__name__}", flush=True)
        record_failure("candidate_persistence_unconfirmed")
        try:
            failure = make_event("real_error", incident="candidate_persistence_failed")
            send_incident(failure, lambda: _storage_failure(notify_issue))
        except Exception:
            print("notification_identity_or_history_unknown", flush=True)
            _storage_failure(notify_issue)
        if kind not in {"routine_cycle", "order_submitted"}:
            return False, publisher
        # Candidate failure is an explicit operational failure, never a healthy
        # zero-trade confirmation. Broker/result/persistence paths stay intact.
        return False, NotificationPublisher(log_message=publisher.log_message, send_message=lambda text: False)


def dispatch_immediate(send, *, kind, incident, report=None, occurred_at=None, run_id=None,
                       actually_ran=False, state=None, severity=1):
    if not enabled():
        return send()
    try:
        event = make_event(kind, report=report, incident=incident, occurred_at=occurred_at, run_id=run_id)
        def immediate():
            with bind_report(report if report is not None else _REPORT.get()):
                return _safe_send_incident(event, send, state=state, severity=severity)
        if not actually_ran and report is None and _REPORT.get() is None:
            # A missing receipt has no business run to export. The QRS
            # contract has no independent non-counting monitor channel.
            return immediate()
        route = route_notification_event(event, env=routing_env(), flag_key=FLAG,
            persist_candidate=lambda fact: persist_candidate(fact, actually_ran=actually_ran,
                dry_run=bool((report or {}).get("dry_run"))), legacy=send,
            immediate=immediate)
        if not route.candidate_persisted:
            print("notification_candidate_unconfirmed coverage_status=unknown", flush=True)
            record_failure("candidate_persistence_unconfirmed", report=report)
        return route.result
    except Exception as exc:
        print(f"notification_incident_unconfirmed error_type={type(exc).__name__}", flush=True)
        record_failure("incident_history_unconfirmed", report=report)
        return False


def record_order_resolution(order_event):
    from .digest_snapshot.unified_contract import order_identity_digest
    try:
        digest = order_identity_digest(order_event.order_id)
        fact = make_event("fill", order_execution_digest=digest)
        persist_candidate(fact.to_candidate(), actually_ran=False)
        for stream in ("order_status", "order_status_poll"):
            observe_incident(replace(fact, incident_reason=f"{stream}:{digest}"), state="filled", recovered=True, severity=0)
    except Exception:
        record_failure("order_notification_evidence_unconfirmed")


def recover_runtime(*, report=None, route_label=None, occurred_at=None, run_id=None, probe=False):
    if not enabled():
        return
    if report is not None:
        if report.get("status") != "ok" or report.get("notification_unified_errors"):
            return
        summary = report.get("summary") or {}
        if summary.get("orders_pending_count") or summary.get("execution_status") in {"blocked", "pending_reconciliation"}:
            return
    try:
        reasons = ("unknown", "paper_live_conflict")
        incidents = ([f"runtime_route:{route_label}:{reason}" for reason in reasons] if route_label else
                     ["runtime_failure:health_probe"] if probe else
                     [f"runtime_failure:{phase}:{reason}" for phase in (
                         "market_hours", "runtime_configuration", "strategy_cycle", "reporting") for reason in reasons])
        # Only a persisted successful run/probe can recover its own route.
        # A plain HTTP success (for example a skipped market window) is not proof.
        if report is not None and _ROUTE.get():
            incidents.extend(f"runtime_route:{_ROUTE.get()}:{reason}" for reason in reasons)
        for incident in incidents:
            event = make_event("real_error", report=report, incident=incident, occurred_at=occurred_at, run_id=run_id)
            observe_incident(event, state="healthy", recovered=True, severity=0)
        if report is not None and not probe:
            recover_incident_group(event, "runtime_issue:", skip_current_run=True)
    except Exception:
        record_failure("runtime_recovery_unconfirmed", report=report)


def attach_cycle_event(summary, result, *, report, config):
    kind, facts = cycle_facts(result, dry_run=config.dry_run_only,
                             attention_reasons=getattr(config, "notification_attention_reason_codes", ()))
    if kind == "order_submitted":
        kind = "routine_cycle"
    event = make_event(kind, report=report, incident=cycle_incident(result, kind),
                       order_count=facts["order_count"], fill_count=facts["fill_count"])
    summary["notification_digest"] = {**facts, "event": event.to_candidate(),
        "preview_count": len(getattr(result, "dry_run_orders", ()) or ()) if config.dry_run_only else None}


def _archived_event(report, env):
    identity, target = target_binding(env, report=report)
    if report.get("schema_version") != "runtime_report.v1" or report.get("status") not in {
            "ok", "completed", "success", "error", "aborted", "failed", "skipped"}:
        raise ValueError("candidate_source_incomplete")
    facts = (report.get("summary") or {}).get("notification_digest") or {}
    if report.get("status") == "skipped":
        if ((report.get("diagnostics") or {}).get("skip_reason") not in
                {"market_closed", "fractional_share_execution_unsupported"} or facts):
            raise ValueError("candidate_non_execution_conflict")
    known = facts.get("schema_version") == "qsl.notification_cycle.v1" and facts.get("dry_run") is target["dry_run_only"]
    failed = report.get("status") in {"error", "aborted", "failed"}
    event = make_event("real_error" if failed else "routine_cycle", env=env, report=report,
        incident="runtime_failure" if failed else None,
        order_count=facts.get("order_count") if known else None,
        fill_count=facts.get("fill_count") if known else None)
    saved = facts.get("event")
    if saved is not None:
        if (not isinstance(saved, dict) or any(saved.get(key) != value for key, value in identity.items())
                or saved.get("source_run_id") != report.get("run_id")
                or _stamp(saved.get("occurred_at")) != event.occurred_at):
            raise ValueError("notification_archived_event_mismatch")
        # The run summary owns counts; a saved event cannot contradict it.
        if any(saved.get(name) != getattr(event, name) for name in ("order_count", "fill_count")):
            raise ValueError("notification_archived_event_mismatch")
        event = replace(event, event_kind=saved["event_kind"], incident_reason=saved.get("incident_reason"),
                        order_count=saved.get("order_count"), fill_count=saved.get("fill_count"))
        canonical = event.to_candidate()
        normalized = dict(saved)
        # QPK 07e24c0 NotificationEvent declares these five payload fields
        # nullable. The pinned runtime-report finalizer omits their nulls.
        # Normalize only this event envelope, never identity/window fields or
        # known counts, and preserve every present value and unexpected key.
        for name in ("incident_reason", "order_execution_digest", "expected_block_evidence",
                     "order_count", "fill_count"):
            if name not in normalized and canonical[name] is None:
                normalized[name] = None
        if canonical != normalized:
            raise ValueError("notification_archived_event_mismatch")
    return event, facts


def persist_archived_report(report, *, env, store=None, state_store=None, heartbeat=False):
    event, facts = _archived_event(report, env)
    skipped = report.get("status") == "skipped"
    _, target = target_binding(env, report=report)
    return persist_candidate(event.to_candidate(), env=env, store=store, state_store=state_store,
                             actually_ran=not skipped, not_run=skipped, dry_run=target["dry_run_only"],
                             skip_reason=(report.get("diagnostics") or {}).get("skip_reason") if skipped else None,
                             preview_count=facts.get("preview_count") if heartbeat or target["dry_run_only"] else None,
                             observation_counts=(report.get("summary") or {}).get("routine_observation_counts"))


def heartbeat_observation(*, now, report=None, recovery_at=None, env=None):
    env = os.environ if env is None else env
    try:
        if not isinstance(report, Mapping):
            raise ValueError("notification_receipt_business_run_unknown")
        archived, facts = _archived_event(report, env)
        if archived.occurred_at > _stamp(now):
            raise ValueError("notification_receipt_time_invalid")
        persist_archived_report(report, env=env, heartbeat=True)
        if report.get("status") == "skipped":
            return
        # The observed receipt owns both the run identity and its business
        # window. A next-window monitor job is not another business run.
        event = replace(archived, event_kind="healthy_heartbeat", incident_reason="execution_receipt",
                        order_count=None, fill_count=None)
        persist_candidate(event.to_candidate(), env=env, actually_ran=False)
        if recovery_at is not None:
            observe_incident(replace(event, occurred_at=_stamp(recovery_at)), state="accepted",
                             recovered=True, severity=0, env=env)
    except Exception as exc:
        print(f"notification_heartbeat_candidate_unconfirmed error_type={type(exc).__name__}", flush=True)
        raise


def publish_attention(decision, *, recover, telegram_sender=None):
    from quant_platform_kit.risk.attention import render_attention_compact
    from quant_platform_kit.risk.attention_notify import _default_telegram_sender
    from .display_names import display_name
    try:
        event = make_event("risk_escalation", incident=f"new_risk_attention:{_hash(sorted(decision.reason_codes))}")
        if decision.level.value not in {"action", "halt"}:
            if recover:
                recover_incident_group(event, "new_risk_attention:")
            return {"sent": 0, "skipped": 1, "failed": 0}
        identity, _ = target_binding()
        locale = os.environ.get("QSL_NOTIFY_LANG") or os.environ.get("NOTIFY_LANG") or "zh"
        english = str(locale).lower().startswith("en")
        text = render_attention_compact(locale=locale,
            platform=display_name("platforms", identity["platform_id"], locale, fallback="Platform" if english else "平台"),
            account_alias=display_name("accounts", identity["opaque_account_uid"], locale, platform=identity["platform_id"],
                                       fallback="Account" if english else "账户"),
            strategy_profile=display_name("strategies", identity["strategy_profile"], locale,
                                          fallback="Unnamed strategy" if english else "未命名策略"), decision=decision)
        sender = telegram_sender or _default_telegram_sender
        outcome = dispatch_immediate(lambda: bool(sender(text=text, alert_key=event.incident_reason)),
            kind="risk_escalation", incident=event.incident_reason, state=decision.level.value,
            severity=decision.level.severity_order)
        return {"sent": int(outcome is True), "skipped": int(outcome is not True), "failed": 0}
    except Exception as exc:
        print(f"notification_risk_history_unconfirmed error_type={type(exc).__name__}", flush=True)
        record_failure("risk_notification_history_unconfirmed")
        return {"sent": 0, "skipped": 0, "failed": 1}


def emit_archived(env, *, now=None, read=None, store=None):
    """Catch up the completed window from existing reports, including PAPER.

    No runtime invocation, target enablement or scheduler changes. The same
    source run and routine event kind are used by the synchronous producer.
    """
    from .digest_snapshot.digest_archive import _read
    from .digest_snapshot.unified_contract import sgt_business_window
    from datetime import timedelta
    if env.get("RUNTIME_TARGET_ENABLED") == "false":
        return {"status": "target_disabled", "uploaded": False}
    values = env.get("DIGEST_TARGET_BINDINGS_JSON", "").strip()
    if values:
        bindings = json.loads(values)
        if not isinstance(bindings, list) or not all(isinstance(value, dict) for value in bindings):
            raise ValueError("candidate_bindings_invalid")
        selected = [value for value in bindings if value.get("DIGEST_TARGET_ID") == env.get("DIGEST_MATRIX_TARGET_ID")]
        if len(selected) != 1:
            raise ValueError("notification_matrix_binding_unknown")
        env = {**env, **selected[0], "DIGEST_TARGET_BINDINGS_JSON": ""}
    identity, target = target_binding(env)
    current = _stamp(now or datetime.now(timezone.utc))
    window = sgt_business_window(current)
    start, end = window.start - timedelta(days=1), window.start
    prefix = str(env.get("EXECUTION_REPORT_GCS_URI") or "").rstrip("/")
    from .unified_store import validate_prefix
    validate_prefix(prefix)
    read = read or _read
    months = {start.astimezone(timezone.utc).strftime("%Y-%m"), end.astimezone(timezone.utc).strftime("%Y-%m")}
    entries = []
    for month in sorted(months):
        entries.extend(read(["ls", "--json", f"{prefix}/{identity['platform_id']}/**/{month}/*.json"]))
    if len(entries) > 10000:
        raise ValueError("candidate_archive_budget_exceeded")
    seen, count = set(), 0
    for entry in entries:
        uri = str(entry.get("url") or "").split("#", 1)[0]
        if uri in seen:
            continue
        seen.add(uri)
        if not uri.startswith(f"{prefix}/{identity['platform_id']}/"):
            raise ValueError("candidate_source_locator_mismatch")
        updated = _stamp((entry.get("metadata") or {}).get("updated") or (entry.get("metadata") or {}).get("timeCreated"))
        if not start <= updated <= current:
            continue
        report = read(["cat", uri])
        source = report.get("runtime_target") or {}
        if any(source.get(key) != target[key] for key in _TARGET_KEYS):
            continue
        stamp = _stamp(report.get("started_at"))
        if not start <= stamp < end:
            continue
        # Run facts keep the cycle kind even when real-order notifications were
        # immediate. Absence of per-order evidence remains null, never zero.
        event, facts = _archived_event(report, env)
        persist_archived_report(report, env=env, store=store)
        count += 1
    if not count:
        # Absence is not proof of a healthy no-run window. Preserve the existing
        # archive producer's fail-closed schedule check for expected live runs.
        schedule = env.get("DIGEST_NOTIFICATION_SCHEDULE_JSON")
        if not schedule:
            raise ValueError("candidate_source_absent")
        from quant_platform_kit.common.runtime_heartbeat_policy import filter_due_targets
        due, evaluated = filter_due_targets([json.loads(schedule)], since=start, now=end - timedelta(microseconds=1),
            publication_grace=timedelta(0), warning_logger=lambda message: None)
        if not evaluated or due:
            raise ValueError("candidate_due_source_absent")
        # This is a proved idle window, with no invented run or event time.
        injected_store = store
        store = store or GCSNotificationStore(env.get("DIGEST_CANDIDATES_GCS_URI", ""))
        state_store = injected_store or GCSNotificationStore(env.get("QSL_NOTIFICATION_MARKER_GCS_URI", ""))
        slot = str(env.get("DIGEST_STORE_TARGET") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", slot):
            raise ValueError("candidate_store_identity_missing")
        key = f"{identity['platform_id']}/{start.date().isoformat()}/{slot}"
        version, previous = state_store.read(f"candidate_state/{key}")
        if not previous and store.read(key)[1] is not None:
            raise ValueError("notification_candidate_history_unknown")
        if previous:
            if previous.get("notification_scope_key") != _hash(identity):
                raise ValueError("notification_candidate_scope_conflict")
            if previous.get("notification_source_runs"):
                raise ValueError("notification_idle_contradicts_runs")
        payload = {"schema_version": "qsl.digest_candidates.v1", "notification_scope_key": _hash(identity),
                   "notification_events": {}, "notification_source_runs": {}, "notification_conflicts": [],
                   "runs": [{**identity, "business_day": start.date().isoformat(), "actually_ran": False,
                             "scheduled_idle": True, "status": "ok", "evidence_provenance": "candidates"}]}
        if payload != previous and not state_store.compare_and_set(f"candidate_state/{key}", version, payload):
            raise CandidatePersistenceError("notification_candidate_concurrent_update")
        _export_projection(store, state_store, key, payload)
    return {"status": "projected", "uploaded": True, "runs": count}
