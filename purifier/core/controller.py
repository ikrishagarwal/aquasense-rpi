"""Serialized measurement/persistence pipeline; vision never blocks a write."""

import asyncio
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from purifier.core.session_manager import SessionManager
from purifier.core.state import Reading
from purifier.communication.schemas import uuid
from purifier.hardware.interfaces import Recognizer
from purifier.storage.repositories import Repository
from purifier.utils.async_bridge import committed
from purifier.utils.time import Clock, SystemClock
from purifier.utils.logging import event


class Controller:
    def __init__(self, sessions: SessionManager, repository: Repository,
                 recognizer: Recognizer | None = None, clock: Clock | None = None,
                 recognition_deadline: float = 5,
                 capture_eligible: Callable[[], bool] | None = None) -> None:
        self.sessions, self.repository = sessions, repository
        self.recognizer = recognizer
        self.clock = clock or SystemClock()
        self.recognition_deadline = recognition_deadline
        self.capture_eligible = capture_eligible or (lambda: False)
        self.recognition_task: asyncio.Task[None] | None = None
        self.pending: dict[str, Any] | None = None
        self.fault = False
        self.readings: dict[str, tuple[Reading[float], float]] = {}

    def _snapshot(self, event: dict[str, Any]) -> dict[str, Any]:
        for name, (reading, max_age) in self.readings.items():
            if name not in ('temperature_c', 'tds_in', 'tds_out', 'ph_approx'):
                raise ValueError('unknown sensor field')
            wire = reading.wire(self.clock.monotonic(), max_age)
            event[name] = wire['value']
            event['sensor_quality'][name] = wire['quality']
        return event

    async def _save(self, event: dict[str, Any]) -> None:
        self.pending = self._snapshot(event)
        try:
            await committed(self.repository.save(self.pending))
        except Exception:
            self.fault = True
            # Preserve uncommitted event in memory; no fabricated success.
            raise
        self.pending = None

    async def _identify(self, session_id: str) -> None:
        request_id = str(uuid4())
        deadline = self.clock.monotonic() + self.recognition_deadline
        try:
            async with asyncio.timeout(self.recognition_deadline):
                if self.recognizer is None:
                    return
                result = await self.recognizer.identify(request_id, session_id)
            active = self.sessions.active
            if (active is not None and active.event_id == session_id
                    and result.request_id == request_id and result.session_id == session_id
                    and self.clock.monotonic() < deadline):
                if result.status == 'matched':
                    uuid(result.user_id)
                    if result.similarity is None or not math.isfinite(result.similarity) or not -1 <= result.similarity <= 1:
                        raise ValueError('invalid recognition confidence')
                    if not self.capture_eligible():
                        return
                    active.user_id, active.confidence = result.user_id, result.similarity
                active.recognition_status = result.status
        except Exception:
            # Optional subsystem boundary: log no image, embedding or exception text.
            event('vision_unavailable', 30)
            active = self.sessions.active
            if active is not None and active.event_id == session_id:
                active.recognition_status = 'unavailable'

    async def pulse(self, at: float, wall: datetime, checkpoint: bool = True) -> None:
        if self.fault:
            raise RuntimeError('measurement persistence fault')
        previous = self.sessions.active.event_id if self.sessions.active else None
        event = self.sessions.pulse(at, wall)
        active = self.sessions.active
        if active is None:
            raise RuntimeError('pulse did not start session')
        if active.event_id != previous and self.recognizer is not None and self.capture_eligible():
            if self.recognition_task is None or self.recognition_task.done():
                self.recognition_task = asyncio.create_task(self._identify(active.event_id))
        if event:
            await self._save(event)
        if checkpoint:
            await self.checkpoint()

    async def checkpoint(self) -> None:
        if self.sessions.active is None:
            return
        try:
            await committed(self.repository.checkpoint(self._snapshot(self.sessions.snapshot())))
        except Exception:
            self.fault = True
            raise

    async def tick(self, now: float) -> None:
        if self.fault:
            raise RuntimeError('measurement persistence fault')
        event = self.sessions.tick(now)
        if event:
            await self._save(event)

    async def shutdown(self) -> None:
        if self.recognition_task is not None:
            self.recognition_task.cancel()
            await asyncio.wait({self.recognition_task}, timeout=0.2)
        if not self.fault and self.sessions.active is not None:
            await self._save(self.sessions.finish('shutdown'))
