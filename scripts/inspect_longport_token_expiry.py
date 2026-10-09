#!/usr/bin/env python3
"""Read-only LongPort Access Token expiry inspector.

Decodes JWT ``exp`` from a Secret Manager token secret and prints days remaining.
Never prints secret values, never calls /v1/token/refresh, never adds SM versions.

Usage (ADC or GHA WIF already authenticated):

  python scripts/inspect_longport_token_expiry.py \\
    --project longbridgequant \\
    --secret longport_token_paper \\
    --warn-days 30
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from typing import Any


TARGET_SECRETS = {
    "paper": "longport_token_paper",
    "hk": "longport_token_hk",
    "sg": "longport_token_sg",
}


def decode_token_expiry_unix(token: str) -> float | None:
    """Return JWT exp as unix seconds, or None if the token is not a decodable JWT."""
    try:
        parts = token.split(".")
        if len(parts) <= 1:
            return None
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        expiry = payload.get("exp")
        if expiry is None:
            return None
        return float(expiry)
    except Exception:
        return None


def days_until_expiry(expiry_unix: float, *, now: float | None = None) -> float:
    current = time.time() if now is None else float(now)
    return (float(expiry_unix) - current) / 86400.0


def access_secret_latest(project_id: str, secret_name: str) -> str:
    try:
        import google.cloud.secretmanager_v1 as secret_manager
    except ImportError:  # pragma: no cover - depends on install layout
        from google.cloud import secret_manager

    client = secret_manager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()


def inspect_one(
    *,
    project_id: str,
    secret_name: str,
    warn_days: float,
    now: float | None = None,
    token_reader: Any | None = None,
) -> dict[str, Any]:
    reader = token_reader or access_secret_latest
    token = reader(project_id, secret_name)
    expiry = decode_token_expiry_unix(token)
    result: dict[str, Any] = {
        "project_id": project_id,
        "secret_name": secret_name,
        "jwt_exp_present": expiry is not None,
        "warn_days": float(warn_days),
    }
    if expiry is None:
        result["status"] = "undecodable"
        result["ok"] = False
        return result
    remaining = days_until_expiry(expiry, now=now)
    result["days_until_expiry"] = round(remaining, 3)
    result["expired"] = remaining <= 0
    if remaining <= 0:
        result["status"] = "expired"
        result["ok"] = False
    elif remaining < float(warn_days):
        result["status"] = "warn"
        result["ok"] = False
    else:
        result["status"] = "ok"
        result["ok"] = True
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="GCP project id")
    parser.add_argument(
        "--target",
        choices=sorted(TARGET_SECRETS),
        help="Named runtime target; sets --secret from the public manifest mapping",
    )
    parser.add_argument(
        "--secret",
        help="Explicit Secret Manager token secret name (overrides --target)",
    )
    parser.add_argument(
        "--warn-days",
        type=float,
        default=30.0,
        help="Fail (exit 2) when days remaining is below this threshold (default 30)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    secret_name = args.secret
    if not secret_name:
        if not args.target:
            print("error: provide --target or --secret", file=sys.stderr)
            return 2
        secret_name = TARGET_SECRETS[args.target]
    result = inspect_one(
        project_id=args.project,
        secret_name=secret_name,
        warn_days=args.warn_days,
    )
    # Never include token material; result is names + numeric expiry distance only.
    print(json.dumps(result, sort_keys=True))
    if result.get("ok"):
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
