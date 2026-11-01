"""Pure drain eligibility; unknown or stale state is never safe."""

import math
from dataclasses import dataclass

from purifier.core.state import Reading, TankLevel


@dataclass(frozen=True)
class Conditions:
    dispensing: bool
    last_flow_at: float
    tank: Reading[TankLevel]
    physical_enable: bool
    driver_healthy: bool
    conflicting_operation: bool
    storage_healthy: bool
    cutoff_verified: bool
    control_healthy: bool = True


def rejection(state: Conditions, now: float, quiet_s: float, max_age_s: float) -> str | None:
    if not all(math.isfinite(v) for v in (now, state.last_flow_at, quiet_s, max_age_s)) or quiet_s <= 0 or max_age_s <= 0:
        return 'invalid_timing'
    checks = (
        (state.dispensing, 'dispensing'),
        (now - state.last_flow_at < quiet_s, 'quiet_period'),
        (not state.tank.fresh(now, max_age_s), 'tank_stale_or_fault'),
        (state.tank.value != TankLevel.OK, 'tank_unsafe'),
        (not state.physical_enable, 'physical_disabled'),
        (not state.driver_healthy, 'driver_fault'),
        (state.conflicting_operation, 'conflicting_operation'),
        (not state.storage_healthy, 'storage_fault'),
        (not state.cutoff_verified, 'cutoff_unverified'),
        (not state.control_healthy, 'control_fault'),
    )
    return next((reason for blocked, reason in checks if blocked), None)
