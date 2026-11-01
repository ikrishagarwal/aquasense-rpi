"""Explicit simulation factory. No automatic hardware fallback."""

from purifier.hardware.valve import DisabledValve
from purifier.simulation.devices import SimulatedSensor
from purifier.utils.time import Clock


def optional_sensor(mode: str, clock: Clock) -> SimulatedSensor[float]:
    if mode != 'simulation':
        raise RuntimeError('physical sensor adapters are not approved')
    return SimulatedSensor(clock)


def disabled_valve(mode: str) -> DisabledValve:
    if mode != 'simulation':
        raise RuntimeError('physical valve adapter is not approved')
    return DisabledValve()
