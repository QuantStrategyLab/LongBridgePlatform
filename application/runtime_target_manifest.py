"""Public LongBridge runtime-target manifest contract (non-sensitive).

This module loads and strictly validates the checked-in runtime-target
manifest. It does not read GitHub Environment variables, Secret Manager
values, or Cloud Run state, and it never enables trading.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
PLATFORM_ID = "longbridge"
ALLOWED_MODES = frozenset({"live", "paper", "shadow"})

REQUIRED_TARGET_FIELDS = frozenset(
    {
        "id",
        "mode",
        "region",
        "strategy_profile",
        "secret_ref",
        "service",
        "environment",
    }
)

REQUIRED_SECRET_REF_KEYS = frozenset(
    {
        "longport_token",
        "longport_app_key",
        "longport_app_secret",
    }
)

# Public identifiers only: Secret Manager / GitHub secret *names*, never values.
_SECRET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_TARGET_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SERVICE_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_ENVIRONMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_REGION_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_STRATEGY_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_FORBIDDEN_VALUE_KEYS = frozenset(
    {
        "token",
        "password",
        "secret",
        "api_key",
        "api_secret",
        "app_key",
        "app_secret",
        "private_key",
        "access_token",
        "refresh_token",
        "credentials",
        "longport_token",
        "longport_app_key",
        "longport_app_secret",
        "telegram_token",
    }
)

_SECRET_VALUE_HINT_RE = re.compile(
    r"(?i)(-----BEGIN |Bearer\s+[A-Za-z0-9._\-]{20,}|sk-[A-Za-z0-9]{20,})"
)

DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "runtime_targets.manifest.json"
)


class RuntimeTargetManifestError(ValueError):
    """Raised when a runtime-target manifest fails contract validation."""


@dataclass(frozen=True)
class RuntimeTargetSecretRef:
    longport_token: str
    longport_app_key: str
    longport_app_secret: str


@dataclass(frozen=True)
class RuntimeTargetEntry:
    id: str
    mode: str
    region: str
    strategy_profile: str
    service: str
    environment: str
    secret_ref: RuntimeTargetSecretRef
    enabled: bool = False
    label: str | None = None
    account_scope: str | None = None


@dataclass(frozen=True)
class RuntimeTargetManifest:
    schema_version: int
    platform_id: str
    targets: tuple[RuntimeTargetEntry, ...]
    source_path: Path | None = None


def default_manifest_path() -> Path:
    return DEFAULT_MANIFEST_PATH


def load_runtime_target_manifest(path: Path | str | None = None) -> RuntimeTargetManifest:
    """Load and validate a runtime-target manifest from disk."""
    manifest_path = Path(path) if path is not None else default_manifest_path()
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeTargetManifestError(
            f"Unable to read runtime-target manifest: {manifest_path}"
        ) from exc
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeTargetManifestError(
            f"Runtime-target manifest is not valid JSON: {manifest_path}"
        ) from exc
    return validate_runtime_target_manifest(payload, source_path=manifest_path)


def validate_runtime_target_manifest(
    payload: Any,
    *,
    source_path: Path | None = None,
) -> RuntimeTargetManifest:
    """Strictly validate a runtime-target manifest payload."""
    if not isinstance(payload, Mapping):
        raise RuntimeTargetManifestError("Manifest root must be a JSON object")

    _reject_forbidden_value_keys(payload, path="$")
    _reject_secret_value_strings(payload, path="$")

    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise RuntimeTargetManifestError(
            f"Unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION}"
        )

    platform_id = payload.get("platform_id")
    if platform_id != PLATFORM_ID:
        raise RuntimeTargetManifestError(
            f"platform_id must be {PLATFORM_ID!r}, got {platform_id!r}"
        )

    unknown_root = sorted(
        set(payload) - {"schema_version", "platform_id", "targets", "description"}
    )
    if unknown_root:
        raise RuntimeTargetManifestError(
            f"Unknown top-level fields: {', '.join(unknown_root)}"
        )

    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list):
        raise RuntimeTargetManifestError("targets must be a JSON array")
    if not raw_targets:
        raise RuntimeTargetManifestError("targets must contain at least one entry")

    entries: list[RuntimeTargetEntry] = []
    seen_ids: set[str] = set()
    seen_services: set[str] = set()
    seen_environments: set[str] = set()

    for index, raw_target in enumerate(raw_targets):
        path = f"$.targets[{index}]"
        entry = _validate_target(raw_target, path=path)
        if entry.id in seen_ids:
            raise RuntimeTargetManifestError(f"Duplicate target id: {entry.id!r}")
        if entry.service in seen_services:
            raise RuntimeTargetManifestError(
                f"Duplicate Cloud Run service: {entry.service!r}"
            )
        if entry.environment in seen_environments:
            raise RuntimeTargetManifestError(
                f"Duplicate GitHub Environment: {entry.environment!r}"
            )
        seen_ids.add(entry.id)
        seen_services.add(entry.service)
        seen_environments.add(entry.environment)
        entries.append(entry)

    return RuntimeTargetManifest(
        schema_version=SCHEMA_VERSION,
        platform_id=PLATFORM_ID,
        targets=tuple(entries),
        source_path=source_path,
    )


def iter_enabled_targets(
    manifest: RuntimeTargetManifest,
) -> Iterable[RuntimeTargetEntry]:
    """Yield targets marked enabled. Callers must not treat this as production authority."""
    for target in manifest.targets:
        if target.enabled:
            yield target


def _validate_target(raw_target: Any, *, path: str) -> RuntimeTargetEntry:
    if not isinstance(raw_target, Mapping):
        raise RuntimeTargetManifestError(f"{path} must be a JSON object")

    _reject_forbidden_value_keys(raw_target, path=path)
    _reject_secret_value_strings(raw_target, path=path)

    missing = sorted(REQUIRED_TARGET_FIELDS - set(raw_target))
    if missing:
        raise RuntimeTargetManifestError(
            f"{path} missing required fields: {', '.join(missing)}"
        )

    unknown = sorted(
        set(raw_target)
        - REQUIRED_TARGET_FIELDS
        - {"enabled", "label", "account_scope", "description"}
    )
    if unknown:
        raise RuntimeTargetManifestError(
            f"{path} has unknown fields: {', '.join(unknown)}"
        )

    target_id = _require_match(
        raw_target.get("id"),
        field="id",
        path=path,
        pattern=_TARGET_ID_RE,
    )
    mode = str(raw_target.get("mode") or "").strip().lower()
    if mode not in ALLOWED_MODES:
        raise RuntimeTargetManifestError(
            f"{path}.mode must be one of {sorted(ALLOWED_MODES)}, got {raw_target.get('mode')!r}"
        )
    region = _require_match(
        raw_target.get("region"),
        field="region",
        path=path,
        pattern=_REGION_RE,
    )
    strategy_profile = _require_match(
        raw_target.get("strategy_profile"),
        field="strategy_profile",
        path=path,
        pattern=_STRATEGY_PROFILE_RE,
    )
    service = _require_match(
        raw_target.get("service"),
        field="service",
        path=path,
        pattern=_SERVICE_RE,
    )
    environment = _require_match(
        raw_target.get("environment"),
        field="environment",
        path=path,
        pattern=_ENVIRONMENT_RE,
    )

    if "enabled" not in raw_target:
        enabled = False
    else:
        enabled_raw = raw_target.get("enabled")
        if not isinstance(enabled_raw, bool):
            raise RuntimeTargetManifestError(
                f"{path}.enabled must be a boolean when present"
            )
        enabled = enabled_raw

    label = raw_target.get("label")
    if label is not None and (
        not isinstance(label, str) or not label.strip() or len(label) > 64
    ):
        raise RuntimeTargetManifestError(f"{path}.label must be a short non-empty string")

    account_scope = raw_target.get("account_scope")
    if account_scope is not None and (
        not isinstance(account_scope, str)
        or not account_scope.strip()
        or len(account_scope) > 64
    ):
        raise RuntimeTargetManifestError(
            f"{path}.account_scope must be a short non-empty string"
        )

    secret_ref = _validate_secret_ref(raw_target.get("secret_ref"), path=f"{path}.secret_ref")

    return RuntimeTargetEntry(
        id=target_id,
        mode=mode,
        region=region,
        strategy_profile=strategy_profile,
        service=service,
        environment=environment,
        secret_ref=secret_ref,
        enabled=enabled,
        label=str(label).strip() if isinstance(label, str) else None,
        account_scope=str(account_scope).strip() if isinstance(account_scope, str) else None,
    )


def _validate_secret_ref(raw_ref: Any, *, path: str) -> RuntimeTargetSecretRef:
    if not isinstance(raw_ref, Mapping):
        raise RuntimeTargetManifestError(f"{path} must be a JSON object of secret names")

    _reject_forbidden_value_keys(raw_ref, path=path, allow_secret_ref_keys=True)
    _reject_secret_value_strings(raw_ref, path=path)

    missing = sorted(REQUIRED_SECRET_REF_KEYS - set(raw_ref))
    if missing:
        raise RuntimeTargetManifestError(
            f"{path} missing required secret name fields: {', '.join(missing)}"
        )

    unknown = sorted(set(raw_ref) - REQUIRED_SECRET_REF_KEYS)
    if unknown:
        raise RuntimeTargetManifestError(
            f"{path} has unknown fields: {', '.join(unknown)}"
        )

    values = {
        key: _require_secret_name(raw_ref.get(key), field=key, path=path)
        for key in sorted(REQUIRED_SECRET_REF_KEYS)
    }
    return RuntimeTargetSecretRef(
        longport_token=values["longport_token"],
        longport_app_key=values["longport_app_key"],
        longport_app_secret=values["longport_app_secret"],
    )


def _require_match(
    value: Any,
    *,
    field: str,
    path: str,
    pattern: re.Pattern[str],
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeTargetManifestError(f"{path}.{field} must be a non-empty string")
    text = value.strip()
    if not pattern.fullmatch(text):
        raise RuntimeTargetManifestError(f"{path}.{field} has an invalid format: {text!r}")
    return text


def _require_secret_name(value: Any, *, field: str, path: str) -> str:
    text = _require_match(value, field=field, path=path, pattern=_SECRET_NAME_RE)
    if _looks_like_secret_value(text):
        raise RuntimeTargetManifestError(
            f"{path}.{field} looks like a secret value; put only secret names in the manifest"
        )
    return text


def _reject_forbidden_value_keys(
    payload: Mapping[str, Any],
    *,
    path: str,
    allow_secret_ref_keys: bool = False,
) -> None:
    for key in payload:
        key_text = str(key)
        lowered = key_text.lower()
        if allow_secret_ref_keys and lowered in REQUIRED_SECRET_REF_KEYS:
            continue
        if lowered in _FORBIDDEN_VALUE_KEYS or lowered.endswith(
            ("_token", "_password", "_secret", "_api_key", "_private_key")
        ):
            if path.endswith(".secret_ref") and allow_secret_ref_keys:
                continue
            if key_text == "secret_ref":
                continue
            raise RuntimeTargetManifestError(
                f"{path} must not contain secret-value field {key_text!r}; "
                "use secret_ref with Secret Manager names only"
            )


def _reject_secret_value_strings(payload: Any, *, path: str) -> None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            child = f"{path}.{key}"
            _reject_secret_value_strings(value, path=child)
        return
    if isinstance(payload, list):
        for index, value in enumerate(payload):
            _reject_secret_value_strings(value, path=f"{path}[{index}]")
        return
    if isinstance(payload, str) and _looks_like_secret_value(payload):
        raise RuntimeTargetManifestError(
            f"{path} appears to embed a secret value; remove it from the public manifest"
        )


def _looks_like_secret_value(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if _SECRET_VALUE_HINT_RE.search(stripped):
        return True
    # Long opaque tokens are values, not Secret Manager resource names.
    # Human-readable description prose may contain spaces and is allowed.
    if " " not in stripped and len(stripped) >= 64 and re.fullmatch(
        r"[A-Za-z0-9+/=_\-]+", stripped
    ):
        return True
    return False
