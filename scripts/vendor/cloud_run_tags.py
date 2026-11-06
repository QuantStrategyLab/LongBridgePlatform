"""Versioned offline Cloud Run tag policy. This module never calls a cloud API.

Allocation is an exclusive local reservation, NOT permission to mutate a service.
See docs/cloud-run-tags.md for inventory, ownership and restore requirements.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

CONTRACT_VERSION = 'cloud-run-tags/v1'
TAGGED_URL_BUDGET = 46
_NAME = re.compile(r'[a-z](?:[a-z0-9-]*[a-z0-9])?\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


@dataclass(frozen=True)
class Refusal:
    """Explicit failure with a static, identifier-free reason code."""
    reason: str


@dataclass(frozen=True)
class AllocatedTag:
    """Operational value: do not log the tag or serialize it into public output."""
    tag: str = field(repr=False)


@dataclass(frozen=True)
class Target:
    project: str = field(repr=False)
    region: str = field(repr=False)
    service_name: str = field(repr=False)


@dataclass(frozen=True)
class WarmupApproval:
    """Caller-verified owner decision; this value cannot authenticate an approval."""
    target: Target = field(repr=False)
    action: str = field(repr=False)
    operation_id: str = field(repr=False)
    warmup_traffic_approved: bool = field(repr=False)


@dataclass(frozen=True)
class Lifecycle:
    kind: str
    reason: str
    previous_percent: int = 0
    candidate_percent: int = 0


def headroom(service_name: str) -> int:
    """Measure the exact original name, before any display masking."""
    return TAGGED_URL_BUDGET - len(service_name)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _name(value: object) -> bool:
    return isinstance(value, str) and bool(_NAME.fullmatch(value))


def _target_valid(target: object) -> bool:
    return (isinstance(target, Target) and _nonempty(target.project)
            and _nonempty(target.region) and _name(target.service_name))


def lifecycle_for_impossible_tag(
    target: Target, action: str, operation_id: str, *,
    approval: WarmupApproval | None = None, no_traffic: bool = True,
    image_only: bool = False,
) -> Lifecycle:
    """Default STOP; permit only an exact, explicitly approved warmup 99/1 plan.

    A returned plan still requires the caller's exclusive, single-use execution
    claim and fresh cloud readback. It never grants deployment/traffic authority.
    """
    if not _target_valid(target) or not _nonempty(operation_id):
        return Lifecycle('STOP', 'INVALID_INPUT')
    if no_traffic is not False or image_only is not False:
        return Lifecycle('STOP', 'NO_TRAFFIC_REQUIRED')
    if headroom(target.service_name) > 0:
        return Lifecycle('STOP', 'TAG_NOT_IMPOSSIBLE')
    if (action != 'warmup' or not isinstance(approval, WarmupApproval)
            or approval.warmup_traffic_approved is not True
            or approval.target != target or approval.action != action
            or approval.operation_id != operation_id):
        return Lifecycle('STOP', 'EXACT_WARMUP_APPROVAL_REQUIRED')
    return Lifecycle('MICRO_SPLIT', 'EXACT_WARMUP_APPROVAL', 99, 1)


def _digest(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


def _short_tag(scope: str, operation: str, candidate: str, size: int) -> str:
    digest = _digest(CONTRACT_VERSION, scope, operation, candidate)
    # Always begin with a letter, including the one-character boundary.
    return (chr(ord('a') + int(digest[:2], 16) % 26) + digest[2:])[:size]


def _journal_valid(data: object) -> bool:
    if (not isinstance(data, dict) or set(data) != {'contract_version', 'scopes'}
            or data['contract_version'] != CONTRACT_VERSION
            or not isinstance(data['scopes'], dict)):
        return False
    for scope, records in data['scopes'].items():
        if (not isinstance(scope, str) or not _DIGEST.fullmatch(scope)
                or not isinstance(records, dict)):
            return False
        tags = set()
        for operation, record in records.items():
            if (not isinstance(operation, str) or not _DIGEST.fullmatch(operation)
                    or not isinstance(record, dict)
                    or set(record) != {'candidate', 'preferred', 'tag'}
                    or any(not isinstance(record[k], str) or not _DIGEST.fullmatch(record[k])
                           for k in ('candidate', 'preferred'))
                    or not _name(record['tag']) or record['tag'] in tags):
                return False
            tags.add(record['tag'])
    return True


def _same_file(path: Path, fd: int) -> bool:
    current = os.stat(path, follow_symlinks=False)
    held = os.fstat(fd)
    return (stat.S_ISREG(current.st_mode) and current.st_nlink == 1
            and (current.st_dev, current.st_ino) == (held.st_dev, held.st_ino)
            and stat.S_IMODE(current.st_mode) == 0o600)


def _reserve(data: dict, scope: str, operation: str, candidate: str,
             preferred: str, tag: str, existing: Mapping) -> AllocatedTag | Refusal:
    records = data['scopes'].setdefault(scope, {})
    record = {'candidate': candidate, 'preferred': preferred, 'tag': tag}
    previous = records.get(operation)
    if previous is not None and previous != record:
        return Refusal('OPERATION_REBOUND')
    if any(key != operation and value['tag'] == tag for key, value in records.items()):
        return Refusal('TAG_RESERVED')
    # A cloud revision match alone does not prove operation ownership.
    if tag in existing and (previous != record or _digest(existing[tag]) != candidate):
        return Refusal('TAG_OCCUPIED')
    records[operation] = record
    return AllocatedTag(tag)


def allocate_tag(
    service_name: str, preferred_tag: str, *, existing_tags: Mapping[str, str | None],
    operation_id: str, candidate_id: str, state_path: str | Path,
    project: str, region: str,
) -> AllocatedTag | Refusal:
    """Reserve preferred or one deterministic shortened tag; never overwrite.

    existing_tags must come from a fresh, complete inventory (including zero-
    percent entries); None as a value is occupied with unknown ownership. Use
    one private, shared job-local state_path for all processes/restarts. Only
    the allocator creates this file. Missing evidence or state I/O fails closed.
    """
    if (not _target_valid(Target(project, region, service_name))
            or not _name(preferred_tag) or not _nonempty(operation_id)
            or not _nonempty(candidate_id) or not isinstance(existing_tags, Mapping)
            or any(not _name(k) or (v is not None and not _nonempty(v))
                   for k, v in existing_tags.items())):
        return Refusal('INVALID_INPUT')
    room = headroom(service_name)
    if room <= 0:
        return Refusal('TAG_IMPOSSIBLE')
    scope = _digest(project, region, service_name)
    operation, candidate, preferred = map(_digest, (operation_id, candidate_id, preferred_tag))
    tag = preferred_tag if len(preferred_tag) <= room else _short_tag(scope, operation, candidate, room)
    # Snapshot caller input without ever mutating it. Cloud freshness remains a
    # caller obligation; the reservation ledger is read only AFTER flock.
    existing = dict(existing_tags)
    fd = None
    try:
        path = Path(state_path)
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            created = True
        except FileExistsError:
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            created = False
        with os.fdopen(fd, 'r+', encoding='utf-8') as stream:
            fd = None
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            if not _same_file(path, stream.fileno()):
                return Refusal('STATE_UNAVAILABLE')
            text = stream.read()
            # Another process may have initialized a just-created file first.
            data = json.loads(text) if text else ({'contract_version': CONTRACT_VERSION, 'scopes': {}} if created else None)
            if not _journal_valid(data):
                return Refusal('STATE_UNAVAILABLE')
            result = _reserve(data, scope, operation, candidate, preferred, tag, existing)
            if isinstance(result, Refusal):
                return result
            if not _same_file(path, stream.fileno()):
                return Refusal('STATE_UNAVAILABLE')
            stream.seek(0)
            json.dump(data, stream, sort_keys=True, separators=(',', ':'))
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
            # Persist the directory entry before returning a usable allocation.
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if not _same_file(path, stream.fileno()):
                return Refusal('STATE_UNAVAILABLE')
            return result
    except (OSError, ValueError, TypeError, RecursionError):
        # Do not include exception text: paths/resources may identify accounts.
        return Refusal('STATE_UNAVAILABLE')
    finally:
        if fd is not None:
            os.close(fd)
