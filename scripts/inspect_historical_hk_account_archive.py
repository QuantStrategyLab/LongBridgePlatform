"""Read-only historical HK archive inspection with an injected archive client.

This module has no CLI, credential provider, client factory, Scheduler operation,
broker request, or QRS publish path. A caller must separately establish authority
for its existing client and the trusted expected source binding. Inspection is
historical evidence only, never a current-health or original-request receipt.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
from typing import Any

from scripts import record_daily_account_snapshot as snapshots

MAX_INSPECTION_SECONDS = 180.0
MAX_TOTAL_OBJECTS = snapshots.MAX_OBJECTS_PER_DAY
MAX_TOTAL_BYTES = MAX_TOTAL_OBJECTS * snapshots.MAX_OBJECT_BYTES
MAX_WINDOW_DURATION = snapshots.ARCHIVE_INSPECTION_MAX_DURATION
_GENERATION = re.compile(r"^[1-9][0-9]{0,19}$")
_ARCHIVE_FILENAME = re.compile(
    r"^(?:[01][0-9]|2[0-3])[0-5][0-9][0-5][0-9][0-9]{6}Z\.json$"
)
_BUCKET = "qsl-runtime-logs-shared"
_PREFIX_PATH = "longbridge/account_snapshots"


@dataclass(frozen=True)
class _HistoricalConfig:
    source_binding_id: str
    bucket: str = _BUCKET
    prefix_path: str = _PREFIX_PATH
    target_id: str = "hk"
    expected_scope: str = "HK"


def _check_deadline(deadline: float, monotonic: Callable[[], float]) -> float:
    try:
        remaining = deadline - monotonic()
        valid = math.isfinite(remaining) and remaining > 0
    except Exception:
        valid = False
    if not valid:
        raise snapshots._Rejected("historical_inspection_timeout")
    return remaining


def _historical_window(
    window_start: str,
    window_end: str,
    inspected_at: datetime,
) -> tuple[datetime, datetime, datetime]:
    try:
        if any(
            not isinstance(value, str) or len(value) > 64
            for value in (window_start, window_end)
        ):
            raise ValueError
        start, end = snapshots._aware(window_start), snapshots._aware(window_end)
        now = snapshots._utc_now(lambda: inspected_at)
        if start > end or end > now or end - start > MAX_WINDOW_DURATION:
            raise ValueError
    except Exception:
        raise snapshots._Rejected("historical_time_invalid") from None
    return start, end, now


def _historical_candidates(
    client: Any,
    config: _HistoricalConfig,
    dates: set[Any],
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> list[dict[str, Any]]:
    """Complete only the fixed source/date prefixes, or reject unknown coverage."""
    candidates: list[dict[str, Any]] = []
    names: set[str] = set()
    total_bytes = 0
    for day in sorted(dates):
        remaining = _check_deadline(deadline, monotonic)
        prefix = (
            f"{config.prefix_path}/hk/{config.source_binding_id}/{day.isoformat()}/"
        )
        try:
            iterator = client.list_blobs(
                config.bucket,
                prefix=prefix,
                max_results=snapshots.MAX_OBJECTS_PER_DAY + 1,
                page_size=snapshots.MAX_OBJECTS_PER_DAY + 1,
                timeout=min(snapshots.GCS_TIMEOUT_SECONDS, remaining),
                retry=None,
                fields="items(name,generation,size),nextPageToken",
            )
            # Native SDK pages is a single-use property; access it exactly once.
            pages = getattr(iterator, "pages", None)
            if pages is None or not hasattr(iterator, "next_page_token"):
                raise snapshots._Rejected("historical_listing_invalid")
            page = list(
                islice(next(iter(pages), ()), snapshots.MAX_OBJECTS_PER_DAY + 1)
            )
            token = iterator.next_page_token
        except snapshots._Rejected:
            raise
        except Exception:
            raise snapshots._Rejected("historical_listing_failed") from None
        _check_deadline(deadline, monotonic)
        if token is not None or len(page) > snapshots.MAX_OBJECTS_PER_DAY:
            raise snapshots._Rejected("historical_listing_truncated")
        for blob in page:
            try:
                name, generation, size = blob.name, blob.generation, blob.size
                valid_generation = (
                    type(generation) in {int, str}
                    and _GENERATION.fullmatch(str(generation)) is not None
                    and int(generation) <= 2**64 - 1
                )
                if (
                    not isinstance(name, str)
                    or not name.startswith(prefix)
                    or _ARCHIVE_FILENAME.fullmatch(name[len(prefix) :]) is None
                    or name in names
                    or not valid_generation
                    or type(size) is not int
                    or not 0 <= size <= snapshots.MAX_OBJECT_BYTES
                ):
                    raise ValueError
            except Exception:
                raise snapshots._Rejected("historical_listing_invalid") from None
            names.add(name)
            total_bytes += size
            candidates.append(
                {"name": name, "generation": int(generation), "size": size}
            )
            if len(candidates) > MAX_TOTAL_OBJECTS or total_bytes > MAX_TOTAL_BYTES:
                raise snapshots._Rejected("historical_listing_truncated")
    return candidates


def _validate_historical_payload(
    payload: Mapping[str, Any],
    config: _HistoricalConfig,
    candidate: Mapping[str, Any],
    now: datetime,
) -> tuple[datetime, datetime]:
    try:
        started = snapshots._aware(payload.get("observed_started_at"))
        finished = snapshots._aware(payload.get("observed_finished_at"))
        if finished > now or started > finished:
            raise ValueError
        binding = payload.get("source_binding")
        if not isinstance(binding, Mapping) or set(binding) != {"kind", "status", "id"}:
            raise ValueError
        for field, expected in (
            ("broker_reported_balances", snapshots._BALANCE_FIELDS),
            ("cash", snapshots._CASH_FIELDS),
        ):
            rows = payload.get(field)
            if not isinstance(rows, list) or any(
                not isinstance(row, Mapping) or set(row) != set(expected)
                for row in rows
            ):
                raise ValueError
        # Reuse the unchanged archive contract at the object's historical end.
        # Its original 15-minute observation duration/freshness rule still applies.
        snapshots._validate_history_object(
            payload, config, candidate, started, finished
        )
    except Exception:
        raise snapshots._Rejected("historical_object_invalid") from None
    return started, finished


def inspect_historical_hk_account_archive(
    *,
    archive_client: Any,
    expected_source_binding_id: str,
    window_start: str,
    window_end: str,
    inspected_at: datetime,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Inspect existing HK objects in an explicit <=5-minute historical window.

    The binding is a caller assertion, not proof of broker identity or client
    authorization. No target, bucket, path, endpoint or credential is inferred
    from an object. Valid objects outside the window remain non-matches; any
    malformed object or incomplete read rejects the entire inspection.
    """
    if (
        not isinstance(expected_source_binding_id, str)
        or snapshots._BINDING_ID.fullmatch(expected_source_binding_id) is None
    ):
        raise snapshots._Rejected("historical_config_invalid")
    start, end, now = _historical_window(window_start, window_end, inspected_at)
    config = _HistoricalConfig(expected_source_binding_id)
    try:
        deadline = monotonic() + MAX_INSPECTION_SECONDS
    except Exception:
        raise snapshots._Rejected("historical_inspection_timeout") from None
    candidates = _historical_candidates(
        archive_client,
        config,
        {start.date(), end.date()},
        deadline=deadline,
        monotonic=monotonic,
    )
    observations = []
    for candidate in candidates:
        _check_deadline(deadline, monotonic)
        try:
            payload, raw = snapshots._read_candidate(
                archive_client,
                config,
                candidate,
                deadline=deadline,
                monotonic=monotonic,
            )
        except snapshots._Rejected as rejected:
            if rejected.category == "observation_timeout":
                raise snapshots._Rejected("historical_inspection_timeout") from None
            raise
        except Exception:
            raise snapshots._Rejected("historical_read_failed") from None
        _check_deadline(deadline, monotonic)
        started, finished = _validate_historical_payload(
            payload, config, candidate, now
        )
        same_window = start <= started <= finished <= end
        if same_window:
            # Historical acceptance does not bypass or modify the live validator.
            snapshots._validate_history_object(payload, config, candidate, start, end)
        observations.append(
            {
                "historical_window_match": same_window,
                "currently_stale": not snapshots._is_fresh(payload, now),
                "observed_started_at": started.isoformat(),
                "observed_finished_at": finished.isoformat(),
                "object_generation": candidate["generation"],
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "balance_currency_rows": len(payload["broker_reported_balances"]),
                "cash_currency_rows": len(payload["cash"]),
            }
        )
    _check_deadline(deadline, monotonic)
    matches = sum(item["historical_window_match"] for item in observations)
    return {
        "evidence_kind": "historical_hk_archive_inspection",
        "target": "hk",
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "inspected_at": now.isoformat(),
        "scanned_object_count": len(observations),
        "historical_window_match_count": matches,
        "same_window_archive_unique": matches == 1,
        "current_health_confirmed": False,
        "request_terminal_confirmed": False,
        "receiver_ack_confirmed": False,
        "native_identity_confirmed": False,
        "observations": observations,
    }
