"""Simulate the Arduino monitor, privacy inputs, and isolated recognition."""

import json
import tempfile
import time
import unittest
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from purifier.arduino_monitor import PourMonitor
from purifier.arduino_monitor import main as monitor_main
from purifier.vision.arduino_capture import FaceFrame, TimedCamera, identify_pour
from purifier.vision.arduino_worker import RecognitionProcess, Recognizer, Settings
from purifier.vision.arduino_worker import main as enrollment_main


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


class VisionBench:
    def __init__(self, directory: Path) -> None:
        self.config = directory / "vision.json"
        self.raw = {
            "yunet": "yunet.onnx",
            "sface": "sface.onnx",
            "directory": "enroll",
            "key": "key",
            "proximity": None,
            "shutter": {"pin_bcm": 25, "eligible_high": True, "verified": True},
        }
        self.config.write_text(json.dumps(self.raw))
        key = directory / "key"
        key.touch(mode=0o600)
        key.write_bytes(b"a" * 32)
        self.user = str(uuid4())
        self.sample = [1.0] + [0.0] * 127
        self.inputs = {25: SimpleNamespace(is_active=True)}
        self.opened_pins: list[int] = []

    @contextmanager
    def gpio_input(self, pin: int, **_kwargs: object) -> Iterator[SimpleNamespace]:
        self.opened_pins.append(pin)
        yield self.inputs[pin]

    @contextmanager
    def dependencies(self, burst: Burst) -> Iterator[MagicMock]:
        model = MagicMock()
        model.quality.side_effect = burst.quality
        model.embedding.return_value = self.sample
        modules: dict[str, SimpleNamespace | None] = {
            "cv2": SimpleNamespace(error=RuntimeError, setNumThreads=MagicMock()),
            "purifier.vision.still_identify": SimpleNamespace(
                FaceModel=MagicMock(), open_camera=burst.open
            ),
        }
        gpio_modules = {
            "gpiozero": SimpleNamespace(DigitalInputDevice=self.gpio_input),
            "gpiozero.exc": SimpleNamespace(GPIOZeroError=RuntimeError),
            "gpiozero.pins.lgpio": SimpleNamespace(LGPIOFactory=MagicMock()),
            "lgpio": SimpleNamespace(error=RuntimeError),
        }
        if self.raw["shutter"] is None and self.raw["proximity"] is None:
            modules.update(dict.fromkeys(gpio_modules))
        else:
            modules.update(gpio_modules)

        def timed_camera(
            open_camera: Callable[[Callable[[], None]], AbstractContextManager[Any]],
            eligible: Callable[[], bool],
            first_frame_at: float,
            deadline: float,
        ) -> TimedCamera:
            return TimedCamera(
                open_camera,
                eligible,
                first_frame_at,
                deadline,
                burst.clock.read,
                burst.clock.sleep,
            )

        with (
            patch.dict("sys.modules", modules),
            patch("purifier.vision.arduino_worker.TimedCamera", timed_camera),
            patch("purifier.vision.arduino_worker.FaceBurstModel", return_value=model),
            patch("purifier.vision.arduino_worker.resource.setrlimit"),
        ):
            yield model


class ArduinoVisionTests(unittest.TestCase):
    def test_flow_only_burst_matches_once_across_a_merged_pour(self) -> None:
        messages: list[str] = []
        recognition = FakeRecognition()
        monitor = PourMonitor(messages.append, recognition)
        monitor.receive(b"79,0,0", 9.5)
        self.assertEqual(recognition.jobs, [])
        monitor.receive(b"80,2,2", 10.0)
        monitor.receive(b"81,0,2", 10.5)

        [(pour_id, started_at)] = recognition.jobs
        burst = Burst(started_at)
        with tempfile.TemporaryDirectory() as tmp:
            bench = VisionBench(Path(tmp))
            bench.raw["shutter"] = None
            bench.config.write_text(json.dumps(bench.raw))
            settings = Settings.load(bench.config)
            settings.store().enroll(bench.user, [bench.sample] * 3, label="alice")
            with bench.dependencies(burst) as model:
                user = Recognizer(settings).recognize(started_at)
            self.assertEqual(bench.opened_pins, [])
            model.embedding.assert_called_once()
            self.assertEqual(model.embedding.call_args.args[0].image, 3)
        self.assertEqual(user, "alice")
        self.assertEqual(burst.opened, 1)
        for actual, expected in zip(burst.frames, (11.0, 11.4, 11.8), strict=True):
            self.assertAlmostEqual(actual, expected)
        recognition.results.append((pour_id, user))
        monitor.receive(b",4,6", 12.0)
        monitor.receive(b"82,0,6", 12.5)
        monitor.receive(b",0,6", 14.0)
        monitor.receive(b"nan,0,6", 15.5)

        self.assertEqual(recognition.jobs, [(pour_id, 10.0)])
        self.assertEqual(
            messages, ["User: alice | TDS estimate: 82.0 ppm | Dispensed: 6.0 mL"]
        )

    def test_monitor_enables_worker_with_opt_in_vision_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench = VisionBench(Path(tmp))
            for verified in (True, False, None):
                with self.subTest(verified=verified):
                    bench.raw["shutter"] = (
                        {"pin_bcm": 25, "eligible_high": True, "verified": verified}
                        if verified is not None
                        else None
                    )
                    bench.config.write_text(json.dumps(bench.raw))
                    argv = ["monitor", "--vision-config", str(bench.config)]
                    with (
                        patch("sys.argv", argv),
                        patch("purifier.arduino_monitor.RecognitionProcess") as worker,
                        patch(
                            "purifier.arduino_monitor.open_port",
                            side_effect=KeyboardInterrupt,
                        ),
                    ):
                        self.assertEqual(monitor_main(), 0)
                    self.assertEqual(worker.call_count, int(verified is not False))

    def test_automatic_capture_obeys_shutter_and_optional_proximity(self) -> None:
        for blocked_by, expected_frames in (
            ("unverified shutter", 0),
            ("closed shutter", 0),
            ("inactive proximity", 0),
            ("shutter closes during frame", 2),
        ):
            with (
                self.subTest(blocked_by=blocked_by),
                tempfile.TemporaryDirectory() as tmp,
            ):
                bench = VisionBench(Path(tmp))
                if blocked_by == "unverified shutter":
                    bench.raw["shutter"] = {
                        "pin_bcm": 25,
                        "eligible_high": True,
                        "verified": False,
                    }
                elif blocked_by == "closed shutter":
                    bench.inputs[25].is_active = False
                elif blocked_by == "inactive proximity":
                    bench.raw["proximity"] = {
                        "pin_bcm": 22,
                        "eligible_high": True,
                        "verified": True,
                    }
                    bench.inputs[22] = SimpleNamespace(is_active=False)
                bench.config.write_text(json.dumps(bench.raw))
                settings = Settings.load(bench.config)
                settings.store().enroll(bench.user, [bench.sample] * 3, label="alice")
                burst = Burst(0.0)

                def capture_and_close_shutter(
                    stream: str,
                    capture: Callable[[str], int] = burst.capture_array,
                    shutter: SimpleNamespace = bench.inputs[25],
                ) -> int:
                    frame = capture(stream)
                    if frame == 2:
                        shutter.is_active = False
                    return frame

                if expected_frames:
                    burst.capture_array = capture_and_close_shutter

                with bench.dependencies(burst) as model:
                    user = Recognizer(settings).recognize(0.0)
                self.assertIsNone(user)
                self.assertEqual(len(burst.frames), expected_frames)
                self.assertEqual(burst.opened, int(expected_frames > 0))
                self.assertEqual(burst.closed, burst.opened)
                model.embedding.assert_not_called()

    def test_manual_enrollment_without_gpio_and_with_closed_shutter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bench = VisionBench(Path(tmp))
            shutter = bench.raw["shutter"]
            bench.raw["shutter"] = None
            bench.config.write_text(json.dumps(bench.raw))
            args = ["--config", str(bench.config), "enroll", "--user-id", bench.user]
            burst = Burst(0.0)
            with (
                bench.dependencies(burst),
                patch("builtins.print"),
                patch(
                    "purifier.vision.arduino_worker.time.monotonic", burst.clock.read
                ),
            ):
                self.assertEqual(enrollment_main([*args, "--label", "alice"]), 0)

            store = Settings.load(bench.config).store()
            self.assertEqual(bench.opened_pins, [])
            self.assertEqual(len(burst.frames), 9)
            self.assertEqual((burst.opened, burst.closed), (1, 1))
            expected = ([bench.sample] * 3, "alice")
            encrypted = store.directory / f"{bench.user}.enc"
            self.assertEqual(list(store.directory.iterdir()), [encrypted])
            self.assertEqual(store.load_labeled(bench.user), expected)

            bench.raw["shutter"] = shutter
            bench.config.write_text(json.dumps(bench.raw))
            bench.inputs[25].is_active = False
            blocked_burst = Burst(0.0)
            with bench.dependencies(blocked_burst), patch("builtins.print"):
                self.assertEqual(enrollment_main(args), 1)
            self.assertEqual(blocked_burst.opened, 0)
            self.assertEqual(store.load_labeled(bench.user), expected)

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
            self.assertTrue(settings.shutter_unverified)
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
