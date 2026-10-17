"""Private notification storage, separate from all execution state.

Conditional writes are single attempts. An uncertain transport/storage result
must be reconciled from its persisted marker, never retried as a fresh send.
"""
from __future__ import annotations

import json
import re


def validate_prefix(value: str) -> str:
    value = str(value or "").rstrip("/")
    if not re.fullmatch(r"gs://[a-z0-9][a-z0-9._-]+/[^\s?#*]+", value):
        raise ValueError("notification_store_configuration_missing")
    return value


class GCSNotificationStore:
    def __init__(self, prefix: str, *, client=None):
        self.prefix = validate_prefix(prefix)
        if client is None:
            from google.cloud import storage
            client = storage.Client()
        self.client = client

    def _blob(self, key):
        from google.cloud import storage
        if not re.fullmatch(r"[a-zA-Z0-9_./-]+", key) or ".." in key:
            raise ValueError("notification_store_key_invalid")
        return storage.Blob.from_string(f"{self.prefix}/{key}.json", client=self.client)

    def read(self, key):
        from google.api_core.exceptions import NotFound
        blob = self._blob(key)
        try:
            blob.reload(retry=None, timeout=30)
        except NotFound:
            return None, None
        generation = int(blob.generation)
        # Disappearance/change after metadata was observed is incomplete
        # history, not a new absent marker authorizing a fresh send.
        raw = blob.download_as_bytes(if_generation_match=generation, retry=None, timeout=30)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("notification_marker_invalid")
        return generation, value

    def compare_and_set(self, key, expected_generation, value):
        from google.api_core.exceptions import PreconditionFailed
        blob = self._blob(key)
        try:
            blob.upload_from_string(json.dumps(value, allow_nan=False, sort_keys=True),
                content_type="application/json", if_generation_match=expected_generation or 0,
                retry=None, timeout=30)
        except PreconditionFailed:
            return False
        return True
