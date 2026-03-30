from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from strategy_registry import (
    LONGBRIDGE_PLATFORM,
    resolve_strategy_definition,
)

DEFAULT_ACCOUNT_REGION = "DEFAULT"


@dataclass(frozen=True)
class PlatformRuntimeSettings:
    project_id: str | None
    secret_name: str
    account_prefix: str
    service_name: str
    strategy_profile: str
    strategy_domain: str
    account_region: str
    notify_lang: str
    tg_token: str | None
    tg_chat_id: str | None


def resolve_strategy_profile(raw_value: str | None) -> str:
    return resolve_strategy_definition(
        raw_value,
        platform_id=LONGBRIDGE_PLATFORM,
    ).profile


def infer_account_region(
    raw_value: str | None,
    *,
    account_prefix: str,
    service_name: str,
) -> str:
    for candidate in (
        raw_value,
        account_prefix,
        _infer_region_from_service_name(service_name),
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
    service_name = os.getenv("SERVICE_NAME", "longbridge-quant-semiconductor-rotation-income")
    strategy_definition = resolve_strategy_definition(
        os.getenv("STRATEGY_PROFILE"),
        platform_id=LONGBRIDGE_PLATFORM,
    )
    return PlatformRuntimeSettings(
        project_id=project_id_resolver(),
        secret_name=os.getenv("LONGPORT_SECRET_NAME", "longport_token"),
        account_prefix=account_prefix,
        service_name=service_name,
        strategy_profile=strategy_definition.profile,
        strategy_domain=strategy_definition.domain,
        account_region=infer_account_region(
            os.getenv("ACCOUNT_REGION"),
            account_prefix=account_prefix,
            service_name=service_name,
        ),
        notify_lang=os.getenv("NOTIFY_LANG", "en"),
        tg_token=os.getenv("TELEGRAM_TOKEN"),
        tg_chat_id=os.getenv("GLOBAL_TELEGRAM_CHAT_ID"),
    )


def _normalize_region(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    return value.upper()


def _infer_region_from_service_name(service_name: str) -> str | None:
    name = str(service_name).strip().lower()
    if name.endswith("-hk"):
        return "HK"
    if name.endswith("-sg"):
        return "SG"
    return None
