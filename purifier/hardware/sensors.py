"""Sensor conversion and quality logic, independent of board libraries."""

import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generic, TypeVar

from purifier.core.state import Quality, Reading, TankLevel
from purifier.hardware.interfaces import ADC
from purifier.utils.time import Clock

T = TypeVar('T')


class ADCManager:
    def __init__(self, adapter: ADC, supply_v: float = 3.3) -> None:
        self.adapter, self.supply_v = adapter, supply_v
        self._lock = threading.Lock()

    def read_voltage(self, channel: int) -> float:
        if channel not in range(4):
            raise ValueError('invalid ADC channel')
        with self._lock:
            value = self.adapter.read_voltage(channel)
        if not math.isfinite(value) or not 0 <= value <= self.supply_v:
            raise ValueError('ADC input outside supply rails')
        return value


@dataclass(frozen=True)
class LinearCalibration:
    slope: float
    intercept: float
    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.slope, self.intercept, self.minimum, self.maximum)) or self.minimum >= self.maximum:
            raise ValueError('invalid calibration')

    def convert(self, voltage: float) -> float:
        result = self.slope * voltage + self.intercept
        if not math.isfinite(result) or not self.minimum <= result <= self.maximum:
            raise ValueError('calibrated reading outside validated range')
        return result


class AnalogSensor:
    def __init__(self, adc: ADCManager, channel: int, clock: Clock, unit: str,
                 calibration: LinearCalibration | None = None) -> None:
        self.adc, self.channel, self.clock = adc, channel, clock
        self.unit, self.calibration = unit, calibration

    def read(self) -> Reading[float]:
        value = None
        try:
            voltage = self.adc.read_voltage(self.channel)
            quality = Quality.UNCALIBRATED
            if self.calibration:
                value = self.calibration.convert(voltage)
                quality = Quality.VALID
        except OSError:
            quality = Quality.SENSOR_ERROR
        except ValueError:
            quality = Quality.OUT_OF_RANGE
        return Reading(value, self.unit, self.clock.utcnow(), self.clock.monotonic(), quality)


class DS18B20:
    def __init__(self, path: Path, clock: Clock) -> None:
        self.path, self.clock = path, clock

    def read(self) -> Reading[float]:
        value = None
        quality = Quality.SENSOR_ERROR
        try:
            with self.path.open('r') as stream:
                text = stream.read(4097)
            lines = text.splitlines()
            if len(text) > 4096 or len(lines) != 2 or not lines[0].endswith('YES'):
                raise ValueError('invalid temperature CRC')
            value = int(lines[1].split('t=')[1]) / 1000
            if not -55 <= value <= 125:
                value, quality = None, Quality.OUT_OF_RANGE
            else:
                quality = Quality.VALID
        except (OSError, ValueError, IndexError):
            value = None
        return Reading(value, 'C', self.clock.utcnow(), self.clock.monotonic(), quality)


class DebouncedTank:
    def __init__(self, clock: Clock, ok_level: bool, debounce_s: float) -> None:
        if debounce_s <= 0:
            raise ValueError('positive debounce required')
        self.clock, self.ok_level, self.debounce_s = clock, ok_level, debounce_s
        self.candidate: bool | None = None
        self.since = clock.monotonic()

    def update(self, electrical: bool | None) -> Reading[TankLevel]:
        now = self.clock.monotonic()
        if electrical is None or electrical != self.candidate:
            self.candidate, self.since = electrical, now
        # Unsafe transitions take effect immediately; OK requires stable input.
        level = TankLevel.UNKNOWN
        quality = Quality.SENSOR_ERROR
        if electrical is not None:
            quality = Quality.VALID
            if electrical != self.ok_level:
                level = TankLevel.LOW
            elif now - self.since >= self.debounce_s:
                level = TankLevel.OK
        return Reading(level, 'threshold', self.clock.utcnow(), now, quality)


class Poller(Generic[T]):
    """One due read per step; failure backoff is bounded and independent per sensor.

    Physical blocking adapters must be hosted outside the event loop. This class
    supplies scheduling policy, not an executor for unverified hardware APIs.
    """
    def __init__(self, read: Callable[[], Reading[T]], clock: Clock, interval: float,
                 unit: str, max_backoff: float = 60) -> None:
        if interval <= 0 or max_backoff < interval:
            raise ValueError('invalid polling interval')
        self.read, self.clock, self.interval, self.unit = read, clock, interval, unit
        self.max_backoff, self.backoff, self.next_due = max_backoff, interval, 0.0
        self.latest: Reading[T] | None = None

    def step(self) -> Reading[T] | None:
        now = self.clock.monotonic()
        if now < self.next_due:
            return self.latest
        try:
            self.latest = self.read()
        except (OSError, ValueError):
            self.latest = Reading(None, self.unit, self.clock.utcnow(), now, Quality.SENSOR_ERROR)
        if self.latest.quality == Quality.SENSOR_ERROR:
            self.backoff = min(self.max_backoff, self.backoff * 2)
        else:
            self.backoff = self.interval
        self.next_due = now + self.backoff
        return self.latest
