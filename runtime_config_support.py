from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from quant_platform_kit.common.strategies import derive_strategy_artifact_paths
from strategy_registry import (
    DEFAULT_STRATEGY_PROFILE as PLATFORM_DEFAULT_STRATEGY_PROFILE,
    LONGBRIDGE_PLATFORM,
    resolve_strategy_definition,
    resolve_strategy_metadata,
)
from us_equity_strategies import get_strategy_catalog

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
    income_threshold_usd: float | None = None
    qqqi_income_ratio: float | None = None
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
    artifact_root = _first_non_empty(
        os.getenv("LONGBRIDGE_STRATEGY_ARTIFACT_ROOT"),
        os.getenv("STRATEGY_ARTIFACT_ROOT"),
    )
    derived_artifact_paths = derive_strategy_artifact_paths(
        get_strategy_catalog(),
        strategy_definition.profile,
        artifact_root=artifact_root,
        repo_root=Path(__file__).resolve().parent,
    )
    strategy_config_path, strategy_config_source = resolve_strategy_config_path(
        explicit_path=_first_non_empty(
            os.getenv("LONGBRIDGE_STRATEGY_CONFIG_PATH"),
            os.getenv("STRATEGY_CONFIG_PATH"),
        ),
        bundled_path=(
            str(derived_artifact_paths.bundled_config_path)
            if derived_artifact_paths.bundled_config_path is not None
            else None
        ),
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
        income_threshold_usd=_optional_float_env("INCOME_THRESHOLD_USD"),
        qqqi_income_ratio=_qqqi_income_ratio_env(),
        feature_snapshot_path=_first_non_empty(
            os.getenv("LONGBRIDGE_FEATURE_SNAPSHOT_PATH"),
            os.getenv("FEATURE_SNAPSHOT_PATH"),
            str(derived_artifact_paths.feature_snapshot_path)
            if derived_artifact_paths.feature_snapshot_path is not None
            else None,
        ),
        feature_snapshot_manifest_path=_first_non_empty(
            os.getenv("LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH"),
            os.getenv("FEATURE_SNAPSHOT_MANIFEST_PATH"),
            str(derived_artifact_paths.feature_snapshot_manifest_path)
            if derived_artifact_paths.feature_snapshot_manifest_path is not None
            else None,
        ),
        strategy_config_path=strategy_config_path,
        strategy_config_source=strategy_config_source,
    )


def resolve_strategy_config_path(
    *,
    explicit_path: str | None,
    bundled_path: str | None,
) -> tuple[str | None, str | None]:
    path = _first_non_empty(explicit_path)
    if path is not None:
        return path, "env"

    bundled = _first_non_empty(bundled_path)
    if bundled is not None and Path(bundled).exists():
        return bundled, "bundled_canonical_default"
    return None, None


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


def _optional_float_env(name: str) -> float | None:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return None
    return float(raw_value)


def _qqqi_income_ratio_env() -> float | None:
    value = _optional_float_env("QQQI_INCOME_RATIO")
    if value is not None and not (0.0 <= value <= 1.0):
        raise ValueError(f"QQQI_INCOME_RATIO must be in [0,1], got {value}")
    return value


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None
