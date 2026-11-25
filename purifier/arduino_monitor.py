"""Standalone Linux serial monitor for the Arduino flow and TDS sketch."""

import argparse
import math
import os
import select
import signal
import sys
import termios
import time
import tty
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from purifier.vision.arduino_worker import RecognitionProcess, Recognizer, Settings

IDLE_SECONDS = 3.0
REPORT_TIMEOUT_SECONDS = 2.0
RECONNECT_SECONDS = 1.0
MAX_REPORT_BYTES = 128
VISION_POLL_SECONDS = 0.05


def nonnegative_number(field: bytes) -> float | None:
    try:
        value = float(field)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def volume_tolerance(total: float) -> float:
    # Three two-decimal fields plus one float32 addition in the sketch.
    return 0.016 + total * 2**-24


@dataclass(frozen=True)
class Report:
    tds_ppm: float | None
    interval_ml: float
    total_ml: float

    @classmethod
    def parse(cls, line: bytes) -> "Report | None":
        fields = line.split(b",")
        if len(fields) != 3:
            return None
        tds, interval, total = map(nonnegative_number, fields)
        if interval is None or total is None:
            return None
        if interval - total > volume_tolerance(total):
            return None
        return cls(tds, interval, total)


class Recognition(Protocol):
    def start(self, pour_id: int, started_at: float) -> bool: ...
    def poll(self, now: float) -> tuple[int, str | None] | None: ...
    def cancel(self, pour_id: int) -> None: ...


@dataclass
class Pour:
    id: int
    waiting: bool = False
    label: str | None = None
    volume_ml: float = 0.0
    tds_ppm: float | None = None
    zero_since: float | None = None


class PourMonitor:
    def __init__(
        self, emit: Callable[[str], None], recognition: Recognition | None = None
    ) -> None:
        self.emit = emit
        self.recognition = recognition
        self.previous_total: float | None = None
        self.pour: Pour | None = None
        self.pending: Pour | None = None
        self.next_id = 1

    def summarize(self, pour: Pour, reason: str | None = None) -> None:
        tds = "unavailable" if pour.tds_ppm is None else f"{pour.tds_ppm:.1f} ppm"
        user = pour.label or "Guest"
        volume = f"{pour.volume_ml:.1f} mL"
        dispensed = (
            f"Dispensed: {volume}"
            if reason is None
            else f"Incomplete pour ({reason}): {volume}"
        )
        self.emit(f"User: {user} | TDS estimate: {tds} | {dispensed}")

    def tick(self, now: float) -> None:
        if self.recognition is None:
            return
        result = self.recognition.poll(now)
        if result is None:
            return
        pour_id, label = result
        if self.pending is not None and self.pending.id == pour_id:
            self.pending.label = label
            self.summarize(self.pending)
            self.pending = None
        elif self.pour is not None and self.pour.id == pour_id and self.pour.waiting:
            self.pour.label = label
            self.pour.waiting = False

    def finish(self, *, reason: str | None = None) -> None:
        pour, self.pour = self.pour, None
        if pour is None:
            return
        if reason is not None:
            if self.recognition is not None:
                self.recognition.cancel(pour.id)
            pour.label = None
            self.summarize(pour, reason)
        elif pour.waiting:
            self.pending = pour
        else:
            self.summarize(pour)

    def interrupt(self, reason: str) -> None:
        self.finish(reason=reason)
        self.previous_total = None

    def stop(self) -> None:
        self.interrupt("monitor stopped")
        if self.pending is not None:
            if self.recognition is not None:
                self.recognition.cancel(self.pending.id)
            self.summarize(self.pending)
            self.pending = None

    def receive(self, line: bytes | None, now: float) -> None:
        self.tick(now)
        if line is None:
            self.interrupt("serial timeout")
            return
        report = Report.parse(line)
        if report is None:
            self.interrupt("malformed flow report")
            return

        previous_total = self.previous_total
        if previous_total is not None:
            delta = report.total_ml - previous_total
            first_since_reset = (
                previous_total > 0 and report.total_ml == report.interval_ml
            )
            if delta < 0 or first_since_reset:
                self.interrupt("Arduino reset or decreasing total")
            elif report.interval_ml == 0 and delta != 0:
                self.interrupt("inconsistent cumulative total")
                return
            elif abs(delta - report.interval_ml) > volume_tolerance(report.total_ml):
                self.interrupt("inconsistent cumulative total")
                return
        self.previous_total = report.total_ml

        pour = self.pour
        idle = (
            pour is not None
            and pour.zero_since is not None
            and now - pour.zero_since >= IDLE_SECONDS
        )
        if report.interval_ml > 0:
            if idle:
                self.finish()
                pour = None
            if pour is None:
                pour = Pour(self.next_id)
                self.next_id += 1
                self.pour = pour
                if self.recognition is not None and self.pending is None:
                    pour.waiting = self.recognition.start(pour.id, now)
            pour.volume_ml += report.interval_ml
            pour.zero_since = None
        elif pour is not None and pour.zero_since is None:
            pour.zero_since = now

        if pour is not None and report.tds_ppm is not None:
            pour.tds_ppm = report.tds_ppm
        if idle and report.interval_ml == 0:
            self.finish()


@contextmanager
def open_port(path: str) -> Iterator[int]:
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        tty.setraw(fd, termios.TCSANOW)
        settings = termios.tcgetattr(fd)
        settings[0] &= ~(termios.IXOFF | termios.IXANY)
        settings[2] &= ~(termios.CSTOPB | termios.CRTSCTS)
        settings[2] |= termios.CLOCAL | termios.CREAD
        settings[4] = settings[5] = termios.B9600
        termios.tcsetattr(fd, termios.TCSANOW, settings)
        yield fd
    finally:
        os.close(fd)


def read_reports(
    fd: int,
    timeout_s: float = REPORT_TIMEOUT_SECONDS,
    *,
    on_tick: Callable[[], None] | None = None,
) -> Iterator[bytes | None]:
    """Yield complete lines, or None after a period without a complete line."""
    pending = b""
    discarding = False
    deadline = time.monotonic() + timeout_s
    while True:
        if on_tick is not None:
            on_tick()
        remaining = max(0.0, deadline - time.monotonic())
        if on_tick is not None:
            remaining = min(remaining, VISION_POLL_SECONDS)
        ready = select.select([fd], [], [], remaining)[0] if remaining else []
        if not ready:
            if time.monotonic() < deadline:
                continue
            discarding = discarding or bool(pending)
            pending = b""
            yield None
            deadline = time.monotonic() + timeout_s
            continue

        try:
            chunk = os.read(fd, 256)
        except BlockingIOError:
            continue
        if not chunk:
            raise OSError("serial port disconnected")
        lines = chunk.split(b"\n")
        for part in lines[:-1]:
            if discarding or len(pending) + len(part) > MAX_REPORT_BYTES:
                yield b""
            else:
                yield pending + part
            pending = b""
            discarding = False
            deadline = time.monotonic() + timeout_s

        if not discarding:
            pending += lines[-1]
            if len(pending) > MAX_REPORT_BYTES:
                pending = b""
                discarding = True


def stop_on_signal(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyACM0", help="Arduino serial device")
    parser.add_argument("--vision-config", type=Path, help="Opt-in Arduino vision JSON")
    args = parser.parse_args()

    recognition = None
    if args.vision_config is not None:
        try:
            settings = Settings.load(args.vision_config)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        if not settings.shutter_unverified:
            recognition = RecognitionProcess(Recognizer(settings).recognize)
    monitor = PourMonitor(lambda message: print(message, flush=True), recognition)

    def tick() -> None:
        monitor.tick(time.monotonic())

    on_tick = tick if recognition is not None else None
    previous_handler = signal.signal(signal.SIGTERM, stop_on_signal)
    reported_error = False
    try:
        while True:
            try:
                with open_port(args.port) as fd:
                    for line in read_reports(fd, on_tick=on_tick):
                        if line is not None:
                            reported_error = False
                        monitor.receive(line, time.monotonic())
            except (OSError, termios.error) as error:
                monitor.interrupt("serial connection lost")
                if not reported_error:
                    print(
                        f"Arduino serial error on {args.port}: {error}", file=sys.stderr
                    )
                    reported_error = True
                retry_at = time.monotonic() + RECONNECT_SECONDS
                while (remaining := retry_at - time.monotonic()) > 0:
                    tick()
                    time.sleep(min(remaining, VISION_POLL_SECONDS))
    except KeyboardInterrupt:
        monitor.stop()
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        if recognition is not None:
            recognition.close()


if __name__ == "__main__":
    raise SystemExit(main())
