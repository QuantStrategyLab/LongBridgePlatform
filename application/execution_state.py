"""Execution marker storage for duplicate-run suppression."""

from __future__ import annotations

from typing import Any

from quant_platform_kit.common.execution_state import (
    ExecutionMarkerStore,
    build_execution_marker_key,
)
from quant_platform_kit.common.execution_state import (
    _report_matches_execution,
    build_execution_marker_store_from_env as _build_execution_marker_store_from_env,
    resolve_execution_dedup_enabled as _resolve_execution_dedup_enabled,
)

__all__ = [
    "ExecutionMarkerStore",
    "build_execution_marker_key",
    "build_execution_marker_store_from_env",
    "resolve_execution_dedup_enabled",
]


def build_execution_marker_store_from_env(
    *,
    env_reader,
    gcp_project_id: str | None = None,
    client_factory: Any = None,
) -> ExecutionMarkerStore:
    return _build_execution_marker_store_from_env(
        platform_env_prefix="LONGBRIDGE",
        env_reader=env_reader,
        gcp_project_id=gcp_project_id,
        client_factory=client_factory,
    )


def resolve_execution_dedup_enabled(
    *,
    env_reader,
    dry_run_only: bool,
    account_scope: object = None,
) -> bool:
    return _resolve_execution_dedup_enabled(
        platform_env_prefix="LONGBRIDGE",
        env_reader=env_reader,
        dry_run_only=dry_run_only,
        account_scope=account_scope,
    )
