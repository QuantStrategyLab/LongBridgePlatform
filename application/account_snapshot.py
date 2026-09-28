"""Pure helpers for one optional natural-cycle account history sample."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from typing import Any

from application.broker_reconciliation import _normalize_broker_balance, _normalize_cash


ACCOUNT_SNAPSHOT_SOURCE_BINDING_KIND = "deployment_scope_token_version"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CAPTURE: ContextVar[dict[str, Any] | None] = ContextVar(
    "longbridge_natural_cycle_history",
    default=None,
)


def begin_natural_cycle_history() -> Token[dict[str, Any] | None]:
    return _CAPTURE.set(
        {
            "token_seen": False,
            "version_name": None,
            "account_seen": False,
            "projection": None,
            "started": None,
            "finished": None,
        }
    )


def end_natural_cycle_history(token: Token[dict[str, Any] | None]) -> None:
    _CAPTURE.reset(token)


def note_cycle_token_version(version_name: str | None) -> None:
    slot = _CAPTURE.get()
    if slot is None:
        return
    slot["token_seen"] = True
    slot["version_name"] = version_name


def current_cycle_token_version() -> str | None:
    slot = _CAPTURE.get()
    if slot is None or not slot.get("token_seen"):
        return None
    version_name = slot.get("version_name")
    if isinstance(version_name, str) and version_name.strip():
        return version_name
    return None


def note_cycle_history_observation(
    projection: Mapping[str, Any] | None,
    started: Any,
    finished: Any,
) -> None:
    """Keep the first balance read. Later reads in the same cycle are not merged."""

    slot = _CAPTURE.get()
    if slot is None or slot.get("account_seen"):
        return
    slot["account_seen"] = True
    slot["projection"] = dict(projection) if isinstance(projection, Mapping) else None
    slot["started"] = started
    slot["finished"] = finished


def cycle_history_observation() -> dict[str, Any] | None:
    slot = _CAPTURE.get()
    if slot is None or not slot.get("account_seen"):
        return None
    projection = slot.get("projection")
    if not isinstance(projection, Mapping):
        return None
    return {
        "projection": {
            "broker_reported_balances": list(projection["broker_reported_balances"]),
            "cash": list(projection["cash"]),
        },
        "started": slot.get("started"),
        "finished": slot.get("finished"),
    }


def split_cycle_token(loaded: Any) -> tuple[str, str | None]:
    """Pass a plain token through. A metadata response is stripped once."""

    if isinstance(loaded, str):
        return loaded, None
    value = getattr(loaded, "value", None)
    version_name = getattr(loaded, "version_name", None)
    if not isinstance(value, str):
        raise RuntimeError("account snapshot token metadata is unavailable")
    token = value.strip()
    if isinstance(version_name, str) and version_name.strip():
        return token, version_name
    return token, None


def read_cycle_token(
    project_id: str | None,
    secret_name: str,
    *,
    store: Any,
    fetch_token: Callable[[str | None, str], str],
) -> Any:
    """One secret response. Metadata failure does not fall back to another read."""

    reader = getattr(store, "get_secret_with_metadata", None)
    if callable(reader):
        return reader(secret_name, project_id=project_id)
    return fetch_token(project_id, secret_name)


def project_cycle_history_balances(account_balance: Any) -> dict[str, list[dict[str, str]]] | None:
    """Project complete currency rows. Any gap drops the sample and stays local."""

    try:
        if isinstance(account_balance, (str, bytes)) or not isinstance(account_balance, (list, tuple)):
            return None
        if not account_balance:
            return None
        balances: list[dict[str, str]] = []
        cash_rows: list[dict[str, str]] = []
        seen_balance: set[str] = set()
        seen_cash: set[str] = set()
        for item in account_balance:
            balance = _normalize_broker_balance(item)
            currency = str(balance["currency"])
            if currency in seen_balance:
                return None
            seen_balance.add(currency)
            balances.append(
                {
                    "currency": currency,
                    "net_assets": str(balance["net_assets"]),
                    "total_cash": str(balance["total_cash"]),
                }
            )
            for row in _normalize_cash(item)["cash_infos"]:
                cash_currency = str(row["currency"])
                if cash_currency in seen_cash:
                    return None
                seen_cash.add(cash_currency)
                cash_rows.append(
                    {
                        "currency": cash_currency,
                        "available_cash": str(row["available_cash"]),
                        "frozen_cash": str(row["frozen_cash"]),
                        "settling_cash": str(row["settling_cash"]),
                    }
                )
        balances.sort(key=lambda row: row["currency"])
        cash_rows.sort(key=lambda row: row["currency"])
        return {"broker_reported_balances": balances, "cash": cash_rows}
    except Exception:
        return None


def trusted_cloud_run_region(env_reader: Callable[[str, str], str | None]) -> str | None:
    for name in ("CLOUD_RUN_REGION", "GOOGLE_CLOUD_REGION"):
        value = str(env_reader(name, "") or "").strip()
        if value:
            return value
    return None


def build_account_snapshot_source_binding(
    *,
    version_name: str | None,
    project_id: str | None,
    service: str | None,
    revision: str | None,
    account_scope: str,
    region: str | None = None,
) -> dict[str, object]:
    """Hash one deployment, scope, and token version. Missing parts stay unbound."""

    version = str(version_name or "").strip()
    project = str(project_id or "").strip()
    service_name = str(service or "").strip()
    revision_name = str(revision or "").strip()
    scope = str(account_scope or "").strip().upper()
    if not version or not project or not service_name or not revision_name or not scope:
        return _unbound_source()
    payload = {
        "account_scope": scope,
        "project_id": project,
        "revision": revision_name,
        "service": service_name,
        "version_name": version,
    }
    region_name = str(region or "").strip()
    if region_name:
        payload["region"] = region_name
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    if _SHA256_PATTERN.fullmatch(digest) is None:
        return _unbound_source()
    return {
        "kind": ACCOUNT_SNAPSHOT_SOURCE_BINDING_KIND,
        "status": "bound",
        "id": digest,
    }


def _unbound_source() -> dict[str, object]:
    return {
        "kind": ACCOUNT_SNAPSHOT_SOURCE_BINDING_KIND,
        "status": "unavailable",
        "id": None,
    }
