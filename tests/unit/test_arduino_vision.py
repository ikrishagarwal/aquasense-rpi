"""Simulate the Arduino monitor, privacy inputs, and isolated recognition."""

import json
import tempfile
import time
import unittest
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from purifier.arduino_monitor import PourMonitor
from purifier.vision.arduino_capture import FaceFrame, TimedCamera, identify_pour
from purifier.vision.arduino_worker import RecognitionProcess, Settings


class Clock:
    now = 0.0

    def read(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.now += delay


class Burst:
    def __init__(self, started_at: float, closes_at: float = float("inf")) -> None:
        self.clock = Clock()
        self.clock.now = started_at
        self.closes_at = closes_at
        self.frames: list[float] = []
        self.opened = 0
        self.closed = 0
        self.embedded: list[int] = []
        self.camera = TimedCamera(
            self.open,
            lambda: self.clock.now < self.closes_at,
            started_at + 1.0,
            started_at + 5.0,
            self.clock.read,
            self.clock.sleep,
        )

    @contextmanager
    def open(self, check: Callable[[], None]) -> Iterator["Burst"]:
        check()
        self.opened += 1
        try:
            yield self
        finally:
            self.closed += 1

    def capture_array(self, stream: str) -> int:
        assert stream == "main"
        self.frames.append(self.clock.now)
        return len(self.frames)

    def quality(self, frame: FaceFrame) -> float:
        return float(frame.image)

    def embedding(self, frame: FaceFrame) -> list[float]:
        assert self.closed == 1
        self.embedded.append(frame.image)
        return [1.0, 0.0]


class FakeRecognition:
    def __init__(self) -> None:
        self.jobs: list[tuple[int, float]] = []
        self.results: deque[tuple[int, str | None]] = deque()
        self.cancellations: list[int] = []

    def start(self, pour_id: int, started_at: float) -> bool:
        self.jobs.append((pour_id, started_at))
        return True

    def poll(self, now: float) -> tuple[int, str | None] | None:
        return self.results.popleft() if self.results else None

    def cancel(self, pour_id: int) -> None:
        self.cancellations.append(pour_id)


class HangOnce:
    """Top-level callable so the test exercises the production spawn path."""

    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __call__(self, started_at: float) -> str:
        if not self.marker.exists():
            self.marker.touch()
            while True:
                time.sleep(60)
        return "alice"


class ArduinoVisionTests(unittest.TestCase):
    def test_simulated_burst_matches_once_across_a_merged_pour(self) -> None:
        messages: list[str] = []
        recognition = FakeRecognition()
        monitor = PourMonitor(messages.append, recognition)
        monitor.receive(b"80,2,2", 10.0)
        monitor.receive(b"81,0,2", 10.5)

        [(pour_id, started_at)] = recognition.jobs
        burst = Burst(started_at)
        enrolled = {"alice": [[1, 0]], "bob": [[0, 1]]}
        user = identify_pour(burst.camera, burst, enrolled, 0.60, 0.05)
        self.assertEqual(user, "alice")
        self.assertEqual(burst.opened, 1)
        for actual, expected in zip(burst.frames, (11.0, 11.4, 11.8), strict=True):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(burst.embedded, [3])
        recognition.results.append((pour_id, user))
        monitor.receive(b",4,6", 12.0)
        monitor.receive(b"82,0,6", 12.5)
        monitor.receive(b",0,6", 14.0)
        monitor.receive(b"nan,0,6", 15.5)

        self.assertEqual(recognition.jobs, [(pour_id, 10.0)])
        self.assertEqual(
            messages, ["User: alice | TDS estimate: 82.0 ppm | Dispensed: 6.0 mL"]
        )

    def test_privacy_and_ambiguous_frames_return_guest(self) -> None:
        cases = [(0.0, 0, 0), (1.2, 1, 1), (float("inf"), 1, 3)]
        for closes_at, opens, frames in cases:
            with self.subTest(closes_at=closes_at):
                burst = Burst(0.0, closes_at)
                enrolled = {"alice": [[1, 0]], "bob": [[1, 0]]}
                self.assertIsNone(
                    identify_pour(burst.camera, burst, enrolled, 0.60, 0.05)
                )
                self.assertEqual(burst.opened, opens)
                self.assertEqual(burst.closed, opens)
                self.assertEqual(len(burst.frames), frames)
                self.assertEqual(burst.embedded, [3] if frames == 3 else [])

    def test_completed_pour_waits_without_blocking_next_pour(self) -> None:
        messages: list[str] = []
        recognition = FakeRecognition()
        monitor = PourMonitor(messages.append, recognition)
        for now, line in (
            (0.0, b",2,2"),
            (0.5, b",0,2"),
            (2.0, b",0,2"),
            (3.5, b"90,4,6"),
        ):
            monitor.receive(line, now)
        self.assertEqual(messages, [])
        self.assertEqual(recognition.jobs, [(1, 0.0)])
        recognition.results.append((1, "alice"))
        monitor.tick(4.0)
        monitor.receive(b"91,0,6", 4.5)
        recognition.results.append((1, "late"))
        monitor.receive(b",0,6", 6.0)
        monitor.receive(b",0,6", 7.5)
        self.assertEqual(
            messages,
            [
                "User: alice | TDS estimate: unavailable | Dispensed: 2.0 mL",
                "User: Guest | TDS estimate: 91.0 ppm | Dispensed: 4.0 mL",
            ],
        )

    def test_disconnect_cancels_and_stale_result_cannot_label_next_pour(self) -> None:
        messages: list[str] = []
        recognition = FakeRecognition()
        monitor = PourMonitor(messages.append, recognition)
        monitor.receive(b"80,2,2", 0.0)
        monitor.interrupt("serial connection lost")
        self.assertEqual(recognition.cancellations, [1])
        monitor.receive(b"90,4,4", 1.0)
        recognition.results.append((1, "late"))
        monitor.receive(b",0,4", 1.5)
        recognition.results.append((2, None))
        monitor.receive(b",0,4", 3.0)
        monitor.receive(b",0,4", 4.5)
        self.assertEqual(
            messages,
            [
                "User: Guest | TDS estimate: 80.0 ppm | "
                "Incomplete pour (serial connection lost): 2.0 mL",
                "User: Guest | TDS estimate: 90.0 ppm | Dispensed: 4.0 mL",
            ],
        )

    def test_hung_worker_is_killed_at_deadline_and_next_pour_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "capture-entered"
            worker = RecognitionProcess(HangOnce(marker))
            try:
                messages: list[str] = []
                monitor = PourMonitor(messages.append, worker)
                start = time.monotonic()
                monitor.receive(b"80,2,2", start)
                until = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < until:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                for offset in (0.5, 2.0, 3.5):
                    monitor.receive(b",0,2", start + offset)
                self.assertEqual(messages, [])
                monitor.tick(start + 5.0)
                self.assertEqual(
                    messages,
                    ["User: Guest | TDS estimate: 80.0 ppm | Dispensed: 2.0 mL"],
                )
                assert worker.process is not None
                worker.process.join(timeout=3)
                self.assertFalse(worker.process.is_alive())

                second = max(time.monotonic(), start + 5.0)
                monitor.receive(b"90,4,6", second)
                until = time.monotonic() + 3
                while worker.job is not None and time.monotonic() < until:
                    monitor.tick(second + 0.1)
                    time.sleep(0.01)
                self.assertIsNone(worker.job)
                for offset in (0.5, 2.0, 3.5):
                    monitor.receive(b",0,6", second + offset)
                self.assertEqual(
                    messages[-1],
                    "User: alice | TDS estimate: 90.0 ppm | Dispensed: 4.0 mL",
                )
                self.assertEqual(len(messages), 2)
            finally:
                worker.close()

    def test_settings_fail_closed_and_enrollment_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config_path = directory / "vision.json"
            raw = {
                "yunet": "yunet.onnx",
                "sface": "sface.onnx",
                "directory": "enroll",
                "key": "key",
                "proximity": {"pin_bcm": 22, "eligible_high": True, "verified": True},
                "shutter": {"pin_bcm": 25, "eligible_high": False, "verified": False},
            }
            config_path.write_text(json.dumps(raw))
            settings = Settings.load(config_path)
            self.assertIsNotNone(settings.proximity)
            self.assertIsNone(settings.shutter)
            self.assertEqual((settings.threshold, settings.margin), (0.60, 0.05))
            raw["margin"] = float("nan")
            config_path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                Settings.load(config_path)

            key = directory / "key"
            key.touch(mode=0o600)
            key.write_bytes(b"a" * 32)
            store = settings.store()
            user = str(uuid4())
            sample = [1.0] + [0.0] * 127
            store.enroll(user, [sample] * 3, label="alice")
            encrypted = next(store.directory.glob("*.enc"))
            self.assertNotIn(b"alice", encrypted.read_bytes())
            self.assertEqual(store.load_labeled(user), ([sample] * 3, "alice"))
            self.assertEqual(store.load(user), [sample] * 3)
            store.delete(user)
            self.assertFalse(encrypted.exists())
