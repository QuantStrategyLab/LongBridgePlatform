"""Calendar, validation and append-only observation state machine."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
WORKING_STATUSES = frozenset(
    {
        "NOTREPORTED",
        "REPLACEDNOTREPORTED",
        "PROTECTEDNOTREPORTED",
        "VARIETIESNOTREPORTED",
        "WAITTONEW",
        "NEW",
        "WAITTOREPLACE",
        "PENDINGREPLACE",
        "REPLACED",
        "PARTIALFILLED",
        "WAITTOCANCEL",
        "PENDINGCANCEL",
    }
)


class ObservationError(Exception):
    """Only fixed error codes may cross the process boundary."""


def instant(value):
    try:
        value = datetime.fromisoformat(value)
        if value.utcoffset() is None:
            raise ValueError
        return value.astimezone(UTC)
    except (ValueError, TypeError):
        raise ObservationError("schema_invalid") from None


def slot(now):
    return (
        now.astimezone(UTC)
        .replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        .strftime("%Y%m%dT%H%MZ")
    )


class Calendar:
    def __init__(self, path=Path(__file__).with_name("calendar.json")):
        data = json.loads(path.read_text())
        checksum = hashlib.sha256(
            json.dumps(data["sessions"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if checksum != data["sha256"]:
            raise ObservationError("calendar_unavailable")
        self.start = date.fromisoformat(data["coverage_start"])
        self.end = date.fromisoformat(data["coverage_end"])
        self.sessions = data["sessions"]

    def due(self, day):
        if not self.start <= day <= self.end:
            raise ObservationError("calendar_unavailable")
        close = self.sessions.get(day.isoformat())
        if close is None:
            return None
        return datetime.fromisoformat(f"{day}T{close}").replace(tzinfo=NY).astimezone(
            UTC
        ) + timedelta(minutes=30)

    def expected(self, start, now):
        day = start
        end = now.astimezone(NY).date()
        self.due(end)
        while day <= end:
            due = self.due(day)
            if due is not None and due <= now:
                yield day, due
            day += timedelta(days=1)


def text(value, pattern):
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ObservationError("schema_invalid")
    return value


def amount(value):
    try:
        if (
            not isinstance(value, str)
            or len(value) > 64
            or not Decimal(value).is_finite()
        ):
            raise ValueError
        return value
    except (ValueError, InvalidOperation):
        raise ObservationError("schema_invalid") from None


def rows(raw, fields):
    if not isinstance(raw, list) or len(raw) > 5000:
        raise ObservationError("schema_invalid")
    if not all(isinstance(row, dict) for row in raw):
        fail()
    return [{key: check(row.get(key)) for key, check in fields.items()} for row in raw]


def fail():
    raise ObservationError("schema_invalid")


def _currency(value):
    return text(value, r"[A-Z]{3}")


def _symbol(value):
    return text(value, r"[A-Z0-9.-]{1,24}\.(US|HK)")


_ORDER_FIELDS = {
    "symbol": _symbol,
    "currency": _currency,
    "status": lambda x: text(x, r"[A-Z_]{1,32}"),
    "side": lambda x: text(x, r"BUY|SELL"),
    "order_type": lambda x: text(x, r"[A-Z_]{1,32}"),
    "quantity": amount,
    "executed_quantity": amount,
    "submitted_at": lambda x: instant(x).isoformat(),
    "updated_at": lambda x: instant(x).isoformat() if x else None,
    "order_digest": lambda x: text(x, r"[a-f0-9]{64}"),
}
_ROW_FIELDS = {
    "cash": {
        "currency": _currency,
        **dict.fromkeys(("available_cash", "frozen_cash", "settling_cash"), amount),
    },
    "broker_reported_balances": {
        "currency": _currency, "net_assets": amount, "total_cash": amount,
    },
    "positions": {"symbol": _symbol, "currency": _currency, "quantity": amount},
    "recent_orders_7d": _ORDER_FIELDS,
    "known_non_terminal_orders_7d": _ORDER_FIELDS,
}


def sanitize(payload, binding, now):
    try:
        return _sanitize(payload, binding, now)
    except ObservationError:
        raise
    except (TypeError, AttributeError, KeyError, ValueError):
        raise ObservationError("schema_invalid") from None


def _sanitize(payload, binding, now):
    """Strict field allowlist; no raw provider strings, IDs or binding in output."""
    if not isinstance(payload, dict):
        fail()
    checks = {
        "schema_version": "longbridge_account_snapshot.v1",
        "status": "partial",
        "account_scope": "PAPER",
        "no_order": True,
        "live_authority_granted": False,
        "snapshot_atomic": False,
        "cash_complete": True,
        "positions_complete": True,
    }
    if any(
        type(payload.get(k)) is not type(v) or payload[k] != v
        for k, v in checks.items()
    ):
        fail()
    source = payload.get("source_binding", {})
    if (
        source.get("status") != "bound"
        or source.get("id") != binding
        or not re.fullmatch(r"[a-f0-9]{64}", binding)
    ):
        raise ObservationError("identity_failure")
    started, finished = (
        instant(payload.get(k)) for k in ("observed_started_at", "observed_finished_at")
    )
    if (
        not now - timedelta(minutes=5)
        <= started
        <= finished
        <= now + timedelta(seconds=30)
    ):
        fail()

    cash = rows(payload.get("cash"), _ROW_FIELDS["cash"])
    balances = rows(
        payload.get("broker_reported_balances"), _ROW_FIELDS["broker_reported_balances"]
    )
    positions = rows(payload.get("positions"), _ROW_FIELDS["positions"])
    query = payload.get("recent_orders_query", {})
    begin, end = instant(query.get("start_at")), instant(query.get("end_at"))
    if (
        end != started
        or end - begin != timedelta(days=7)
        or query.get("paging_complete") is not False
        or payload.get("recent_orders_complete") is not False
        or not cash
        or not balances
    ):
        fail()
    orders = rows(payload.get("recent_orders_7d"), _ORDER_FIELDS)
    by_id = {}
    for order in orders:
        submitted = instant(order["submitted_at"])
        if not begin <= submitted <= end:
            fail()
        if order["order_digest"] in by_id and by_id[order["order_digest"]] != order:
            fail()
        by_id[order["order_digest"]] = order
    return {
        "schema_version": "paper_observation.v1",
        "account_scope": "PAPER",
        "account_id": None,
        "identity_verification": "source_binding_only",
        "snapshot_atomic": False,
        "observed_started_at": started.isoformat(),
        "observed_finished_at": finished.isoformat(),
        "cash": cash,
        "broker_reported_balances": balances,
        "positions": positions,
        "recent_orders_7d": list(by_id.values()),
        "known_non_terminal_orders_7d": [
            order for order in by_id.values() if order["status"] in WORKING_STATUSES
        ],
        "recent_orders_complete": False,
        "open_orders_complete": False,
        "orders_query_start": begin.isoformat(),
        "orders_query_end": end.isoformat(),
    }


def validate_stored(record, day):
    """Check the immutable normalized contract, without asserting source authenticity."""
    try:
        _validate_stored(record, day)
    except (
        ObservationError, TypeError, AttributeError, KeyError, ValueError,
        OverflowError, OSError,
    ):
        raise ObservationError("stored_record_invalid") from None
    return record


def _validate_stored(record, day):
    fixed = {
        "schema_version": "paper_observation.v1",
        "trading_date": str(day),
        "account_scope": "PAPER",
        "account_id": None,
        "identity_verification": "source_binding_only",
        "snapshot_atomic": False,
        "recent_orders_complete": False,
        "open_orders_complete": False,
    }
    time_fields = {
        "observed_started_at", "observed_finished_at", "orders_query_start",
        "orders_query_end", "scheduled_at", "job_started_at", "store_requested_at",
    }
    required = fixed.keys() | _ROW_FIELDS.keys() | time_fields | {"lateness_seconds", "timing"}
    if (
        not isinstance(record, dict)
        or record.keys() != required
        or any(type(record[k]) is not type(v) or record[k] != v for k, v in fixed.items())
    ):
        fail()
    for key, fields in _ROW_FIELDS.items():
        # Persisted rows must already be normalized: never drop unknown fields,
        # supply missing fields or silently repair a historical observation.
        if rows(record[key], fields) != record[key]:
            fail()
    if not record["cash"] or not record["broker_reported_balances"]:
        fail()
    times = {key: instant(record[key]) for key in time_fields}
    started, finished = times["observed_started_at"], times["observed_finished_at"]
    job, stored = times["job_started_at"], times["store_requested_at"]
    begin, end = times["orders_query_start"], times["orders_query_end"]
    scheduled = times["scheduled_at"]
    # A sanitize clock must have existed between job start and store request.
    # Keep its original five-minute age / thirty-second future tolerances;
    # replay time is irrelevant to this historical record.
    if (
        started > finished
        or job > stored
        or max(job, finished - timedelta(seconds=30))
        > min(stored, started + timedelta(minutes=5))
        or started.astimezone(NY).date() != day
        or job.astimezone(NY).date() != day
        or scheduled != Calendar().due(day)
        or scheduled > job
        or end != started
        or end - begin != timedelta(days=7)
    ):
        fail()
    orders = record["recent_orders_7d"]
    if (
        len({order["order_digest"] for order in orders}) != len(orders)
        or any(not begin <= instant(order["submitted_at"]) <= end for order in orders)
        or record["known_non_terminal_orders_7d"]
        != [order for order in orders if order["status"] in WORKING_STATUSES]
    ):
        fail()
    lateness = (started - scheduled).total_seconds()
    if (
        type(record["lateness_seconds"]) not in (int, float)
        or record["lateness_seconds"] != lateness
        or lateness < 0
        or record["timing"] != ("on_time" if lateness <= 300 else "late_snapshot")
    ):
        fail()


class Collector:
    def __init__(
        self, store, fetch, binding, calendar, start, clock=lambda: datetime.now(UTC)
    ):
        self.store, self.fetch, self.binding, self.calendar, self.start = (
            store,
            fetch,
            binding,
            calendar,
            start,
        )
        self.clock = clock

    def complete(self, day, record):
        validate_stored(record, day)
        self.store.create(
            "t0.json",
            {
                "snapshot": f"snapshots/US/{day}.json",
                "observed_at": record["observed_started_at"],
            },
        )
        self.store.create(
            f"health/days/{day}.json",
            {
                "trading_date": str(day),
                "status": record["timing"],
                "observed_at": record["observed_started_at"],
                "recent_orders_complete": False,
                "open_orders_complete": False,
            },
        )

    def run(self, now):
        self.store.create(f"health/heartbeat/{slot(now)}.json", {"at": now.isoformat()})
        gaps = []
        for day, due in self.calendar.expected(self.start, now):
            if self.store.get(f"health/days/{day}.json"):
                continue
            record = self.store.get(f"snapshots/US/{day}.json")
            if record:
                self.complete(day, record)
            else:
                gaps.append((day, due))
        if not gaps:
            return "no_due_gap"
        today = now.astimezone(NY).date()
        if self.store.get(f"health/stopped/{today}.json"):
            return "stopped_for_day"
        ordinary = [
            (day, due)
            for day, due in gaps
            if day == today and timedelta(0) <= now - due < timedelta(minutes=45)
        ]
        if ordinary:
            day, due = ordinary[0]
            claim = (
                f"health/attempts/{day}/{int((now - due).total_seconds() // 900)}.json"
            )
        else:
            if any(now - due < timedelta(minutes=60) for _, due in gaps):
                return "waiting_recovery_slot"
            for day, _ in gaps:
                if day < today:
                    self.store.create(
                        f"health/gaps/{day}.json",
                        {
                            "trading_date": str(day),
                            "cash": None,
                            "positions": None,
                            "status": "unrecoverable_point_in_time",
                        },
                    )
            if not any((now.date() - day).days <= 7 for day, _ in gaps):
                return "unrecoverable_gaps"
            claim = f"health/recovery/{now.date()}.json"
        if not self.store.create(claim, {"started_at": now.isoformat()}):
            return "slot_already_claimed"
        try:
            record = sanitize(self.fetch(), self.binding, self.clock())
        except ObservationError as exc:
            code = str(exc)
            self.store.create(
                f"health/failures/{today}/{slot(now)}.json", {"reason": code}
            )
            if code in {"identity_failure", "schema_invalid", "http_401", "http_403"}:
                self.store.create(f"health/stopped/{today}.json", {"reason": code})
            return code
        observed = instant(record["observed_started_at"])
        if observed.astimezone(NY).date() != today:
            self.store.create(
                f"health/stopped/{today}.json", {"reason": "schema_invalid"}
            )
            return "schema_invalid"
        unique = observed.strftime("%Y%m%dT%H%M%S%fZ")
        self.store.create(f"observations/{unique}.json", record)
        for day, due in gaps:
            if day == today:
                lateness = (observed - due).total_seconds()
                if lateness < 0:
                    raise ObservationError("schema_invalid")
                canonical = {
                    **record,
                    "trading_date": str(day),
                    "scheduled_at": due.isoformat(),
                    "job_started_at": now.isoformat(),
                    "store_requested_at": self.clock().isoformat(),
                    "lateness_seconds": lateness,
                    "timing": "on_time" if lateness <= 300 else "late_snapshot",
                }
                key = f"snapshots/US/{day}.json"
                self.store.create(key, canonical)
                self.complete(day, self.store.get(key))
            if 0 <= (now.date() - day).days <= 7:
                orders = [
                    o
                    for o in record["recent_orders_7d"]
                    if instant(o["submitted_at"]).astimezone(NY).date() == day
                ]
                self.store.create(
                    f"orders/{day}/{unique}.json",
                    {
                        "trading_date": str(day),
                        "fetched_at": record["observed_finished_at"],
                        "query_start": record["orders_query_start"],
                        "query_end": record["orders_query_end"],
                        "orders": orders,
                        "complete": False,
                        "historical_state_reconstructed": False,
                    },
                )
        return "stored"


class Watchdog:
    def __init__(self, store, send, calendar, start, activation, verification=False):
        self.store, self.send, self.calendar, self.start, self.activation = (
            store,
            send,
            calendar,
            start,
            activation,
        )
        self.verification = verification
        if verification:
            self.send = lambda message: send("[verification] " + message)

    def notify(self, incident, active, now):
        prefix = "verification/" if self.verification else ""
        root = f"alerts/{prefix}{incident}"
        if active and not self.store.get(f"{root}/ack.json"):
            claim = f"{root}/claims/{slot(now)}.json"
            if self.store.create(claim, {"at": now.isoformat()}) and self.send(
                f"PAPER observation alert: {incident}"
            ):
                self.store.create(
                    f"{root}/ack.json",
                    {
                        "at": now.isoformat(),
                        "sender_ack": True,
                        "receiver_receipt": None,
                    },
                )
        elif (
            not active
            and self.store.get(f"{root}/ack.json")
            and not self.store.get(f"{root}/recovered.json")
        ):
            if self.store.create(
                f"{root}/recover-claims/{slot(now)}.json", {"at": now.isoformat()}
            ) and self.send(f"PAPER observation recovery: {incident}"):
                self.store.create(f"{root}/recovered.json", {"at": now.isoformat()})

    def run(self, now):
        if now < self.activation:
            return "before_activation"
        try:
            self.store.create(
                f"alerts/watch-heartbeat/{slot(now)}.json", {"at": now.isoformat()}
            )
            alive = any(
                beat and now - timedelta(minutes=45) <= instant(beat["at"]) <= now
                for beat in (
                    self.store.get(
                        f"health/heartbeat/{slot(now - timedelta(minutes=i * 15))}.json"
                    )
                    for i in range(4)
                )
            )
            self.notify(
                f"{now.date()}/collector_heartbeat",
                not alive and now - self.activation >= timedelta(minutes=45),
                now,
            )
            for day, due in self.calendar.expected(self.start, now):
                stopped = self.store.get(f"health/stopped/{day}.json")
                self.notify(f"{day}/permission_identity_schema", bool(stopped), now)
                if now < max(due + timedelta(minutes=60), self.activation):
                    continue
                marker = self.store.get(f"health/days/{day}.json")
                self.notify(f"{day}/missing_snapshot", not marker, now)
                self.notify(
                    f"{day}/late_snapshot",
                    bool(marker and marker.get("status") == "late_snapshot"),
                    now,
                )
            remaining = (self.calendar.end - now.date()).days
            for threshold in (90, 30, 7):
                self.notify(
                    f"calendar/coverage_{threshold}", remaining <= threshold, now
                )
        except ObservationError as exc:
            if str(exc) == "calendar_unavailable":
                try:
                    self.notify(f"{now.date()}/calendar_unavailable", True, now)
                    return "calendar_unavailable"
                except ObservationError:
                    pass
            # Storage failure must not depend on storage-based deduplication.
            code = "storage_unavailable"
            self.send(
                f"PAPER observation alert: {now.date()}/{code}; persistent dedup unavailable"
            )
            return code
        return "checked"
