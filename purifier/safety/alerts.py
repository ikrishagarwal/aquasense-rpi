"""Durable alert transitions and measured-volume filter usage."""

import math
import sqlite3
from datetime import datetime
from typing import Any
from uuid import uuid4

from purifier.core.state import Reading
from purifier.storage.repositories import encode
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed


class Alerts:
    def __init__(self, writer: Writer, device_id: str | None, threshold: float | None,
                 hysteresis: float | None, consecutive: int, filter_capacity_litres: float | None,
                 filter_fraction: float = 0.9) -> None:
        if not all(math.isfinite(x) and x > 0 for x in (threshold, hysteresis, filter_capacity_litres) if x is not None) or consecutive < 1 or not 0 < filter_fraction <= 1:
            raise ValueError('invalid alert calibration or debounce')
        if threshold is not None and (hysteresis is None or hysteresis >= threshold):
            raise ValueError('hysteresis required below threshold')
        self.filter_fraction = filter_fraction
        self.writer, self.device_id = writer, device_id
        self.threshold, self.hysteresis = threshold, hysteresis
        self.consecutive, self.capacity = consecutive, filter_capacity_litres
        self.high, self.low = 0, 0
        self.last_sample: float | None = None

    async def _transition(self, name: str, active: bool, timestamp: datetime) -> bool:
        def work(db: sqlite3.Connection) -> bool:
            previous = db.execute('SELECT active FROM alert_state WHERE name=?', (name,)).fetchone()
            if (bool(previous[0]) if previous else False) == active:
                return False
            db.execute('INSERT INTO alert_state VALUES (?,?) ON CONFLICT(name) DO UPDATE SET active=excluded.active', (name, int(active)))
            payload: dict[str, Any] = {'schema_version': 1, 'event_id': str(uuid4()),
                'revision': 1, 'device_id': self.device_id, 'type': 'alert', 'name': name,
                'active': active, 'timestamp': timestamp.isoformat()}
            db.execute('INSERT INTO outbox(event_id,revision,payload) VALUES (?,?,?)',
                       (payload['event_id'], 1, encode(payload)))
            return True
        return bool(await committed(self.writer.submit(work)))

    async def tds(self, reading: Reading[float], now: float, max_age: float = 5) -> bool:
        if self.threshold is None or self.hysteresis is None:
            return False
        if not reading.fresh(now, max_age) or reading.value is None or not math.isfinite(reading.value):
            self.high = self.low = 0
            return False
        if self.last_sample is not None and reading.sampled_at <= self.last_sample:
            return False
        self.last_sample = reading.sampled_at
        if reading.value >= self.threshold:
            self.high += 1
            self.low = 0
        elif reading.value <= self.threshold - self.hysteresis:
            self.low += 1
            self.high = 0
        else:
            self.high = self.low = 0
        if self.high >= self.consecutive:
            return await self._transition('outlet_tds_high', True, reading.timestamp)
        if self.low >= self.consecutive:
            return await self._transition('outlet_tds_high', False, reading.timestamp)
        return False

    async def filter_usage(self, timestamp: datetime) -> bool:
        if self.capacity is None:
            return False
        millilitres = await committed(self.writer.submit(lambda db: db.execute(
            "SELECT value FROM counters WHERE name='filter_ml'").fetchone()[0]))
        if millilitres >= self.capacity * 1000 * self.filter_fraction:
            return await self._transition('filter_usage_90_percent', True, timestamp)
        return False
