import asyncio
import tempfile
import unittest
from pathlib import Path

from purifier.core.controller import Controller
from purifier.core.session_manager import SessionManager
from purifier.core.state import Quality, Reading
from purifier.storage.repositories import Repository
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed
from purifier.utils.time import FakeClock


class FailureSimulationTests(unittest.TestCase):
    def test_hung_vision_and_stale_sensor_do_not_block_commit(self):
        async def exercise(path):
            class Hung:
                async def identify(self, request_id, session_id):
                    await asyncio.Event().wait()
            writer = Writer(path)
            await writer.start_async()
            clock = FakeClock()
            repo = Repository(writer, None)
            ctrl = Controller(SessionManager(450), repo, Hung(), clock,
                              recognition_deadline=.01, capture_eligible=lambda: True)
            ctrl.readings['tds_out'] = (Reading(100, 'ppm', clock.utcnow(), 0, Quality.VALID), 2)
            try:
                await ctrl.pulse(0, clock.utcnow())
                clock.advance(1)
                await ctrl.pulse(1, clock.utcnow())
                clock.advance(4)
                await ctrl.tick(5)
                events = await committed(repo.pending())
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['pulse_count'], 2)
                self.assertEqual(events[0]['user_id'], 'guest')
                self.assertIsNone(events[0]['tds_out'])
                self.assertEqual(events[0]['sensor_quality']['tds_out'], 'stale')
            finally:
                await ctrl.shutdown()
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))

    def test_privacy_gate_prevents_even_requesting_vision(self):
        async def exercise(path):
            class Unexpected:
                called = False
                async def identify(self, request_id, session_id):
                    self.called = True
                    raise AssertionError('privacy gate failed')
            writer = Writer(path)
            await writer.start_async()
            clock = FakeClock()
            worker = Unexpected()
            ctrl = Controller(SessionManager(450), Repository(writer, None), worker, clock)
            try:
                await ctrl.pulse(0, clock.utcnow())
                await ctrl.shutdown()
                self.assertFalse(worker.called)
            finally:
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))

    def test_pulse_arriving_during_checkpoint_does_not_split_session(self):
        from purifier.core.runtime import run
        from purifier.hardware.gpio_flow import PulseCounter
        from purifier.hardware.valve import DisabledValve
        from contextlib import closing
        import json
        import sqlite3

        async def exercise(path):
            clock = FakeClock()
            writer = Writer(path)
            pulses = PulseCounter()
            stop = asyncio.Event()
            class HookRepository(Repository):
                first = True
                def checkpoint(self, event):
                    future = super().checkpoint(event)
                    if self.first:
                        self.first = False
                        # A callback arrives just below the merge boundary while
                        # the previous batch's SQLite checkpoint is outstanding.
                        clock.advance(3)
                        pulses.pulse(2.999)
                    else:
                        stop.set()
                    return future
            repo = HookRepository(writer, None)
            ctrl = Controller(SessionManager(450), repo, clock=clock)
            pulses.pulse(0)
            await run(writer, DisabledValve(), stop, ctrl, pulses, clock=clock)
            with closing(sqlite3.connect(path)) as db:
                rows = db.execute('SELECT payload FROM dispense_sessions').fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(json.loads(rows[0][0])['pulse_count'], 2)
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))
