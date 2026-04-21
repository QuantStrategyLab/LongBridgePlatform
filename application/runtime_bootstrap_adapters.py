"""Builder helpers for LongBridge runtime bootstrap assembly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True)
class LongBridgeRuntimeBootstrap:
    project_id: str | None
    secret_name: str
    token_refresh_threshold_days: int
    fetch_token_from_secret_fn: Callable[[str | None, str], str]
    refresh_token_if_needed_fn: Callable[..., str]
    build_contexts_fn: Callable[[str, str, str], tuple[Any, Any]]
    calculate_strategy_indicators_fn: Callable[[Any], Any]
    env_reader: Callable[[str, str], str | None] = os.getenv
    app_key_env_name: str = "LONGPORT_APP_KEY"
    app_secret_env_name: str = "LONGPORT_APP_SECRET"

    def _read_app_credentials(self) -> tuple[str, str]:
        return (
            str(self.env_reader(self.app_key_env_name, "") or ""),
            str(self.env_reader(self.app_secret_env_name, "") or ""),
        )

    def __call__(self) -> tuple[Any, Any, Any]:
        app_key, app_secret = self._read_app_credentials()
        token = self.refresh_token_if_needed_fn(
            self.fetch_token_from_secret_fn(self.project_id, self.secret_name),
            project_id=self.project_id,
            secret_name=self.secret_name,
            app_key=app_key,
            app_secret=app_secret,
            refresh_threshold_days=self.token_refresh_threshold_days,
        )
        quote_context, trade_context = self.build_contexts_fn(app_key, app_secret, token)
        indicators = self.calculate_strategy_indicators_fn(quote_context)
        if indicators is None:
            raise Exception("Quote data missing or API limited; cannot compute indicators")
        return quote_context, trade_context, indicators


def build_runtime_bootstrap(
    *,
    project_id: str | None,
    secret_name: str,
    token_refresh_threshold_days: int,
    fetch_token_from_secret_fn: Callable[[str | None, str], str],
    refresh_token_if_needed_fn: Callable[..., str],
    build_contexts_fn: Callable[[str, str, str], tuple[Any, Any]],
    calculate_strategy_indicators_fn: Callable[[Any], Any],
    env_reader: Callable[[str, str], str | None] = os.getenv,
) -> LongBridgeRuntimeBootstrap:
    return LongBridgeRuntimeBootstrap(
        project_id=project_id,
        secret_name=secret_name,
        token_refresh_threshold_days=int(token_refresh_threshold_days),
        fetch_token_from_secret_fn=fetch_token_from_secret_fn,
        refresh_token_if_needed_fn=refresh_token_if_needed_fn,
        build_contexts_fn=build_contexts_fn,
        calculate_strategy_indicators_fn=calculate_strategy_indicators_fn,
        env_reader=env_reader,
    )
