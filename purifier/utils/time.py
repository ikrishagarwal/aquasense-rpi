"""Injected monotonic time for deadlines; UTC for records."""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def utcnow(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class FakeClock:
    elapsed: float = 0
    wall: datetime = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.elapsed

    def utcnow(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError('monotonic time cannot go backward')
        self.elapsed += seconds
        self.wall += timedelta(seconds=seconds)
