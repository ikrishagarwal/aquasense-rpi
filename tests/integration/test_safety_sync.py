import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from purifier.communication.schemas import DrainCommand, command, persistence_ack
from purifier.communication.sync import Sync
from purifier.core.session_manager import SessionManager
from purifier.core.state import Quality, Reading, TankLevel
from purifier.safety.interlocks import Conditions, rejection
from purifier.safety.valve_controller import DrainController
from purifier.simulation.devices import SimulatedValve
from purifier.storage.repositories import Repository
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed
from purifier.utils.time import FakeClock


class SafetySyncTests(unittest.TestCase):
    def test_strict_commands(self):
        clock = FakeClock()
        device = str(uuid4())
        payload = {'schema_version': 1, 'command_id': str(uuid4()), 'device_id': device,
                   'type': 'drain', 'issued_at': clock.utcnow().isoformat(),
                   'expires_at': (clock.utcnow()+timedelta(seconds=15)).isoformat(), 'duration_s': 5}
        self.assertEqual(command(json.dumps(payload).encode(), device, clock.utcnow()).duration_s, 5)
        for changed in ({'duration_s': True}, {'duration_s': float('nan')}, {'type': 'flush'},
                        {'extra': 1}, {'expires_at': clock.utcnow().isoformat()}):
            with self.assertRaises(ValueError):
                command(json.dumps(dict(payload, **changed)).encode(), device, clock.utcnow())
        with self.assertRaises(ValueError):
            command(json.dumps(payload).encode(), device, clock.utcnow(), retained=True)

    def test_interlock_matrix(self):
        clock = FakeClock(10)
        tank = Reading(TankLevel.OK, 'threshold', clock.utcnow(), 10, Quality.VALID)
        good = Conditions(False, 0, tank, True, True, False, True, True)
        self.assertIsNone(rejection(good, 10, 3, 2))
        cases = ({'dispensing': True}, {'last_flow_at': 9}, {'physical_enable': False},
                 {'driver_healthy': False}, {'conflicting_operation': True},
                 {'storage_healthy': False}, {'cutoff_verified': False},
                 {'tank': replace(tank, value=TankLevel.UNKNOWN)},
                 {'tank': replace(tank, sampled_at=0)}, {'control_healthy': False})
        for case in cases:
            self.assertIsNotNone(rejection(replace(good, **case), 10, 3, 2))

    def test_drain_commit_replay_fault_close_and_recovery(self):
        async def exercise(path):
            writer = Writer(path)
            await writer.start_async()
            clock = FakeClock(10)
            tank = Reading(TankLevel.OK, 'threshold', clock.utcnow(), 10, Quality.VALID)
            state = Conditions(False, 0, tank, True, True, False, True, True)
            valve = SimulatedValve()
            ctrl = DrainController(writer, valve, clock, lambda: state)
            cmd = DrainCommand(str(uuid4()), str(uuid4()), clock.utcnow(), clock.utcnow()+timedelta(seconds=15), 5)
            try:
                self.assertEqual(await ctrl.accept(cmd), 'running')
                self.assertTrue(valve.opened)
                self.assertEqual(await ctrl.accept(cmd), 'replayed')
                self.assertEqual(valve.open_count, 1)
                state = replace(state, physical_enable=False)
                self.assertEqual(await ctrl.tick(), 'failed')
                self.assertFalse(valve.opened)
                self.assertEqual(await ctrl.accept(cmd), 'replayed')
                state = replace(state, physical_enable=True)
                cmd2 = replace(cmd, command_id=str(uuid4()))
                await ctrl.accept(cmd2)
                await ctrl.stop()
                self.assertFalse(valve.opened)
            finally:
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))

    def test_dropped_ack_duplicate_delivery_and_reconnect(self):
        async def exercise(path):
            writer = Writer(path)
            await writer.start_async()
            device = str(uuid4())
            repo = Repository(writer, device)
            class Backend:
                online = False
                rows = {}
                def publish(self, suffix, payload, qos=1, retain=False):
                    if not self.online:
                        return False
                    self.rows[(payload['device_id'], payload['event_id'])] = payload
                    return True
            backend = Backend()
            sync = Sync(repo, backend)
            try:
                for _ in range(10):
                    manager = SessionManager(450)
                    manager.pulse(0, FakeClock().utcnow())
                    await committed(repo.save(manager.finish()))
                self.assertEqual(await sync.step(0), 0)
                backend.online = True
                self.assertEqual(await sync.step(3), 10)
                self.assertEqual(await sync.step(20), 10)
                self.assertEqual(len(backend.rows), 10)
                self.assertEqual(len(await committed(repo.pending())), 10)
                for event in backend.rows.values():
                    ack = json.dumps({'schema_version': 1, 'device_id': device,
                        'event_id': event['event_id'], 'revision': 1, 'status': 'committed'}).encode()
                    self.assertTrue(await sync.ack(ack))
                    self.assertTrue(await sync.ack(ack))
                self.assertEqual(await committed(repo.pending()), [])
            finally:
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))

    def test_automatic_supervisor_closes_on_physical_disable(self):
        async def exercise(path):
            writer = Writer(path)
            await writer.start_async()
            clock = FakeClock(10)
            tank = Reading(TankLevel.OK, 'threshold', clock.utcnow(), 10, Quality.VALID)
            state = Conditions(False, 0, tank, True, True, False, True, True)
            valve = SimulatedValve()
            ctrl = DrainController(writer, valve, clock, lambda: state)
            cmd = DrainCommand(str(uuid4()), str(uuid4()), clock.utcnow(), clock.utcnow()+timedelta(seconds=15), 5)
            try:
                await ctrl.accept(cmd)
                self.assertTrue(valve.opened)
                state = replace(state, physical_enable=False)
                # No explicit controller.tick(): supervision must close independently.
                await asyncio.wait_for(ctrl._supervisor, timeout=1)
                self.assertFalse(valve.opened)
                rows = await committed(writer.submit(lambda db: db.execute(
                    'SELECT status,reason FROM command_log WHERE command_id=?', (cmd.command_id,)).fetchone()))
                self.assertEqual(rows, ('failed', 'physical_disabled'))
            finally:
                await ctrl.stop()
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))

    def test_acceptance_persistence_failure_never_opens(self):
        async def exercise(path):
            writer = Writer(path)
            await writer.start_async()
            clock = FakeClock(10)
            tank = Reading(TankLevel.OK, 'threshold', clock.utcnow(), 10, Quality.VALID)
            state = Conditions(False, 0, tank, True, True, False, True, True)
            valve = SimulatedValve()
            ctrl = DrainController(writer, valve, clock, lambda: state)
            cmd = DrainCommand(str(uuid4()), str(uuid4()), clock.utcnow(), clock.utcnow()+timedelta(seconds=15), 5)
            try:
                await committed(writer.submit(lambda db: db.execute(
                    "CREATE TRIGGER fail_accept BEFORE UPDATE ON command_log WHEN NEW.status='accepted' BEGIN SELECT RAISE(ABORT, 'simulated disk fault'); END")))
                import sqlite3
                with self.assertRaises(sqlite3.IntegrityError):
                    await ctrl.accept(cmd)
                self.assertFalse(valve.opened)
                self.assertEqual(valve.open_count, 0)
            finally:
                await ctrl.stop()
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))

    def test_interrupted_drain_recovers_failed_and_never_resumes(self):
        async def exercise(path):
            writer = Writer(path)
            await writer.start_async()
            clock = FakeClock(10)
            tank = Reading(TankLevel.OK, 'threshold', clock.utcnow(), 10, Quality.VALID)
            state = Conditions(False, 0, tank, True, True, False, True, True)
            valve = SimulatedValve()
            ctrl = DrainController(writer, valve, clock, lambda: state)
            cmd = DrainCommand(str(uuid4()), str(uuid4()), clock.utcnow(), clock.utcnow()+timedelta(seconds=15), 5)
            try:
                await ctrl.accept(cmd)
                # Stop supervision without writing a terminal lifecycle record.
                # This models interrupted software, not a physical power-loss test.
                ctrl._supervisor.cancel()
                await asyncio.gather(ctrl._supervisor, return_exceptions=True)
            finally:
                await writer.close_async()
            writer = Writer(path)
            await writer.start_async()
            restarted_valve = SimulatedValve()
            restarted = DrainController(writer, restarted_valve, clock, lambda: state)
            try:
                repo = Repository(writer, cmd.device_id)
                await committed(repo.recover())
                rows = await committed(writer.submit(lambda db: db.execute(
                    'SELECT status,reason FROM command_log WHERE command_id=?', (cmd.command_id,)).fetchone()))
                self.assertEqual(rows, ('failed', 'restart'))
                pending = await committed(repo.pending())
                self.assertEqual([event['revision'] for event in pending], [1, 2, 3, 4])
                self.assertEqual(pending[-1]['reason'], 'restart')
                self.assertEqual(await restarted.accept(cmd), 'replayed')
                self.assertEqual(restarted_valve.open_count, 0)
            finally:
                await restarted.stop()
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))
