"""Simulation-only drain state machine with durable replay prevention."""

import asyncio
import sqlite3
from collections.abc import Callable

from purifier.communication.schemas import DrainCommand
from purifier.safety.interlocks import Conditions, rejection
from purifier.simulation.devices import SimulatedValve
from purifier.storage.repositories import encode
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed
from purifier.utils.time import Clock
from purifier.utils.logging import event


class DrainController:
    def __init__(self, writer: Writer, valve: SimulatedValve, clock: Clock,
                 conditions: Callable[[], Conditions], max_on_s: float = 15,
                 quiet_s: float = 3, max_tank_age_s: float = 2) -> None:
        # There is deliberately no physical actuation implementation in this class.
        if type(valve) is not SimulatedValve:
            raise TypeError('only simulated valve accepted until bench gates pass')
        if not 0 < max_on_s <= 15 or quiet_s <= 0 or max_tank_age_s <= 0:
            raise ValueError('invalid drain limits')
        self.writer, self.valve, self.clock, self.conditions = writer, valve, clock, conditions
        self.max_on_s, self.quiet_s, self.max_tank_age_s = max_on_s, quiet_s, max_tank_age_s
        self.active: DrainCommand | None = None
        self.deadline = 0.0
        self._accepting = False
        self._supervisor: asyncio.Task[None] | None = None
        self.fault: str | None = None
        self.valve.close()

    async def _record(self, command: DrainCommand, status: str, reason: str) -> None:
        ts = self.clock.utcnow().isoformat()
        payload = {'schema_version': 1, 'device_id': command.device_id,
                   'command_id': command.command_id, 'event_id': command.command_id,
                   'type': 'command_ack', 'status': status,
                   'reason': reason, 'timestamp': ts,
                   'valve_state': 'commanded_open' if status == 'running' else 'commanded_closed'}
        def work(db: sqlite3.Connection) -> None:
            revision = db.execute('SELECT COUNT(*) FROM command_history WHERE command_id=?', (command.command_id,)).fetchone()[0] + 1
            payload['revision'] = revision
            db.execute('INSERT INTO outbox(event_id,revision,payload) VALUES (?,?,?)', (command.command_id, revision, encode(payload)))
            db.execute('UPDATE command_log SET status=?, reason=?, ts=?, payload=? WHERE command_id=?',
                       (status, reason, ts, encode(payload), command.command_id))
            db.execute('INSERT INTO command_history(command_id,status,reason,ts) VALUES (?,?,?,?)',
                       (command.command_id, status, reason, ts))
        await committed(self.writer.submit(work))

    def _reason(self, command: DrainCommand) -> str | None:
        if not self.writer.healthy:
            return 'storage_fault'
        if self.valve.failed:
            return 'driver_fault'
        if not command.issued_at <= self.clock.utcnow() < command.expires_at:
            return 'expired'
        if not 0 < command.duration_s <= self.max_on_s:
            return 'duration_limit'
        return rejection(self.conditions(), self.clock.monotonic(), self.quiet_s, self.max_tank_age_s)

    async def accept(self, command: DrainCommand) -> str:
        if self._accepting:
            return 'busy'
        self._accepting = True
        try:
            ts = self.clock.utcnow().isoformat()
            def receive(db: sqlite3.Connection) -> str | None:
                row = db.execute('SELECT status FROM command_log WHERE command_id=?', (command.command_id,)).fetchone()
                if row:
                    return str(row[0])
                payload = {'schema_version': 1, 'device_id': command.device_id,
                    'command_id': command.command_id, 'event_id': command.command_id,
                    'type': 'command_ack', 'revision': 1, 'status': 'received',
                    'reason': '', 'timestamp': ts, 'valve_state': 'commanded_closed'}
                db.execute('INSERT INTO command_log VALUES (?, ?, ?, ?, ?)',
                           (command.command_id, 'received', '', ts, encode(payload)))
                db.execute('INSERT INTO outbox(event_id,revision,payload) VALUES (?,?,?)',
                           (command.command_id, 1, encode(payload)))
                db.execute('INSERT INTO command_history(command_id,status,reason,ts) VALUES (?,?,?,?)',
                           (command.command_id, 'received', '', ts))
                return None
            previous = await committed(self.writer.submit(receive))
            if previous is not None:
                return 'replayed'
            reason = 'busy' if self.active else self._reason(command)
            if reason:
                await self._record(command, 'rejected', reason)
                return reason
            await self._record(command, 'accepted', 'interlocks_passed')
            # Re-evaluate after commit because state can change during persistence.
            reason = self._reason(command)
            if reason:
                await self._record(command, 'failed', reason)
                return reason
            self.deadline = self.clock.monotonic() + min(command.duration_s, self.max_on_s)
            self.active = command
            try:
                self.valve.open()
                self._supervisor = asyncio.create_task(self._supervise(command))
                await self._record(command, 'running', 'commanded_open')
            except BaseException:
                self.valve.close()
                self.active = None
                raise
            return 'running' if self.active is command else 'closed'
        except BaseException:
            self.valve.close()
            self.active = None
            raise
        finally:
            self._accepting = False

    async def _supervise(self, command: DrainCommand) -> None:
        try:
            while self.active is command:
                await self.tick()
                if self.active is command:
                    await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            if self.active is command:
                self.valve.close()
                self.active = None
            raise
        except Exception:
            self.valve.close()
            if self.active is command:
                self.active = None
            self.fault = 'drain_supervisor_failed'
            event(self.fault, 50)

    async def tick(self) -> str | None:
        if self.active is None:
            return None
        command = self.active
        try:
            reason = self._reason(command)
        except Exception:
            self.valve.close()
            self.active = None
            raise
        timed_out = self.clock.monotonic() >= self.deadline
        if reason or timed_out:
            self.valve.close()  # Always before persistence/network.
            self.active = None
            status = 'failed' if reason else 'completed'
            await self._record(command, status, reason or 'commanded_closed')
            return status
        return 'running'

    async def stop(self) -> None:
        self.valve.close()
        command, self.active = self.active, None
        if command:
            await self._record(command, 'failed', 'shutdown')
        if self._supervisor is not None:
            await asyncio.wait({self._supervisor}, timeout=0.2)
