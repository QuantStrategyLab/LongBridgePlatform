"""Explicit startup config normalization for LongBridge (B08, batch 1).

Pure functions: they never read or write ``os.environ``. ``main`` applies the
results explicitly (settings replacement + an env overlay for the monitor
dispatcher) and keeps the returned corrections as an adoption receipt.

Normalization semantics are unchanged from the previous inline module-level
code in ``main.py``:
- plugin mount ``strategy`` fields are rewritten to the active STRATEGY_PROFILE;
- monitor dispatch target ``strategy_profile`` fields are rewritten to the
  active STRATEGY_PROFILE;
- malformed / non-matching JSON is left untouched; unchanged input is returned
  verbatim (no re-serialization).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

MONITOR_DISPATCH_TARGET_ENV_NAMES = (
    "MONITOR_DISPATCH_TARGETS_JSON",
    "LONGBRIDGE_MONITOR_DISPATCH_TARGETS_JSON",
)


@dataclass(frozen=True)
class ConfigCorrection:
    """One adopted correction: which source/field changed and from/to what."""

    source: str
    field: str
    original: str
    adopted: str


@dataclass(frozen=True)
class StartupConfigReceipt:
    """Receipt of the values adopted at startup (no secrets; profile names only)."""

    strategy_profile: str
    corrections: tuple[ConfigCorrection, ...] = ()
    env_overrides: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy_profile": self.strategy_profile,
            "corrections": [
                {"source": c.source, "field": c.field, "original": c.original, "adopted": c.adopted}
                for c in self.corrections
            ],
            "env_overrides": list(self.env_overrides),
        }


def _default_log(message: str) -> None:
    print(message, flush=True)


def normalize_plugin_mounts_strategy(
    raw_mounts_json: str | None,
    *,
    strategy_profile: str,
    log: Callable[[str], None] = _default_log,
) -> tuple[str | None, tuple[ConfigCorrection, ...]]:
    if not raw_mounts_json:
        return raw_mounts_json, ()
    try:
        mounts = json.loads(raw_mounts_json)
    except json.JSONDecodeError:
        return raw_mounts_json, ()
    if not isinstance(mounts, dict):
        return raw_mounts_json, ()
    plugins = mounts.get("strategy_plugins")
    if not isinstance(plugins, list):
        return raw_mounts_json, ()
    corrections: list[ConfigCorrection] = []
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        old = entry.get("strategy")
        if old and old != strategy_profile:
            entry["strategy"] = strategy_profile
            corrections.append(
                ConfigCorrection(
                    source="STRATEGY_PLUGIN_MOUNTS_JSON",
                    field="strategy_plugins[].strategy",
                    original=str(old),
                    adopted=strategy_profile,
                )
            )
            log(f"[config-sync] Plugin mount strategy corrected: {old} → {strategy_profile}")
    if not corrections:
        return raw_mounts_json, ()
    return json.dumps(mounts, ensure_ascii=False), tuple(corrections)


def normalize_monitor_dispatch_targets(
    env: Mapping[str, str],
    *,
    strategy_profile: str,
    log: Callable[[str], None] = _default_log,
) -> tuple[dict[str, str], tuple[ConfigCorrection, ...]]:
    """Return ``{env_name: corrected_json}`` for changed names only (env untouched)."""
    overrides: dict[str, str] = {}
    corrections: list[ConfigCorrection] = []
    for env_name in MONITOR_DISPATCH_TARGET_ENV_NAMES:
        raw = env.get(env_name)
        if not raw:
            continue
        try:
            targets = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(targets, dict):
            continue
        entries = targets.get("targets")
        if not isinstance(entries, list):
            continue
        changed = False
        for entry in entries:
            if isinstance(entry, dict):
                old = entry.get("strategy_profile")
                if old and old != strategy_profile:
                    entry["strategy_profile"] = strategy_profile
                    changed = True
                    corrections.append(
                        ConfigCorrection(
                            source=env_name,
                            field="targets[].strategy_profile",
                            original=str(old),
                            adopted=strategy_profile,
                        )
                    )
                    log(f"[config-sync] {env_name} strategy_profile corrected: {old} → {strategy_profile}")
        if changed:
            overrides[env_name] = json.dumps(targets, ensure_ascii=False)
    return overrides, tuple(corrections)


def overlay_env(env: Mapping[str, str], overrides: Mapping[str, str]) -> dict[str, str]:
    """Read-only view used by consumers that previously read the mutated env."""
    return {**dict(env), **dict(overrides)}
