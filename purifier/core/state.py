"""Portable domain values shared by simulation and hardware boundaries."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar('T')


class Quality(str, Enum):
    VALID = 'valid'
    STALE = 'stale'
    UNCALIBRATED = 'uncalibrated'
    OUT_OF_RANGE = 'out_of_range'
    SENSOR_ERROR = 'sensor_error'


class TankLevel(str, Enum):
    LOW = 'LOW'
    OK = 'OK'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True)
class Reading(Generic[T]):
    value: T | None
    unit: str
    timestamp: datetime
    sampled_at: float
    quality: Quality

    def fresh(self, now: float, max_age: float) -> bool:
        return (self.value is not None and self.quality == Quality.VALID
                and 0 <= now - self.sampled_at <= max_age)

    def wire(self, now: float, max_age: float) -> dict[str, object]:
        fresh = self.fresh(now, max_age)
        quality = self.quality
        if quality == Quality.VALID and not fresh:
            quality = Quality.STALE
        return {'value': self.value if fresh else None, 'unit': self.unit,
                'timestamp': self.timestamp.isoformat(), 'quality': quality.value}


@dataclass(frozen=True)
class Recognition:
    request_id: str
    session_id: str
    status: str
    user_id: str = 'guest'
    similarity: float | None = None
