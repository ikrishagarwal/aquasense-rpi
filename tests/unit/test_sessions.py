import unittest

from purifier.core.session_manager import SessionManager, SessionState
from purifier.core.state import Quality, Reading, TankLevel
from purifier.hardware.gpio_flow import PulseCounter
from purifier.simulation.devices import SimulatedSensor
from purifier.utils.time import FakeClock


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sessions = SessionManager(450)

    def pulse(self, seconds=0):
        self.clock.advance(seconds)
        return self.sessions.pulse(self.clock.monotonic(), self.clock.utcnow())

    def test_merge_and_exact_timeout_split(self):
        self.pulse()
        identity = self.sessions.active.event_id
        self.pulse(2.999)
        self.assertEqual(self.sessions.active.event_id, identity)
        finished = self.pulse(3)
        self.assertEqual(finished['event_id'], identity)
        self.assertEqual(finished['pulse_count'], 2)
        self.assertAlmostEqual(finished['volume_ml'], 2000 / 450)
        self.assertNotEqual(self.sessions.active.event_id, identity)

    def test_maximum_and_wall_clock_correction(self):
        sessions = SessionManager(450, maximum_s=4)
        sessions.pulse(0, self.clock.utcnow())
        sessions.pulse(2, self.clock.utcnow())
        event = sessions.pulse(4, self.clock.utcnow())
        self.assertIn('maximum_duration', event['quality_flags'])
        self.assertEqual(event['pulse_count'], 2)
        self.assertEqual(sessions.active.pulses, 1)

    def test_shutdown_is_incomplete(self):
        self.pulse()
        self.assertTrue(self.sessions.finish('shutdown')['incomplete'])
        self.assertEqual(self.sessions.state, SessionState.IDLE)

    def test_counter_retains_overflow_count_and_swaps_atomically(self):
        counter = PulseCounter(2)
        for i in range(3):
            counter.pulse(i)
        batch = counter.take()
        counter.pulse(4)
        self.assertEqual(list(batch.times), [0, 1])
        self.assertEqual(batch.overflow_count, 1)
        self.assertEqual(batch.total, 3)
        self.assertEqual(list(counter.take().times), [4])

    def test_stuck_or_unplugged_sensor_never_fabricates_live_value(self):
        sensor = SimulatedSensor(self.clock, 25.0, 'C', Quality.VALID)
        sensor.stuck = sensor.read()
        self.clock.advance(10)
        self.assertIsNone(sensor.read().wire(self.clock.monotonic(), 5)['value'])
        sensor.failed = True
        with self.assertRaises(OSError):
            sensor.read()

    def test_concurrent_callbacks_preserve_total_even_when_timing_buffer_fills(self):
        import threading
        counter = PulseCounter(200)
        def emit():
            for _ in range(1000):
                counter.pulse(1)
        threads = [threading.Thread(target=emit) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        batch = counter.take()
        self.assertEqual(batch.total, 4000)
        self.assertEqual(len(batch.times) + batch.overflow_count, 4000)
