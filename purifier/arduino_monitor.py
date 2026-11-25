"""Standalone Linux serial monitor for the Arduino flow and TDS sketch."""

import argparse
import math
import os
import select
import sys
import termios
import time
import tty
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

IDLE_SECONDS = 3.0
REPORT_TIMEOUT_SECONDS = 2.0
RECONNECT_SECONDS = 1.0
MAX_REPORT_BYTES = 128


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


class PourMonitor:
    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self.previous_total: float | None = None
        self.volume_ml = 0.0
        self.tds_ppm: float | None = None
        self.zero_since: float | None = None

    def finish(self, *, reason: str | None = None) -> None:
        if self.volume_ml > 0:
            tds = "unavailable" if self.tds_ppm is None else f"{self.tds_ppm:.1f} ppm"
            if reason is None:
                message = f"Dispensed {self.volume_ml:.1f} mL"
            else:
                message = f"Incomplete pour ({reason}): {self.volume_ml:.1f} mL"
            self.emit(f"{message} | TDS estimate: {tds}")
        self.volume_ml = 0.0
        self.tds_ppm = None
        self.zero_since = None

    def interrupt(self, reason: str) -> None:
        self.finish(reason=reason)
        self.previous_total = None

    def receive(self, line: bytes | None, now: float) -> None:
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

        idle = self.zero_since is not None and now - self.zero_since >= IDLE_SECONDS
        if report.interval_ml > 0:
            if idle:
                self.finish()
            if self.volume_ml == 0:
                self.emit("Flow started")
            self.volume_ml += report.interval_ml
            self.zero_since = None
        elif self.volume_ml > 0 and self.zero_since is None:
            self.zero_since = now

        if self.volume_ml > 0 and report.tds_ppm is not None:
            self.tds_ppm = report.tds_ppm
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
    fd: int, timeout_s: float = REPORT_TIMEOUT_SECONDS
) -> Iterator[bytes | None]:
    """Yield complete lines, or None after a period without a complete line."""
    pending = b""
    discarding = False
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        ready = select.select([fd], [], [], remaining)[0] if remaining else []
        if not ready:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyACM0", help="Arduino serial device")
    args = parser.parse_args()

    monitor = PourMonitor(lambda message: print(message, flush=True))
    reported_error = False
    try:
        while True:
            try:
                with open_port(args.port) as fd:
                    for line in read_reports(fd):
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
                time.sleep(RECONNECT_SECONDS)
    except KeyboardInterrupt:
        monitor.interrupt("monitor stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
