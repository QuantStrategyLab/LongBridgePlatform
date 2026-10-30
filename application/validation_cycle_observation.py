"""Request-scoped, fail-closed observation for an explicit dry-run cycle."""

from __future__ import annotations

import contextvars
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Iterator


class ValidationSubmitBlocked(RuntimeError):
    """The validation-only adapter never forwards an order."""


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


def current() -> ValidationCycleObservation | None:
    return _CURRENT.get()


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
