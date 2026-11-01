"""Small hardware ports; importing these never loads Pi dependencies."""

from typing import Protocol

from purifier.core.state import Reading, Recognition, TankLevel


class Flow(Protocol):
    def pulse(self, at: float) -> None: ...


class ADC(Protocol):
    def read_voltage(self, channel: int) -> float: ...


class Temperature(Protocol):
    def read(self) -> Reading[float]: ...


class Tank(Protocol):
    def read(self) -> Reading[TankLevel]: ...


class Proximity(Protocol):
    def read(self) -> Reading[bool]: ...


class Recognizer(Protocol):
    async def identify(self, request_id: str, session_id: str) -> Recognition: ...


class Valve(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
