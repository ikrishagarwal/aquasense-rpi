"""Durable HTTPS delivery for the standalone Arduino monitor."""

import http.client
import json
import os
import re
import shlex
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

REQUEST_TIMEOUT_SECONDS = 5
MAX_BACKOFF_SECONDS = 300
OUTBOX_PATH = Path("var/arduino-dispenses.sqlite3")
DELIVERY_ENV_KEYS = (
    "AQUASENSE_BACKEND_URL",
    "AQUASENSE_DEVICE_ID",
    "AQUASENSE_DEVICE_TOKEN",
)


@dataclass(frozen=True)
class DeliverySettings:
    host: str
    port: int | None
    device_id: str
    token: str = field(repr=False)

    @classmethod
    def load(
        cls, environ: Mapping[str, str], *, env_file: Path | None = None
    ) -> "DeliverySettings | None":
        values = read_delivery_env(env_file)
        values.update(
            {key: environ[key] for key in DELIVERY_ENV_KEYS if key in environ}
        )
        if not values:
            return None
        url, device_id, token = (values.get(key, "") for key in DELIVERY_ENV_KEYS)
        if not (url and device_id and token):
            keys = ", ".join(DELIVERY_ENV_KEYS)
            raise ValueError(f"Set all of {keys} to enable delivery")
        try:
            endpoint = urlsplit(url)
            host, port = endpoint.hostname, endpoint.port
        except ValueError:
            raise ValueError("AQUASENSE_BACKEND_URL must be an HTTPS origin") from None
        if (
            not host
            or endpoint.scheme != "https"
            or endpoint.username is not None
            or endpoint.path not in ("", "/")
            or endpoint.query
            or endpoint.fragment
            or re.search(r"\s", url)
            or port == 0
        ):
            raise ValueError("AQUASENSE_BACKEND_URL must be an HTTPS origin")
        try:
            canonical_id = str(UUID(device_id))
        except ValueError:
            raise ValueError("AQUASENSE_DEVICE_ID must be a UUID") from None
        if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
            raise ValueError("AQUASENSE_DEVICE_TOKEN must be a bearer token")
        return cls(host, port, canonical_id, token)


def read_delivery_env(env_file: Path | None) -> dict[str, str]:
    path = env_file if env_file is not None else Path(".env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        if env_file is None:
            return {}
        raise
    except UnicodeError:
        raise ValueError(f"Delivery env file must be UTF-8: {path}") from None

    values: dict[str, str] = {}
    for line_number, line in enumerate(lines, 1):
        key, equals, raw = line.partition("=")
        key = key.strip()
        if key not in DELIVERY_ENV_KEYS:
            continue
        try:
            parts = shlex.split(raw, comments=True)
            if not equals or len(parts) > 1:
                raise ValueError
        except ValueError:
            raise ValueError(
                f"Invalid {key} assignment in {path} at line {line_number}"
            ) from None
        values[key] = parts[0] if parts else ""
    return values


@dataclass(frozen=True)
class DeliveryResult:
    status: int
    committed: bool = False


def post_dispense(settings: DeliverySettings, payload: str) -> DeliveryResult:
    connection = http.client.HTTPSConnection(
        settings.host, settings.port, timeout=REQUEST_TIMEOUT_SECONDS
    )
    with closing(connection):
        connection.request(
            "POST",
            "/v1/edge/dispenses",
            body=payload.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.token}",
                "Content-Type": "application/json",
            },
        )
        with connection.getresponse() as response:
            if response.status == HTTPStatus.OK:
                try:
                    ack = json.loads(response.read(512))
                except ValueError:
                    ack = None
                return DeliveryResult(response.status, ack == {"status": "committed"})
            return DeliveryResult(response.status)


class DispenseOutbox:
    def __init__(
        self,
        settings: DeliverySettings,
        log: Callable[[str], None],
        *,
        path: Path = OUTBOX_PATH,
        send: Callable[[DeliverySettings, str], DeliveryResult] = post_dispense,
    ) -> None:
        self.settings = settings
        self.log = log
        self.path = path
        self.send = send
        self.wake = threading.Event()
        self.stopping = threading.Event()
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        with self.database() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS arduino_dispenses (
                    event_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    failure_reason TEXT,
                    rejected INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS arduino_dispenses_pending
                    ON arduino_dispenses (device_id, next_attempt)
                    WHERE rejected = 0;
                """)
        self.worker = threading.Thread(
            target=self.run, daemon=True, name="arduino-http"
        )
        self.worker.start()

    @contextmanager
    def database(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=0.25)) as db, db:
            db.execute("PRAGMA synchronous = FULL")
            yield db

    def enqueue(
        self,
        *,
        recognized_user_id: str | None,
        start_ts: str,
        end_ts: str,
        volume_ml: float,
        tds_ppm: float | None,
    ) -> None:
        event_id = str(uuid4())
        try:
            payload = json.dumps(
                {
                    "event_id": event_id,
                    "device_id": self.settings.device_id,
                    "recognized_user_id": recognized_user_id,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "volume_ml": volume_ml,
                    "tds_ppm": tds_ppm,
                },
                allow_nan=False,
            )
            with self.database() as db:
                db.execute(
                    "INSERT INTO arduino_dispenses (event_id, device_id, payload)"
                    " VALUES (?, ?, ?)",
                    (event_id, self.settings.device_id, payload),
                )
        except (OSError, sqlite3.Error, ValueError) as error:
            self.log(f"Unable to persist Arduino dispense {event_id}: {error}")
            return
        self.wake.set()

    def deliver_one(self) -> bool:
        with self.database() as db:
            row = db.execute(
                "SELECT event_id, payload, attempts FROM arduino_dispenses"
                " WHERE device_id = ? AND rejected = 0 AND next_attempt <= ?"
                " ORDER BY next_attempt, rowid LIMIT 1",
                (self.settings.device_id, time.time()),
            ).fetchone()
        if row is None:
            return False
        event_id, payload, attempts = row
        rejected = False
        try:
            result = self.send(self.settings, payload)
        except (OSError, http.client.HTTPException) as error:
            reason = f"Network failure ({type(error).__name__})"
        else:
            if result.committed:
                with self.database() as db:
                    db.execute(
                        "DELETE FROM arduino_dispenses WHERE event_id = ?", (event_id,)
                    )
                return True
            if 200 <= result.status < 300:
                reason = f"HTTP {result.status} without a commit acknowledgment"
            else:
                phrase = http.client.responses.get(result.status, "")
                reason = f"HTTP {result.status} {phrase}".rstrip()
                rejected = result.status != 429 and not 500 <= result.status < 600
        delay = min(MAX_BACKOFF_SECONDS, 2 ** min(attempts, 9))
        with self.database() as db:
            db.execute(
                "UPDATE arduino_dispenses SET attempts = attempts + 1,"
                " next_attempt = ?, failure_reason = ?, rejected = ?"
                " WHERE event_id = ?",
                (time.time() + delay, reason, rejected, event_id),
            )
        if rejected:
            self.log(f"Arduino dispense {event_id} rejected: {reason}")
        return True

    def run(self) -> None:
        while not self.stopping.is_set():
            self.wake.clear()
            try:
                if self.deliver_one():
                    continue
            except (OSError, sqlite3.Error) as error:
                self.log(f"Arduino dispense outbox error: {error}")
            self.wake.wait(timeout=1)

    def close(self) -> None:
        self.stopping.set()
        self.wake.set()
        # An in-flight request can be retried from SQLite after process exit.
        self.worker.join(timeout=0.25)
