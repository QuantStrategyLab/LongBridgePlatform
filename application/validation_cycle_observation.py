"""Request-scoped, fail-closed observation for an explicit dry-run cycle."""

from __future__ import annotations

import contextvars
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Iterator
from urllib.parse import urlsplit


class ValidationSubmitBlocked(RuntimeError):
    """The validation-only adapter never forwards an order."""


class ValidationJournalEmpty(RuntimeError):
    """No cloud reports were available to assess the observed journal."""


@dataclass
class ValidationCycleObservation:
    adapter_instrumented: bool = False
    cycle_entered: bool = False
    cycle_returned: bool = False
    report_status: str = "unknown"
    execution_receipt_present: bool = False
    _adapter_attempts: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def block_submit(self, *_args, **_kwargs):
        with self._lock:
            self._adapter_attempts += 1
        raise ValidationSubmitBlocked("validation adapter submission blocked")

    def payload(self, trace_header: str | None, project: str | None) -> dict:
        with self._lock:
            attempts = self._adapter_attempts if self.adapter_instrumented else None
        payload = {
            "event": "validation_cycle_observation",
            "schema": "longbridge.validation-cycle-observation.v1",
            "adapter_instrumented": self.adapter_instrumented,
            "adapter_submit_attempt_count": attempts,
            "cycle_entered": self.cycle_entered,
            "cycle_returned": self.cycle_returned,
            "report_status": self.report_status,
            "execution_receipt_present": self.execution_receipt_present,
            "broker_submit_count": None,
            "trace_correlation": "unverified",
        }
        header = str(trace_header or "")
        trace_id = header.split("/", 1)[0]
        if (re.fullmatch(r"[0-9a-f]{32}", trace_id)
                and isinstance(project, str)
                and re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)):
            payload["logging.googleapis.com/trace"] = f"projects/{project}/traces/{trace_id}"
        return payload


_CURRENT: contextvars.ContextVar[ValidationCycleObservation | None] = contextvars.ContextVar(
    "longbridge_validation_cycle", default=None
)

STRICT_REPORT_SCAN_LIMIT = 10000


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise ValueError("nonstandard JSON constant")


def strict_prior_report_exists(store, *, platform, strategy_profile, account_scope,
                               signal_date, effective_date, dry_run_only) -> bool:
    """Search the listed cloud report scope; failure never means no report."""
    from collections.abc import Mapping
    from datetime import date
    from quant_platform_kit.cloud.gcp_provider import GcpObjectStore
    from quant_platform_kit.common.execution_state import (
        _report_effective_date_keys, _report_matches_execution, _report_signal_date_keys,
    )
    from quant_platform_kit.common.runtime_reports import runtime_report_relative_path

    try:
        identity = (platform, strategy_profile, account_scope)
        if (any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", value)
                or value.lower() in {"unknown", "unknown-profile", "unknown-platform"} for value in identity)
                or type(dry_run_only) is not bool
                or not any(str(value or "").strip() for value in (signal_date, effective_date))):
            raise ValueError
        for value in (signal_date, effective_date):
            if value:
                date.fromisoformat(str(value)[:10])
        cloud_uri = getattr(store, "cloud_prefix_uri", None)
        if not isinstance(cloud_uri, str) or not cloud_uri.startswith("gs://"):
            raise ValueError
        path = runtime_report_relative_path({
            "platform": platform, "strategy_profile": strategy_profile,
            "account_scope": account_scope, "started_at": "2001-01-01T00:00:00+00:00",
            "run_id": "validation-probe",
        })
        prefix = f"{cloud_uri.rstrip('/')}/{path.parent.parent.as_posix()}/"
        objects = store._object_store()
        if not isinstance(objects, GcpObjectStore):
            raise ValueError
        parsed_prefix = urlsplit(prefix)
        bucket_name = parsed_prefix.netloc
        object_prefix = parsed_prefix.path.lstrip("/")
        blobs = objects.client.bucket(bucket_name).list_blobs(
            prefix=object_prefix, max_results=STRICT_REPORT_SCAN_LIMIT + 1
        )
        report_count = 0
        for count, blob in enumerate(blobs, start=1):
            if count > STRICT_REPORT_SCAN_LIMIT:
                raise ValueError
            name = getattr(blob, "name", None)
            if not isinstance(name, str) or not name.startswith(object_prefix):
                raise ValueError
            if not name.endswith(".json"):
                continue
            report_count += 1
            uri = f"gs://{bucket_name}/{name}"
            payload = json.loads(objects.read_text(uri), object_pairs_hook=_unique_json_object,
                                 parse_constant=_reject_json_constant)
            if (not isinstance(payload, Mapping)
                    or not all(isinstance(payload.get(key), str) and payload[key].strip()
                               for key in ("platform", "strategy_profile", "account_scope"))
                    or not isinstance(payload.get("status"), str) or not payload["status"].strip()
                    or type(payload.get("dry_run")) is not bool
                    or not isinstance(payload.get("summary"), Mapping)):
                raise ValueError
            if not all(payload[key].strip().lower() == value.lower()
                       for key, value in zip(("platform", "strategy_profile", "account_scope"), identity)):
                raise ValueError
            if payload["dry_run"] == dry_run_only:
                if payload["status"] not in {"ok", "error", "skipped"}:
                    raise ValueError
                summary = payload["summary"]
                if "action_done" in summary and type(summary["action_done"]) is not bool:
                    raise ValueError
                count_fields = ("order_events_count", "orders_previewed_count",
                                "orders_skipped_count", "orders_pending_count")
                if any(type(summary[field]) is not int or summary[field] < 0
                       for field in count_fields if field in summary):
                    raise ValueError
                signal_keys = _report_signal_date_keys(payload, payload["summary"])
                effective_keys = _report_effective_date_keys(payload, payload["summary"])
                if ((signal_date and not signal_keys) or (effective_date and not effective_keys)):
                    raise ValueError
                for value in signal_keys | effective_keys:
                    date.fromisoformat(value)
            if _report_matches_execution(
                payload, platform=platform, strategy_profile=strategy_profile,
                account_scope=account_scope, signal_date=str(signal_date or ""),
                effective_date=str(effective_date or ""), dry_run_only=dry_run_only,
            ):
                return True
            if (payload["dry_run"] == dry_run_only
                    and (payload["status"] != "ok" or type(summary.get("action_done")) is not bool
                         or not all(field in summary for field in count_fields[:3]))):
                raise ValueError
        if not report_count:
            raise ValidationJournalEmpty
        return False
    except ValidationJournalEmpty:
        raise RuntimeError("validation_journal_empty") from None
    except Exception:
        raise RuntimeError("validation_journal_read_failed") from None


def current() -> ValidationCycleObservation | None:
    return _CURRENT.get()


def require_matching_journal_source(config, reporting) -> None:
    """Reject observed cycles when marker and report prefixes differ or are absent."""
    store = getattr(config, "execution_state_store", None)
    marker_uri = getattr(store, "cloud_prefix_uri", None)
    report_uri = getattr(reporting, "report_gcs_prefix_uri", None)
    if not isinstance(marker_uri, str) or not isinstance(report_uri, str):
        raise RuntimeError("validation_journal_unavailable")
    marker_uri = marker_uri.strip().rstrip("/")
    report_uri = report_uri.strip().rstrip("/")
    try:
        parsed = urlsplit(marker_uri)
        valid = (parsed.scheme == "gs" and bool(parsed.netloc)
                 and not parsed.query and not parsed.fragment
                 and not parsed.username and not parsed.password and parsed.port is None)
    except ValueError:
        valid = False
    if not valid or marker_uri != report_uri:
        raise RuntimeError("validation_journal_unavailable")


def instrument_composer(composer, observation: ValidationCycleObservation):
    protected = replace(composer, broker_adapters=replace(composer.broker_adapters, submit_order_fn=observation.block_submit))
    observation.adapter_instrumented = True
    return protected


@contextmanager
def observe(trace_header: str | None, project: str | None) -> Iterator[ValidationCycleObservation]:
    observation = ValidationCycleObservation()
    token = _CURRENT.set(observation)
    try:
        yield observation
    finally:
        _CURRENT.reset(token)
        try:
            print(json.dumps(observation.payload(trace_header, project), sort_keys=True), flush=True)
        except Exception:
            # Optional evidence must not change the request's original result.
            pass
