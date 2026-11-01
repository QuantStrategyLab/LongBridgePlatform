"""Cloud Run Job entry point. No trading routes, broker imports or CLI overrides."""

import json
import logging
import os
import signal
import sys
from datetime import date, datetime
from urllib.parse import urlsplit

import requests
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.auth.transport.requests import Request
from google.cloud import storage
from google.oauth2.id_token import fetch_id_token

from .core import (
    Calendar,
    Collector,
    ObservationError,
    UTC,
    Watchdog,
    instant,
    sanitize,
)

LIMIT = 1024 * 1024


class Store:
    def __init__(self, bucket):
        self.bucket = storage.Client().bucket(bucket)

    def get(self, key):
        try:
            blob = self.bucket.blob(key)
            blob.reload(timeout=10, retry=None)
            if blob.size is None or blob.size > LIMIT:
                raise ObservationError("stored_record_invalid")
            return json.loads(
                blob.download_as_bytes(
                    if_generation_match=blob.generation, timeout=10, retry=None
                )
            )
        except NotFound:
            return None
        except Exception:
            raise ObservationError("storage_unavailable") from None

    def create(self, key, value):
        body = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        if len(body) > LIMIT:
            raise ObservationError("record_too_large")
        try:
            self.bucket.blob(key).upload_from_string(
                body,
                content_type="application/json",
                if_generation_match=0,
                timeout=10,
                retry=None,
            )
            return True
        except PreconditionFailed:
            if self.get(key) is None:
                raise ObservationError("storage_unavailable") from None
            return False
        except Exception:
            # An accepted write with a lost response is not safe to retry.
            existing = self.get(key)
            if existing == value:
                return True
            raise ObservationError("storage_unavailable") from None


def snapshot_fetcher(base, audience=None):
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".run.app")
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.port
    ):
        raise ObservationError("configuration_invalid")
    base = base.rstrip("/")
    audience = (audience or base).rstrip("/")
    audience_parts = urlsplit(audience)
    if (
        audience_parts.scheme != "https"
        or not audience_parts.hostname
        or not audience_parts.hostname.endswith(".run.app")
        or audience_parts.path
        or audience_parts.query
        or audience_parts.fragment
        or audience_parts.username
        or audience_parts.port
    ):
        raise ObservationError("configuration_invalid")

    def fetch():
        def deadline(_signum, _frame):
            raise ObservationError("http_failure")

        previous = signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, 20)
        try:
            token = fetch_id_token(Request(), audience)
            with requests.get(
                base + "/account-snapshot",
                headers={"Authorization": "Bearer " + token},
                timeout=20,
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code != 200:
                    raise ObservationError(
                        f"http_{response.status_code}"
                        if response.status_code in (401, 403)
                        else "http_failure"
                    )
                data = bytearray()
                for chunk in response.iter_content(16384):
                    data.extend(chunk)
                    if len(data) > LIMIT:
                        raise ObservationError("schema_invalid")
                try:
                    return json.loads(data)
                except (ValueError, UnicodeError):
                    raise ObservationError("schema_invalid") from None
        except ObservationError:
            raise
        except Exception:
            raise ObservationError("http_failure") from None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    return fetch


def sender(token, recipient):
    # Match existing build_sender: POST sendMessage, ten seconds, API ACK only.
    if not token or not recipient:
        raise ObservationError("notification_configuration_invalid")

    def send(message):
        try:
            from notifications.intent_transport import send_intent
            receipt = send_intent(message, token=token, chat_id=recipient, requests_module=requests,
                                  timeout=10, request_options={"allow_redirects": False})
            return receipt["http_status"] == 200 and receipt["ok"]
        except Exception:
            return False

    return send


def main():
    from quant_platform_kit.notifications import install_log_redaction
    install_log_redaction(wrap_streams=True)
    logging.disable(logging.CRITICAL)
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ("collect", "watchdog", "verify"):
            raise ObservationError("configuration_invalid")
        mode = sys.argv[1]
        store = Store(os.environ["OBSERVATION_BUCKET"])
        start = date.fromisoformat(os.environ["OBSERVATION_START"])
        now = datetime.now(UTC)
        if mode in ("collect", "verify"):
            fetch = snapshot_fetcher(
                os.environ["PAPER_SERVICE_URL"], os.environ.get("PAPER_TOKEN_AUDIENCE")
            )
            if mode == "verify":
                record = sanitize(
                    fetch(), os.environ["EXPECTED_SOURCE_BINDING"], datetime.now(UTC)
                )
                store.create(
                    f"health/verification/{now.strftime('%Y%m%dT%H%M%fZ')}.json",
                    {
                        "verified_at": now.isoformat(),
                        "schema_version": record["schema_version"],
                        "identity_verification": record["identity_verification"],
                        "orders_complete": False,
                    },
                )
                print(
                    '{"component":"verification","result":"verified","official_snapshot_created":false}'
                )
                return 0
            runner = Collector(
                store,
                fetch,
                os.environ["EXPECTED_SOURCE_BINDING"],
                Calendar(),
                start,
            )
        else:
            notify = sender(
                os.environ["TELEGRAM_TOKEN"], os.environ["GLOBAL_TELEGRAM_CHAT_ID"]
            )
            runner = Watchdog(
                store,
                notify,
                None,
                start,
                instant(os.environ["OBSERVATION_ACTIVATED_AT"]),
                verification=os.environ.get("OBSERVATION_VERIFY_NOTIFICATIONS")
                == "true",
            )
            try:
                runner.calendar = Calendar()
            except Exception:
                try:
                    runner.notify(f"{now.date()}/calendar_unavailable", True, now)
                except ObservationError:
                    runner.send(
                        "PAPER observation alert: calendar_unavailable; persistent dedup unavailable"
                    )
                raise ObservationError("calendar_unavailable") from None
        result = runner.run(now)
        print(json.dumps({"component": mode, "result": result}))
        return (
            0
            if result
            in {
                "stored",
                "no_due_gap",
                "slot_already_claimed",
                "waiting_recovery_slot",
                "unrecoverable_gaps",
                "checked",
                "before_activation",
            }
            else 1
        )
    except Exception:
        print('{"result":"observation_failed"}')
        return 1


if __name__ == "__main__":
    sys.exit(main())
