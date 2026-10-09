#!/usr/bin/env python3
"""Pre-expiry LongPort Legacy Access Token refresh (Actions / ops).

Default is dry-run: report days remaining and whether a refresh would run.
Live mode calls GET /v1/token/refresh with required ``expired_at``, then adds a
Secret Manager version. Never prints secret values.

Rollout order: paper → sg → hk last. Prefer dry-run until paper/sg succeed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.inspect_longport_token_expiry import (
    TARGET_SECRETS,
    days_until_expiry,
    decode_token_expiry_unix,
)

DEFAULT_REFRESH_THRESHOLD_DAYS = 30
DEFAULT_NEW_TOKEN_LIFETIME_DAYS = 90
APP_KEY_SECRETS = {
    "paper": "longport-app-key-paper",
    "hk": "longport-app-key-hk",
    "sg": "longport-app-key-sg",
}
APP_SECRET_SECRETS = {
    "paper": "longport-app-secret-paper",
    "hk": "longport-app-secret-hk",
    "sg": "longport-app-secret-sg",
}


def _access_secret_latest(project_id: str, secret_name: str) -> str:
    try:
        import google.cloud.secretmanager_v1 as secret_manager
    except ImportError:  # pragma: no cover
        from google.cloud import secret_manager

    client = secret_manager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()


def _add_secret_version(project_id: str, secret_name: str, payload: str) -> str:
    try:
        import google.cloud.secretmanager_v1 as secret_manager
    except ImportError:  # pragma: no cover
        from google.cloud import secret_manager

    client = secret_manager.SecretManagerServiceClient()
    parent = f"projects/{project_id}/secrets/{secret_name}"
    version = client.add_secret_version(
        request={"parent": parent, "payload": {"data": payload.encode("UTF-8")}}
    )
    return str(version.name)


def _format_refresh_expired_at(*, lifetime_days: int = DEFAULT_NEW_TOKEN_LIFETIME_DAYS) -> str:
    expiry = datetime.now(timezone.utc) + timedelta(days=int(lifetime_days))
    return expiry.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _longport_sign(method: str, uri: str, headers: dict[str, str], params: str, body: str, secret: str) -> str:
    canonical_request = (
        f"{method.upper()}|{uri}|{params}|"
        f"authorization:{headers['Authorization']}\n"
        f"x-api-key:{headers['X-Api-Key']}\n"
        f"x-timestamp:{headers['X-Timestamp']}\n|authorization;x-api-key;x-timestamp|"
    )
    if body:
        canonical_request += hashlib.sha1(body.encode("utf-8")).hexdigest()
    sign_str = "HMAC-SHA256|" + hashlib.sha1(canonical_request.encode("utf-8")).hexdigest()
    signature = hmac.new(secret.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"HMAC-SHA256 SignedHeaders=authorization;x-api-key;x-timestamp, Signature={signature}"


def call_longport_refresh(
    *,
    access_token: str,
    app_key: str,
    app_secret: str,
    lifetime_days: int = DEFAULT_NEW_TOKEN_LIFETIME_DAYS,
    requests_module: Any | None = None,
) -> str:
    if requests_module is None:
        import requests as requests_module

    expired_at = _format_refresh_expired_at(lifetime_days=lifetime_days)
    params = urlencode({"expired_at": expired_at})
    headers = {
        "X-Api-Key": app_key,
        "Authorization": access_token,
        "X-Timestamp": str(int(time.time())),
        "Content-Type": "application/json; charset=utf-8",
    }
    headers["X-Api-Signature"] = _longport_sign("GET", "/v1/token/refresh", headers, params, "", app_secret)
    response = requests_module.get(
        f"https://openapi.longportapp.com/v1/token/refresh?{params}",
        headers=headers,
        timeout=15,
    ).json()
    if response.get("code") != 0:
        code = response.get("code")
        message = response.get("message") or "unknown error"
        raise RuntimeError(f"LongPort refresh failed with code {code}: {message}")
    data = response.get("data") or {}
    new_token = data.get("token")
    if not isinstance(new_token, str) or not new_token.strip():
        raise RuntimeError("LongPort refresh returned an empty token payload")
    return new_token.strip()


def plan_refresh(
    *,
    days_remaining: float | None,
    refresh_threshold_days: float,
    force: bool,
) -> tuple[bool, str]:
    if force:
        return True, "forced"
    if days_remaining is None:
        return True, "jwt_exp_undecodable"
    if days_remaining <= 0:
        return False, "already_expired_needs_portal_reset"
    if days_remaining < float(refresh_threshold_days):
        return True, "within_threshold"
    return False, "outside_threshold"


def run_refresh(
    *,
    project_id: str,
    target: str,
    dry_run: bool,
    refresh_threshold_days: float = DEFAULT_REFRESH_THRESHOLD_DAYS,
    force: bool = False,
    lifetime_days: int = DEFAULT_NEW_TOKEN_LIFETIME_DAYS,
    secret_reader: Any | None = None,
    secret_writer: Any | None = None,
    refresh_caller: Any | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if target not in TARGET_SECRETS:
        raise ValueError(f"unsupported target={target}")
    token_secret = TARGET_SECRETS[target]
    app_key_secret = APP_KEY_SECRETS[target]
    app_secret_secret = APP_SECRET_SECRETS[target]
    reader = secret_reader or _access_secret_latest
    writer = secret_writer or _add_secret_version
    refresher = refresh_caller or call_longport_refresh

    current = now if now is not None else time.time()
    token = reader(project_id, token_secret)
    expiry = decode_token_expiry_unix(token)
    days = None if expiry is None else days_until_expiry(expiry, now=current)
    should, reason = plan_refresh(
        days_remaining=days,
        refresh_threshold_days=refresh_threshold_days,
        force=force,
    )
    result: dict[str, Any] = {
        "target": target,
        "token_secret": token_secret,
        "dry_run": bool(dry_run),
        "force": bool(force),
        "refresh_threshold_days": float(refresh_threshold_days),
        "jwt_exp_present": expiry is not None,
        "days_until_expiry": None if days is None else round(days, 3),
        "would_refresh": bool(should),
        "reason": reason,
        "status": "planned",
        "ok": True,
    }
    if not should:
        if reason == "already_expired_needs_portal_reset":
            result["status"] = "blocked_expired"
            result["ok"] = False
        else:
            result["status"] = "skipped"
        return result

    if dry_run:
        result["status"] = "dry_run_would_refresh"
        return result

    app_key = reader(project_id, app_key_secret)
    app_secret = reader(project_id, app_secret_secret)
    if not app_key or not app_secret:
        result["status"] = "missing_app_credentials"
        result["ok"] = False
        return result

    new_token = refresher(
        access_token=token,
        app_key=app_key,
        app_secret=app_secret,
        lifetime_days=lifetime_days,
    )
    version_name = writer(project_id, token_secret, new_token)
    result["status"] = "refreshed"
    result["new_version"] = version_name
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--target", required=True, choices=sorted(TARGET_SECRETS))
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Default true: plan only; never call LongPort refresh or write SM",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh even when days remaining >= threshold (still blocked if already expired)",
    )
    parser.add_argument(
        "--refresh-threshold-days",
        type=float,
        default=DEFAULT_REFRESH_THRESHOLD_DAYS,
    )
    parser.add_argument(
        "--new-token-lifetime-days",
        type=int,
        default=DEFAULT_NEW_TOKEN_LIFETIME_DAYS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.target == "hk" and not args.dry_run:
        print(
            "warning: live refresh on target=hk invalidates the current Access Token; "
            "prefer paper then sg first",
            file=sys.stderr,
        )
    result = run_refresh(
        project_id=args.project,
        target=args.target,
        dry_run=args.dry_run,
        refresh_threshold_days=args.refresh_threshold_days,
        force=args.force,
        lifetime_days=args.new_token_lifetime_days,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
