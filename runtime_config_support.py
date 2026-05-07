from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from quant_platform_kit.common.runtime_config import (
    resolve_bool_value,
    resolve_float_env,
    resolve_optional_float_env,
    resolve_quantity_step_env,
    resolve_strategy_runtime_path_settings,
)
from strategy_registry import (
    LONGBRIDGE_PLATFORM,
    resolve_strategy_definition,
    resolve_strategy_metadata,
)
from us_equity_strategies import get_strategy_catalog

DEFAULT_ACCOUNT_REGION = "DEFAULT"
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
    quantity_step: float = 1.0
    min_order_notional: float = 0.0
    debug_position_snapshot: bool = False
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
    runtime_paths = resolve_strategy_runtime_path_settings(
        strategy_catalog=get_strategy_catalog(),
        strategy_definition=strategy_definition,
        strategy_metadata=strategy_metadata,
        platform_env_prefix="LONGBRIDGE",
        env=os.environ,
        repo_root=Path(__file__).resolve().parent,
    )
    return PlatformRuntimeSettings(
        project_id=project_id_resolver(),
        secret_name=os.getenv("LONGPORT_SECRET_NAME", DEFAULT_LONGPORT_SECRET_NAME),
        account_prefix=account_prefix,
        strategy_profile=runtime_paths.strategy_profile,
        strategy_display_name=runtime_paths.strategy_display_name,
        strategy_domain=runtime_paths.strategy_domain,
        account_region=infer_account_region(
            os.getenv("ACCOUNT_REGION"),
            account_prefix=account_prefix,
        ),
        notify_lang=os.getenv("NOTIFY_LANG", "en"),
        tg_token=os.getenv("TELEGRAM_TOKEN"),
        tg_chat_id=os.getenv("GLOBAL_TELEGRAM_CHAT_ID"),
        dry_run_only=resolve_bool_value(os.getenv("LONGBRIDGE_DRY_RUN_ONLY")),
        quantity_step=resolve_quantity_step_env(
            os.environ,
            step_env="LONGBRIDGE_ORDER_QUANTITY_STEP",
            fractional_env="LONGBRIDGE_FRACTIONAL_SHARES_ENABLED",
            fractional_default=False,
            fractional_step=0.0001,
        ),
        min_order_notional=resolve_float_env(
            os.environ,
            "LONGBRIDGE_MIN_ORDER_NOTIONAL_USD",
            default=1.0,
        ),
        debug_position_snapshot=resolve_bool_value(os.getenv("LONGBRIDGE_DEBUG_POSITION_SNAPSHOT")),
        income_threshold_usd=resolve_optional_float_env(os.environ, "INCOME_THRESHOLD_USD"),
        qqqi_income_ratio=_qqqi_income_ratio_env(),
        feature_snapshot_path=runtime_paths.feature_snapshot_path,
        feature_snapshot_manifest_path=runtime_paths.feature_snapshot_manifest_path,
        strategy_config_path=runtime_paths.strategy_config_path,
        strategy_config_source=runtime_paths.strategy_config_source,
    )


def _normalize_region(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    return value.upper()


def _qqqi_income_ratio_env() -> float | None:
    value = resolve_optional_float_env(os.environ, "QQQI_INCOME_RATIO")
    if value is not None and not (0.0 <= value <= 1.0):
        raise ValueError(f"QQQI_INCOME_RATIO must be in [0,1], got {value}")
    return value
