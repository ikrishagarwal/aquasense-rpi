import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import closing, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest

from purifier.arduino_monitor import main
from purifier.communication.arduino_delivery import (
    DeliveryResult,
    DeliverySettings,
    DispenseOutbox,
)
from purifier.vision.arduino_worker import Match, Result

DEVICE = "11111111-1111-4111-8111-111111111111"
ENROLLMENT = "22222222-2222-4222-8222-222222222222"
ENV = {
    "AQUASENSE_BACKEND_URL": "https://test.invalid",
    "AQUASENSE_DEVICE_ID": DEVICE,
    "AQUASENSE_DEVICE_TOKEN": "fixture-token",
}


def read_outbox(path: Path, sql: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as db, db:
        return db.execute(sql).fetchall()


def test_cli_queues_completed_reports_and_resumes_delivery_after_restart(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "outbox.sqlite3"
    env_file = tmp_path / "arduino.env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in ENV.items()))
    offline_request = threading.Event()
    clock = SimpleNamespace(now=0.0)

    def offline(_settings: DeliverySettings, _payload: str) -> DeliveryResult:
        offline_request.set()
        return DeliveryResult(503)

    def reports(_fd: int, **_kwargs: object) -> Iterator[bytes | None]:
        for when, report in [
            (0.0, b"80,2.25,2.25"),
            (0.5, b"82.35,4.5,6.75"),
            (1.0, b",0,6.75"),
            (2.5, b",0,6.75"),
            (4.0, b",0,6.75"),
            (5.0, b"nan,3.75,10.5"),
            (5.5, b",0,10.5"),
            (7.0, b",0,10.5"),
            (8.5, b",0,10.5"),
            (9.0, b"95,1.25,11.75"),
            (9.5, b"96,0,11.75"),
            (10.0, None),
        ]:
            clock.now = when
            yield report
        assert offline_request.wait(2)
        raise KeyboardInterrupt

    def recognize(now: float) -> Result | None:
        if now == 5.0:
            return 1, Match(ENROLLMENT, "Alice")
        if now == 5.5:
            return 2, None
        return None

    with (
        patch.dict("os.environ", {}, clear=True),
        patch(
            "sys.argv",
            ["monitor", "--env-file", str(env_file), "--vision-config", "fixture.json"],
        ),
        patch(
            "purifier.arduino_monitor.Settings.load",
            return_value=SimpleNamespace(
                shutter_unverified=False,
                recognition_deadline_s=5.5,
            ),
        ),
        patch("purifier.arduino_monitor.RecognitionProcess") as recognition,
        patch("purifier.arduino_monitor.open_port", return_value=nullcontext(0)),
        patch("purifier.arduino_monitor.read_reports", reports),
        patch(
            "purifier.arduino_monitor.time",
            SimpleNamespace(monotonic=lambda: clock.now),
        ),
        patch(
            "purifier.arduino_monitor.datetime",
            SimpleNamespace(
                now=lambda _tz: datetime(2026, 11, 26, tzinfo=UTC)
                + timedelta(seconds=clock.now)
            ),
        ),
        patch(
            "purifier.arduino_monitor.DispenseOutbox",
            lambda settings, log: DispenseOutbox(
                settings, log, path=path, send=offline
            ),
        ),
    ):
        recognition.return_value.start.return_value = True
        recognition.return_value.poll.side_effect = recognize
        assert main() == 0

    summaries = capsys.readouterr().out.splitlines()
    assert summaries == [
        "User: Alice | TDS estimate: 82.3 ppm | Dispensed: 6.8 mL",
        "User: Guest | TDS estimate: unavailable | Dispensed: 3.8 mL",
        "User: Guest | TDS estimate: 96.0 ppm |"
        " Incomplete pour (serial timeout): 1.2 mL",
    ]
    rows = read_outbox(
        path, "SELECT payload, failure_reason FROM arduino_dispenses ORDER BY rowid"
    )
    assert len(rows) == 2
    assert rows[0][1] == "HTTP 503 Service Unavailable"
    recognized, guest = [json.loads(payload) for payload, _ in rows]
    assert recognized["recognized_user_id"] == ENROLLMENT
    assert recognized["volume_ml"] == 6.75
    assert recognized["tds_ppm"] == 82.35
    assert recognized["start_ts"] == "2026-11-26T00:00:00Z"
    assert recognized["end_ts"] == "2026-11-26T00:00:00.500000Z"
    assert guest["recognized_user_id"] is None
    assert guest["volume_ml"] == 3.75
    assert guest["tds_ppm"] is None
    for payload in (recognized, guest):
        assert payload["start_ts"].endswith("Z")
        assert payload["end_ts"].endswith("Z")
        assert payload["device_id"] == DEVICE
        assert UUID(payload["event_id"]).version == 4
    assert guest["start_ts"] == guest["end_ts"]

    committed: dict[str, str] = {}
    deliveries: list[str] = []

    def restored(_settings: DeliverySettings, payload: str) -> DeliveryResult:
        event_id = json.loads(payload)["event_id"]
        deliveries.append(event_id)
        if event_id in committed:
            assert committed[event_id] == payload
            return DeliveryResult(200, committed=True)
        committed[event_id] = payload
        raise TimeoutError("response lost after commit")

    settings = DeliverySettings.load({}, env_file=env_file)
    assert settings
    outbox = DispenseOutbox(settings, pytest.fail, path=path, send=restored)
    try:
        deadline = time.monotonic() + 6
        while read_outbox(path, "SELECT 1 FROM arduino_dispenses"):
            assert time.monotonic() < deadline
            time.sleep(0.02)
    finally:
        outbox.close()
    assert committed.keys() == {recognized["event_id"], guest["event_id"]}
    assert len(deliveries) == 4


def test_permanent_http_rejection_is_retained_and_storage_failures_are_logged(
    tmp_path: Path,
) -> None:
    settings = DeliverySettings.load(ENV)
    assert settings
    messages: list[str] = []
    outbox = DispenseOutbox(
        settings,
        messages.append,
        path=tmp_path / "outbox.sqlite3",
        send=lambda *_: DeliveryResult(409),
    )
    fields = dict(
        recognized_user_id=None,
        start_ts="2026-11-26T00:00:00Z",
        end_ts="2026-11-26T00:00:00Z",
        volume_ml=2.25,
        tds_ppm=None,
    )
    try:
        outbox.enqueue(**fields)
        deadline = time.monotonic() + 2
        while not messages:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert read_outbox(
            outbox.path, "SELECT rejected, failure_reason FROM arduino_dispenses"
        ) == [(1, "HTTP 409 Conflict")]
    finally:
        outbox.close()

    with patch.object(
        outbox, "database", side_effect=sqlite3.OperationalError("disk full")
    ):
        outbox.enqueue(**fields)
    assert "Unable to persist Arduino dispense" in messages[-1]
    assert "disk full" in messages[-1]


def test_delivery_settings_are_optional_and_require_all_three_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert DeliverySettings.load({}) is None
    with pytest.raises(ValueError, match="Set all of AQUASENSE_BACKEND_URL"):
        DeliverySettings.load({"AQUASENSE_DEVICE_ID": DEVICE})
    with pytest.raises(ValueError, match="HTTPS origin"):
        DeliverySettings.load({**ENV, "AQUASENSE_BACKEND_URL": "http://test.invalid"})


def test_default_env_file_and_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text(
        "# Arduino delivery\n"
        "AQUASENSE_BACKEND_URL = 'https://render.example' # Render origin\n"
        f'AQUASENSE_DEVICE_ID="{DEVICE}"\n'
        "AQUASENSE_DEVICE_TOKEN=file-token\n"
        "UNRELATED_SETTING=ignored\n"
    )
    settings = DeliverySettings.load({"AQUASENSE_DEVICE_TOKEN": "override-token"})
    expected = DeliverySettings("render.example", None, DEVICE, "override-token")
    assert settings == expected


def test_explicit_env_file_errors_keep_token_private(tmp_path: Path) -> None:
    env_file = tmp_path / "arduino.env"
    with pytest.raises(FileNotFoundError):
        DeliverySettings.load(ENV, env_file=env_file)

    env_file.write_text("AQUASENSE_DEVICE_TOKEN='private-value\n")
    with pytest.raises(ValueError, match="AQUASENSE_DEVICE_TOKEN.*line 1") as error:
        DeliverySettings.load(ENV, env_file=env_file)
    assert "private-value" not in str(error.value)
