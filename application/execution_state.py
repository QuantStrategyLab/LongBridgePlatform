"""Execution marker storage for duplicate-run suppression."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_EXECUTION_STATE_DIR = "/tmp/longbridge_execution_state"
DEFAULT_EXECUTION_STATE_NAMESPACE = "execution_markers"


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _env_bool(value: object, *, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    text = str(uri or "").strip()
    if not text.startswith("gs://"):
        raise ValueError(f"gcs uri must start with gs://, got: {uri!r}")
    remainder = text[5:]
    bucket, _, prefix = remainder.partition("/")
    if not bucket:
        raise ValueError(f"gcs uri must include a bucket, got: {uri!r}")
    return bucket, prefix.strip("/")


def _clean_key_part(value: object, *, fallback: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._=-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-.")
    return text or fallback


def _clean_relative_key(key: str) -> str:
    parts = [
        _clean_key_part(part, fallback="unknown")
        for part in str(key or "").replace("\\", "/").split("/")
        if str(part or "").strip()
    ]
    return "/".join(parts) or "unknown"


def build_execution_marker_key(
    *,
    platform: str,
    strategy_profile: str,
    account_scope: str,
    execution_mode: str,
    signal_date: object,
    effective_date: object,
    execution_timing_contract: object = None,
) -> str:
    """Build a stable marker key for one strategy signal execution."""

    signal = _first_non_empty(signal_date)
    effective = _first_non_empty(effective_date)
    if not signal and not effective:
        return ""
    return "/".join(
        (
            "v1",
            _clean_key_part(platform, fallback="platform"),
            _clean_key_part(account_scope, fallback="account"),
            _clean_key_part(strategy_profile, fallback="strategy"),
            _clean_key_part(execution_mode, fallback="mode"),
            _clean_key_part(signal or "no-signal-date", fallback="signal"),
            _clean_key_part(effective or "no-effective-date", fallback="effective"),
            _clean_key_part(execution_timing_contract or "no-contract", fallback="contract"),
        )
    )


@dataclass(frozen=True)
class ExecutionMarkerStore:
    local_dir: str | Path | None = DEFAULT_EXECUTION_STATE_DIR
    gcs_prefix_uri: str | None = None
    gcp_project_id: str | None = None
    namespace: str = DEFAULT_EXECUTION_STATE_NAMESPACE
    client_factory: Any = None
    prior_report_scan_limit: int = 100

    def has_marker(self, marker_key: str) -> bool:
        if not str(marker_key or "").strip():
            return False
        if self.gcs_prefix_uri and self._gcs_blob(marker_key).exists():
            return True
        if self.local_dir and self._local_path(marker_key).exists():
            return True
        return False

    def record_marker(
        self,
        marker_key: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not str(marker_key or "").strip():
            return
        payload = {
            "schema_version": "longbridge_execution_marker.v1",
            "marker_key": str(marker_key),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "metadata": dict(metadata or {}),
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if self.gcs_prefix_uri:
            self._gcs_blob(marker_key).upload_from_string(
                encoded,
                content_type="application/json",
            )
            return
        if self.local_dir:
            path = self._local_path(marker_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded, encoding="utf-8")

    def has_prior_execution_report(
        self,
        *,
        platform: str,
        strategy_profile: str,
        account_scope: str,
        signal_date: object,
        effective_date: object,
        dry_run_only: bool,
    ) -> bool:
        if not self.gcs_prefix_uri:
            return False
        signal = _first_non_empty(signal_date)
        effective = _first_non_empty(effective_date)
        if not signal and not effective:
            return False
        bucket_name, prefix = _parse_gcs_uri(str(self.gcs_prefix_uri or ""))
        object_prefix = "/".join(
            part.strip("/")
            for part in (
                prefix,
                _runtime_report_segment(platform),
                _runtime_report_segment(strategy_profile),
                _runtime_report_segment(account_scope),
            )
            if part and part.strip("/")
        )
        client = self._gcs_client()
        scanned = 0
        for blob in client.list_blobs(bucket_name, prefix=object_prefix):
            name = str(getattr(blob, "name", "") or "")
            if not name.endswith(".json"):
                continue
            scanned += 1
            if scanned > max(1, int(self.prior_report_scan_limit or 1)):
                break
            try:
                payload = json.loads(blob.download_as_text())
            except Exception:
                continue
            if _report_matches_execution(
                payload,
                platform=platform,
                strategy_profile=strategy_profile,
                account_scope=account_scope,
                signal_date=signal,
                effective_date=effective,
                dry_run_only=dry_run_only,
            ):
                return True
        return False

    def _local_path(self, marker_key: str) -> Path:
        root = Path(self.local_dir or tempfile.gettempdir()).expanduser()
        return root / self.namespace / f"{_clean_relative_key(marker_key)}.json"

    def _gcs_blob(self, marker_key: str):
        bucket_name, prefix = _parse_gcs_uri(str(self.gcs_prefix_uri or ""))
        object_name = "/".join(
            part.strip("/")
            for part in (
                prefix,
                self.namespace,
                f"{_clean_relative_key(marker_key)}.json",
            )
            if part and part.strip("/")
        )
        if self.client_factory is None:
            try:
                from google.cloud import storage  # type: ignore
            except ImportError as exc:
                raise RuntimeError("google-cloud-storage is required for GCS execution markers") from exc
            client_factory = storage.Client
        else:
            client_factory = self.client_factory
        client = client_factory(project=self.gcp_project_id) if self.gcp_project_id else client_factory()
        return client.bucket(bucket_name).blob(object_name)

    def _gcs_client(self):
        if self.client_factory is None:
            try:
                from google.cloud import storage  # type: ignore
            except ImportError as exc:
                raise RuntimeError("google-cloud-storage is required for GCS execution markers") from exc
            client_factory = storage.Client
        else:
            client_factory = self.client_factory
        return client_factory(project=self.gcp_project_id) if self.gcp_project_id else client_factory()


def build_execution_marker_store_from_env(
    *,
    env_reader: Callable[[str, str | None], str | None],
    gcp_project_id: str | None = None,
    client_factory: Any = None,
) -> ExecutionMarkerStore:
    explicit_gcs_uri = env_reader("LONGBRIDGE_EXECUTION_STATE_GCS_URI", None)
    report_gcs_uri = env_reader("EXECUTION_REPORT_GCS_URI", None)
    local_dir = env_reader("LONGBRIDGE_EXECUTION_STATE_DIR", None)
    return ExecutionMarkerStore(
        local_dir=local_dir or DEFAULT_EXECUTION_STATE_DIR,
        gcs_prefix_uri=explicit_gcs_uri or report_gcs_uri,
        gcp_project_id=gcp_project_id,
        client_factory=client_factory,
    )


def resolve_execution_dedup_enabled(
    *,
    env_reader: Callable[[str, str | None], str | None],
    dry_run_only: bool,
) -> bool:
    raw_value = env_reader("LONGBRIDGE_EXECUTION_DEDUP_ENABLED", None)
    return _env_bool(raw_value, default=bool(dry_run_only))


def _runtime_report_segment(value: object) -> str:
    text = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in text)
    return safe or "unknown"


def _optional_str(value: object) -> str:
    return str(value or "").strip()


def _report_matches_execution(
    payload: Mapping[str, Any],
    *,
    platform: str,
    strategy_profile: str,
    account_scope: str,
    signal_date: str,
    effective_date: str,
    dry_run_only: bool,
) -> bool:
    report = dict(payload or {})
    if _optional_str(report.get("platform")).lower() != _optional_str(platform).lower():
        return False
    if _optional_str(report.get("strategy_profile")).lower() != _optional_str(strategy_profile).lower():
        return False
    if _optional_str(report.get("account_scope")).lower() != _optional_str(account_scope).lower():
        return False
    if bool(report.get("dry_run")) != bool(dry_run_only):
        return False
    summary = dict(report.get("summary") or {})
    if signal_date and _optional_str(summary.get("signal_date")) != signal_date:
        return False
    if effective_date and _optional_str(summary.get("effective_date")) != effective_date:
        return False
    return (
        bool(summary.get("action_done"))
        or int(float(summary.get("orders_previewed_count") or 0)) > 0
        or int(float(summary.get("order_events_count") or 0)) > 0
    )
