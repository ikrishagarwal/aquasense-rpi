"""Deterministic devices: failure is explicit and never imports real drivers."""

from dataclasses import dataclass
from typing import Generic, TypeVar

from purifier.core.state import Quality, Reading, Recognition
from purifier.utils.time import Clock

T = TypeVar('T')


@dataclass
class SimulatedSensor(Generic[T]):
    clock: Clock
    value: T | None = None
    unit: str = ''
    quality: Quality = Quality.SENSOR_ERROR
    stuck: Reading[T] | None = None
    failed: bool = False

    def read(self) -> Reading[T]:
        if self.failed:
            raise OSError('simulated unplugged sensor')
        if self.stuck is not None:
            return self.stuck
        return Reading(self.value, self.unit, self.clock.utcnow(),
                       self.clock.monotonic(), self.quality)


@dataclass
class SimulatedValve:
    opened: bool = False
    open_count: int = 0
    failed: bool = False

    def open(self) -> None:
        if self.failed:
            raise OSError('simulated driver failure')
        self.opened = True
        self.open_count += 1

    def close(self) -> None:
        self.opened = False


@dataclass
class SimulatedRecognition:
    user_id: str = 'guest'
    failed: bool = False

    async def identify(self, request_id: str, session_id: str) -> Recognition:
        if self.failed:
            raise OSError('simulated absent vision')
        return Recognition(request_id, session_id,
                           'guest' if self.user_id == 'guest' else 'matched', self.user_id)
