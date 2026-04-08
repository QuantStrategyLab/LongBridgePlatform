from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from strategy_registry import (
    DEFAULT_STRATEGY_PROFILE as PLATFORM_DEFAULT_STRATEGY_PROFILE,
    LONGBRIDGE_PLATFORM,
    resolve_strategy_definition,
    resolve_strategy_metadata,
)

DEFAULT_ACCOUNT_REGION = "DEFAULT"
DEFAULT_STRATEGY_PROFILE = PLATFORM_DEFAULT_STRATEGY_PROFILE
DEFAULT_LONGPORT_SECRET_NAME = "longport_token_hk"


@dataclass(frozen=True)
class PlatformRuntimeSettings:
    project_id: str | None
    secret_name: str
    account_prefix: str
    strategy_profile: str
    strategy_display_name: str
    strategy_domain: str
    account_region: str
    notify_lang: str
    tg_token: str | None
    tg_chat_id: str | None
    dry_run_only: bool
    feature_snapshot_path: str | None = None
    feature_snapshot_manifest_path: str | None = None
    strategy_config_path: str | None = None
    strategy_config_source: str | None = None


def resolve_strategy_profile(raw_value: str | None) -> str:
    return resolve_strategy_definition(
        raw_value,
        platform_id=LONGBRIDGE_PLATFORM,
    ).profile


def infer_account_region(
    raw_value: str | None,
    *,
    account_prefix: str,
) -> str:
    for candidate in (
        raw_value,
        account_prefix,
    ):
        normalized = _normalize_region(candidate)
        if normalized is not None:
            return normalized
    return DEFAULT_ACCOUNT_REGION


def load_platform_runtime_settings(
    *,
    project_id_resolver: Callable[[], str | None],
) -> PlatformRuntimeSettings:
    account_prefix = os.getenv("ACCOUNT_PREFIX", "DEFAULT")
    strategy_definition = resolve_strategy_definition(
        os.getenv("STRATEGY_PROFILE"),
        platform_id=LONGBRIDGE_PLATFORM,
    )
    strategy_metadata = resolve_strategy_metadata(
        strategy_definition.profile,
        platform_id=LONGBRIDGE_PLATFORM,
    )
    strategy_config_path = _first_non_empty(
        os.getenv("LONGBRIDGE_STRATEGY_CONFIG_PATH"),
        os.getenv("STRATEGY_CONFIG_PATH"),
    )
    return PlatformRuntimeSettings(
        project_id=project_id_resolver(),
        secret_name=os.getenv("LONGPORT_SECRET_NAME", DEFAULT_LONGPORT_SECRET_NAME),
        account_prefix=account_prefix,
        strategy_profile=strategy_definition.profile,
        strategy_display_name=strategy_metadata.display_name,
        strategy_domain=strategy_definition.domain,
        account_region=infer_account_region(
            os.getenv("ACCOUNT_REGION"),
            account_prefix=account_prefix,
        ),
        notify_lang=os.getenv("NOTIFY_LANG", "en"),
        tg_token=os.getenv("TELEGRAM_TOKEN"),
        tg_chat_id=os.getenv("GLOBAL_TELEGRAM_CHAT_ID"),
        dry_run_only=_resolve_bool_env("LONGBRIDGE_DRY_RUN_ONLY"),
        feature_snapshot_path=_first_non_empty(
            os.getenv("LONGBRIDGE_FEATURE_SNAPSHOT_PATH"),
            os.getenv("FEATURE_SNAPSHOT_PATH"),
        ),
        feature_snapshot_manifest_path=_first_non_empty(
            os.getenv("LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH"),
            os.getenv("FEATURE_SNAPSHOT_MANIFEST_PATH"),
        ),
        strategy_config_path=strategy_config_path,
        strategy_config_source="env" if strategy_config_path else None,
    )


def _normalize_region(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    return value.upper()


def _resolve_bool_env(name: str) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return False
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None
