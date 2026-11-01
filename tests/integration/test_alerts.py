import asyncio
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from purifier.core.state import Quality, Reading
from purifier.safety.alerts import Alerts
from purifier.storage.repositories import Repository
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed
from purifier.utils.time import FakeClock


class AlertTests(unittest.TestCase):
    def test_debounce_restart_dedup_stale_and_hysteresis(self):
        async def exercise(path):
            writer = Writer(path)
            await writer.start_async()
            device, clock = str(uuid4()), FakeClock()
            alerts = Alerts(writer, device, 100, 10, 2, 1000)
            def reading(value=110, quality=Quality.VALID):
                return Reading(value, 'ppm', clock.utcnow(), clock.monotonic(), quality)
            try:
                self.assertFalse(await alerts.tds(reading(), 0))
                self.assertFalse(await alerts.tds(reading(), 0))
                clock.advance(1)
                self.assertTrue(await alerts.tds(reading(), 1))
                alerts = Alerts(writer, device, 100, 10, 2, 1000)
                for _ in range(3):
                    clock.advance(1)
                    self.assertFalse(await alerts.tds(reading(), clock.monotonic()))
                clock.advance(1)
                self.assertFalse(await alerts.tds(reading(80, Quality.STALE), clock.monotonic()))
                clock.advance(1)
                self.assertFalse(await alerts.tds(reading(80), clock.monotonic()))
                clock.advance(1)
                self.assertTrue(await alerts.tds(reading(80), clock.monotonic()))
                events = await committed(Repository(writer, device).pending())
                self.assertEqual([e['active'] for e in events], [True, False])
            finally:
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))
