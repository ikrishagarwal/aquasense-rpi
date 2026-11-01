"""Core lifecycle with optional injected simulation ports; no Pi imports."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from purifier.core.controller import Controller
from purifier.communication.service import NetworkService
from purifier.safety.alerts import Alerts
from purifier.safety.health import snapshot
from purifier.storage.repositories import encode
from purifier.hardware.sensors import Poller
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed
from purifier.utils.logging import event
from purifier.utils.time import Clock, SystemClock


class CloseableValve(Protocol):
    def close(self) -> None: ...


class PulseSource(Protocol):
    def take(self) -> Any: ...
    def stop(self) -> None: ...


async def run(writer: Writer, valve: CloseableValve, stop: asyncio.Event,
              controller: Controller | None = None, pulses: PulseSource | None = None,
              sensors: dict[str, Poller[float]] | None = None,
              clock: Clock | None = None, network: NetworkService | None = None,
              alerts: Alerts | None = None, telemetry_s: float = 15) -> None:
    clock = clock or SystemClock()
    valve.close()
    try:
        await writer.start_async()
        if controller is not None:
            await committed(controller.repository.recover())
        await committed(writer.submit(lambda db: db.execute(
            'INSERT INTO system_events(code, ts) VALUES (?, ?)',
            ('controller_started', datetime.now(timezone.utc).isoformat())) and None))
        if network is not None:
            network.start()
        last_integrity = None
        next_telemetry = clock.monotonic() + telemetry_s
        next_maintenance = clock.monotonic() + 60
        event('controller_started')
        while not stop.is_set():
            if not writer.healthy:
                raise RuntimeError('storage unhealthy')
            if controller is not None:
                for name, poller in (sensors or {}).items():
                    reading = poller.step()
                    if reading is not None:
                        controller.readings[name] = (reading, poller.interval * 3)
                cutoff = clock.monotonic()
                if pulses is not None:
                    batch = pulses.take()
                    if batch.overflow_count:
                        event('critical_pulse_timing_overflow', 50)
                        if controller.sessions.active:
                            controller.sessions.active.flags.append('pulse_overflow')
                        # Preserve cumulative count in durable fault metadata.
                        await committed(writer.submit(lambda db: db.execute(
                            'INSERT INTO system_events(code,ts) VALUES (?,?)',
                            (f'pulse_overflow_total_{batch.total}', clock.utcnow().isoformat())) and None))
                        raise RuntimeError('pulse timing buffer overflow')
                    for at in batch.times:
                        wall = clock.utcnow() - timedelta(seconds=clock.monotonic() - at)
                        await controller.pulse(at, wall, checkpoint=False)
                        cutoff = max(cutoff, at)
                    if batch.times:
                        await controller.checkpoint()
                await controller.tick(cutoff)
                if alerts is not None and 'tds_out' in controller.readings:
                    reading, max_age = controller.readings['tds_out']
                    await alerts.tds(reading, clock.monotonic(), max_age)
                if clock.monotonic() >= next_telemetry:
                    report: dict[str, Any] = {'schema_version': 1, 'device_id': controller.repository.device_id,
                        'timestamp': clock.utcnow().isoformat(), 'tank_state': 'UNKNOWN',
                        'sensors': {name: reading.wire(clock.monotonic(), age)
                                    for name, (reading, age) in controller.readings.items()},
                        'health': snapshot(writer)}
                    report['health']['pending_outbox'] = await committed(writer.submit(lambda db: db.execute('SELECT COUNT(*) FROM outbox WHERE acknowledged=0').fetchone()[0]))
                    report['health']['sqlite_integrity'] = last_integrity
                    report['health']['vision'] = 'privacy_blocked' if controller.recognizer else 'disabled'
                    report['health']['mqtt'] = ('connected' if network.transport.connected else 'offline') if network else 'disabled'
                    if network is not None:
                        network.transport.publish('telemetry', report, qos=0)
                    if writer.depth == 0:
                        await committed(writer.submit(lambda db: db.execute(
                            'INSERT INTO sensor_samples(ts,payload) VALUES (?,?)',
                            (clock.utcnow().isoformat(), encode(report))) and None))
                    if alerts is not None:
                        await alerts.filter_usage(clock.utcnow())
                    next_telemetry = clock.monotonic() + telemetry_s
                if clock.monotonic() >= next_maintenance:
                    integrity = await committed(controller.repository.maintain())
                    last_integrity = integrity
                    if integrity != 'ok':
                        raise RuntimeError('SQLite integrity fault')
                    await committed(controller.repository.checkpoint_wal())
                    next_maintenance = clock.monotonic() + 60
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.05 if controller else 1)
            except TimeoutError:
                continue
    finally:
        valve.close()
        if pulses is not None:
            pulses.stop()
        if network is not None:
            network.stop.set()
        try:
            async with asyncio.timeout(5):
                if controller is not None:
                    if pulses is not None:
                        batch = pulses.take()
                        if batch.overflow_count:
                            event('critical_shutdown_pulse_overflow', 50)
                            controller.fault = True
                        if writer.healthy and not controller.fault:
                            for at in batch.times:
                                wall = clock.utcnow() - timedelta(seconds=clock.monotonic() - at)
                                await controller.pulse(at, wall, checkpoint=False)
                    await controller.shutdown()
        finally:
            try:
                if network is not None:
                    await network.close()
            finally:
                await writer.close_async()
                event('controller_stopped')
