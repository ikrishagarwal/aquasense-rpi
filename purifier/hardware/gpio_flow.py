"""Thread-safe bounded edge buffer; GPIO library binding awaits bench selection."""

import math
import threading
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class PulseBatch:
    times: deque[float]
    overflow_count: int
    total: int


class PulseCounter:
    def __init__(self, capacity: int = 4096) -> None:
        if capacity < 1:
            raise ValueError('positive capacity required')
        self.capacity = capacity
        self._lock = threading.Lock()
        self._times: deque[float] = deque()
        self._overflow = 0
        self._total = 0
        self._enabled = True

    def pulse(self, at: float) -> None:
        if not math.isfinite(at):
            raise ValueError('invalid pulse timestamp')
        with self._lock:
            if not self._enabled:
                return
            self._total += 1
            if len(self._times) < self.capacity:
                self._times.append(at)
            else:
                self._overflow += 1

    def take(self) -> PulseBatch:
        with self._lock:
            batch = PulseBatch(self._times, self._overflow, self._total)
            self._times = deque()
            self._overflow = 0
            return batch

    def stop(self) -> None:
        with self._lock:
            self._enabled = False
