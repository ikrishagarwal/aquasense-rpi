"""Deterministic session transitions; callers serialize ingestion and persistence."""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import uuid4


class SessionState(str, Enum):
    IDLE = 'IDLE'
    ACTIVE = 'ACTIVE'
    WAITING = 'WAITING'
    FINALIZING = 'FINALIZING'


@dataclass
class Session:
    event_id: str
    start: float
    last: float
    start_ts: datetime
    pulses: int = 0
    user_id: str = 'guest'
    recognition_status: str = 'unknown'
    confidence: float | None = None
    flags: list[str] = field(default_factory=list)


class SessionManager:
    def __init__(self, pulses_per_litre: float, merge_s: float = 3,
                 maximum_s: float = 300, calibrated: bool = False) -> None:
        if not all(math.isfinite(x) and x > 0 for x in (pulses_per_litre, merge_s, maximum_s)):
            raise ValueError('positive finite session settings required')
        if maximum_s <= merge_s:
            raise ValueError('maximum session must exceed merge timeout')
        self.factor, self.merge_s, self.maximum_s = pulses_per_litre, merge_s, maximum_s
        self.calibrated = calibrated
        self.active: Session | None = None
        self.state = SessionState.IDLE
        self.last_input = -math.inf

    def pulse(self, at: float, wall: datetime) -> dict[str, object] | None:
        if not math.isfinite(at) or at < self.last_input or wall.utcoffset() is None:
            raise ValueError('ordered monotonic pulses and aware wall time required')
        self.last_input = at
        completed = self.tick(at)
        if self.active is None:
            self.active = Session(str(uuid4()), at, at, wall.astimezone(timezone.utc))
        self.active.last = at
        self.active.pulses += 1
        self.state = SessionState.ACTIVE
        return completed

    def tick(self, now: float) -> dict[str, object] | None:
        if self.active is None:
            return None
        if now < self.active.last:
            raise ValueError('time moved backwards')
        if now - self.active.start >= self.maximum_s:
            return self.finish('maximum_duration')
        if now - self.active.last >= self.merge_s:
            return self.finish()
        self.state = SessionState.WAITING
        return None

    def snapshot(self, reason: str | None = None) -> dict[str, object]:
        if self.active is None:
            raise RuntimeError('no active session')
        s = self.active
        flags = list(s.flags)
        if reason:
            flags.append(reason)
        return {
            'schema_version': 1, 'event_id': s.event_id, 'revision': 1,
            'user_id': s.user_id, 'start_ts': s.start_ts.isoformat(),
            'end_ts': (s.start_ts + timedelta(seconds=s.last - s.start)).isoformat(),
            'volume_ml': s.pulses * 1000 / self.factor, 'pulse_count': s.pulses,
            'temperature_c': None, 'tds_in': None, 'tds_out': None, 'ph_approx': None,
            'sensor_quality': {key: 'sensor_error' for key in
                               ('temperature_c', 'tds_in', 'tds_out', 'ph_approx')},
            'confidence': s.confidence, 'recognition_status': s.recognition_status,
            'volume_quality': 'measured' if self.calibrated else 'uncalibrated',
            'incomplete': reason in ('interrupted', 'shutdown', 'pulse_overflow'),
            'quality_flags': flags,
        }

    def finish(self, reason: str | None = None) -> dict[str, object]:
        self.state = SessionState.FINALIZING
        event = self.snapshot(reason)
        self.active = None
        self.state = SessionState.IDLE
        return event
