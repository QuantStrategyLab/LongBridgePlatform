from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.inspect_longport_token_expiry import (  # noqa: E402
    TARGET_SECRETS,
    days_until_expiry,
    decode_token_expiry_unix,
    inspect_one,
    main,
)


def _jwt_with_exp(exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"hdr.{payload}.sig"


def test_target_secret_names_match_public_manifest():
    assert TARGET_SECRETS == {
        "paper": "longport_token_paper",
        "hk": "longport_token_hk",
        "sg": "longport_token_sg",
    }


def test_decode_token_expiry_unix_reads_exp():
    token = _jwt_with_exp(1_700_000_000)
    assert decode_token_expiry_unix(token) == 1_700_000_000.0


def test_decode_token_expiry_unix_returns_none_for_opaque():
    assert decode_token_expiry_unix("not-a-jwt") is None


def test_inspect_one_ok_warn_expired_without_leaking_token():
    now = time.time()
    secret_token = _jwt_with_exp(int(now + 60 * 86400))
    ok = inspect_one(
        project_id="demo",
        secret_name="longport_token_paper",
        warn_days=30,
        now=now,
        token_reader=lambda *_a, **_k: secret_token,
    )
    assert ok["status"] == "ok"
    assert ok["ok"] is True
    dumped = json.dumps(ok)
    assert secret_token not in dumped
    assert "hdr." not in dumped

    warn = inspect_one(
        project_id="demo",
        secret_name="longport_token_paper",
        warn_days=30,
        now=now,
        token_reader=lambda *_a, **_k: _jwt_with_exp(int(now + 10 * 86400)),
    )
    assert warn["status"] == "warn"
    assert warn["ok"] is False

    expired = inspect_one(
        project_id="demo",
        secret_name="longport_token_paper",
        warn_days=30,
        now=now,
        token_reader=lambda *_a, **_k: _jwt_with_exp(int(now - 86400)),
    )
    assert expired["status"] == "expired"
    assert expired["ok"] is False


def test_days_until_expiry_math():
    assert abs(days_until_expiry(100_000.0, now=100_000.0 - 2 * 86400) - 2.0) < 1e-9


def test_main_requires_target_or_secret(capsys):
    code = main(["--project", "demo"])
    assert code == 2
    err = capsys.readouterr().err
    assert "provide --target or --secret" in err
