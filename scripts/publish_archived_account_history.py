"""Replay a bounded, explicitly selected archive source into history only.

No broker calls, observation-time changes, raw files, retry or latest updates.
The receiver remains authoritative for the protected same-account source list.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

MAX_BYTES = 65536
MAX_OBJECTS = 4096
HASH = re.compile(r"^[a-f0-9]{64}$")
TARGET = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class ArchiveError(ValueError):
    """Only fixed diagnostic categories are printed by the CLI."""


def configuration(env, now=None):
    now = now or datetime.now(UTC)
    try:
        target = env["ACCOUNT_HISTORY_TARGET_ID"]
        scope = env["ACCOUNT_HISTORY_EXPECTED_SCOPE"]
        digest = env["ARCHIVE_SOURCE_SHA256"]
        start = date.fromisoformat(env["ARCHIVE_FROM"])
        end = date.fromisoformat(env["ARCHIVE_TO"])
        prefix = urlsplit(env["ACCOUNT_HISTORY_GCS_PREFIX"])
        sync = urlsplit(env["ACCOUNT_FACTS_SYNC_URL"])
        token = env["ACCOUNT_FACTS_SYNC_TOKEN"]
        parts = prefix.path.strip("/").split("/")
        if (not TARGET.fullmatch(target) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", scope)
                or not HASH.fullmatch(digest) or start > end
                or start.isoformat() != env["ARCHIVE_FROM"] or end.isoformat() != env["ARCHIVE_TO"]
                or end >= now.date() or start < now.date() - timedelta(days=365)
                or prefix.scheme != "gs" or not prefix.netloc or prefix.query or prefix.fragment
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", prefix.netloc)
                or any(not p or p in {".", ".."} for p in parts)
                or parts[-1] != "account_snapshots"
                or sync.scheme != "https" or not sync.hostname or sync.username or sync.password
                or sync.query or sync.fragment or sync.path != "/api/account-facts/sync"
                or sync.port not in {None, 443} or not token or len(token) > 16384
                or any(c.isspace() for c in token)):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise ArchiveError("archive_config_invalid") from None
    return {"target": target, "scope": scope, "digest": digest, "start": start, "end": end,
            "bucket": prefix.netloc, "prefix": "/".join(parts) + "/" + target + "/",
            "url": sync._replace(path="/api/account-facts/history/sync").geturl(), "token": token}


def select_objects(blobs, cfg):
    """Group fixed-generation candidates from one independently selected source."""
    selected = {}
    count = 0
    for blob in blobs:
        count += 1
        if count > MAX_OBJECTS:
            raise ArchiveError("archive_listing_truncated")
        if not blob.name.startswith(cfg["prefix"]):
            raise ArchiveError("archive_path_invalid")
        tail = blob.name[len(cfg["prefix"]):].split("/")
        if len(tail) != 3 or not HASH.fullmatch(tail[0]):
            raise ArchiveError("archive_path_invalid")
        if hashlib.sha256(tail[0].encode()).hexdigest() != cfg["digest"]:
            continue
        try:
            day = date.fromisoformat(tail[1])
            valid = (day.isoformat() == tail[1] and re.fullmatch(r"\d{12}Z\.json", tail[2])
                     and str(blob.generation).isdigit() and int(blob.generation) > 0
                     and 0 < int(blob.size) <= MAX_BYTES)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ArchiveError("archive_metadata_invalid")
        if cfg["start"] <= day <= cfg["end"]:
            selected.setdefault(tail[1], []).append(blob)
    if not selected:
        raise ArchiveError("archive_source_empty")
    return sorted(selected.items())


def validate_bytes(raw, blob, day, cfg, now=None):
    now = now or datetime.now(UTC)
    try:
        if not isinstance(raw, bytes) or len(raw) > MAX_BYTES or len(raw) != int(blob.size):
            raise ValueError
        payload = json.loads(raw)
        source = payload["source_binding"]
        started = datetime.fromisoformat(payload["observed_started_at"].replace("Z", "+00:00"))
        finished = datetime.fromisoformat(payload["observed_finished_at"].replace("Z", "+00:00"))
        if (payload["schema_version"] != "longbridge_account_snapshot_history.v1"
                or payload["snapshot_schema_version"] != "longbridge_account_snapshot.v1"
                or payload["target_id"] != cfg["target"] or payload["account_scope"] != cfg["scope"]
                or source["kind"] != "deployment_scope_token_version" or source["status"] != "bound"
                or not HASH.fullmatch(source["id"])
                or hashlib.sha256(source["id"].encode()).hexdigest() != cfg["digest"]
                or payload["observation_date"] != day or payload["snapshot_atomic"] is not False
                or started.tzinfo is None or finished.tzinfo is None
                or started > finished or finished > now
                or started.astimezone(UTC).date().isoformat() != day
                or blob.name.rsplit("/", 1)[-1] != finished.astimezone(UTC).strftime("%H%M%S%fZ.json")):
            raise ValueError
        return payload
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ArchiveError("archive_object_invalid") from None


def publish(raw, payload, cfg, session):
    try:
        with session.post(cfg["url"], data=raw, headers={"Authorization": "Bearer " + cfg["token"],
                          "Content-Type": "application/json"}, timeout=(10, 20),
                          allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ArchiveError("archive_publish_rejected")
            body = bytearray()
            for block in response.iter_content(8192):
                body.extend(block)
                if len(body) > MAX_BYTES:
                    raise ArchiveError("archive_ack_invalid")
            ack = json.loads(body)
        if (ack.get("ok") is not True or ack.get("stored") is not True
                or ack.get("platform") != "longbridge" or ack.get("target_id") != cfg["target"]
                or ack.get("observation_date") != payload["observation_date"]
                or ack.get("observed_finished_at") != payload["observed_finished_at"]
                or not isinstance(ack.get("unchanged"), bool)):
            raise ArchiveError("archive_ack_invalid")
        return ack["unchanged"]
    except ArchiveError:
        raise
    except Exception:
        # An unknown outcome must be investigated; never retry a POST here.
        raise ArchiveError("archive_publish_unknown") from None


def replay(cfg, client, session):
    iterator = client.list_blobs(cfg["bucket"], prefix=cfg["prefix"], page_size=MAX_OBJECTS + 1,
                                 max_results=MAX_OBJECTS + 1, timeout=20, retry=None)
    rows = select_objects(iterator, cfg)
    result = {"selected_days": len(rows), "inserted": 0, "unchanged": 0, "dates": []}
    for day, candidates in rows:
        selected = None
        # Filename clock time alone is not chronological across UTC midnight.
        # Read each fixed generation in memory and compare the original instant.
        for listed in candidates:
            blob = client.bucket(cfg["bucket"]).blob(listed.name, generation=int(listed.generation))
            raw = blob.download_as_bytes(if_generation_match=int(listed.generation), timeout=20, retry=None)
            payload = validate_bytes(raw, listed, day, cfg)
            at = datetime.fromisoformat(payload["observed_finished_at"].replace("Z", "+00:00"))
            if selected is not None and at == selected[0] and raw != selected[1]:
                raise ArchiveError("archive_observation_conflict")
            if selected is None or at > selected[0]:
                selected = (at, raw, payload)
        _, raw, payload = selected
        unchanged = publish(raw, payload, cfg, session)
        result["unchanged" if unchanged else "inserted"] += 1
        result["dates"].append(day)
        print(json.dumps({"date": day, "history": "unchanged" if unchanged else "inserted"}), flush=True)
    return result


def main():
    try:
        cfg = configuration(os.environ)
        from google.cloud import storage
        import requests
        with requests.Session() as session:
            result = replay(cfg, storage.Client(), session)
        print(json.dumps({"status": "completed", **result, "latest_updated": False}))
        return 0
    except ArchiveError as error:
        print(json.dumps({"status": "stopped", "reason": str(error)}))
    except Exception:
        print(json.dumps({"status": "stopped", "reason": "archive_read_failed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
