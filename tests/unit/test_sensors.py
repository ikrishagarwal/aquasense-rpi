import tempfile
import unittest
from pathlib import Path

from purifier.core.state import Quality, TankLevel
from purifier.hardware.sensors import ADCManager, AnalogSensor, DS18B20, DebouncedTank, LinearCalibration, Poller
from purifier.utils.time import FakeClock


class SensorTests(unittest.TestCase):
    def test_analog_uncalibrated_fault_and_range(self):
        class ADC:
            value = 1.0
            def read_voltage(self, channel):
                return self.value
        adc = ADC()
        sensor = AnalogSensor(ADCManager(adc), 0, FakeClock(), 'ppm')
        self.assertEqual(sensor.read().quality, Quality.UNCALIBRATED)
        self.assertIsNone(sensor.read().value)
        sensor.calibration = LinearCalibration(100, 0, 0, 500)
        self.assertEqual(sensor.read().value, 100)
        adc.value = 5
        self.assertEqual(sensor.read().quality, Quality.OUT_OF_RANGE)
        self.assertIsNone(sensor.read().value)

    def test_crc_missing_and_valid_temperature(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'w1_slave'
            sensor = DS18B20(path, FakeClock())
            self.assertIsNone(sensor.read().value)
            path.write_text('CRC NO\na t=25000\n')
            self.assertIsNone(sensor.read().value)
            path.write_text('CRC YES\na t=25000\n')
            self.assertEqual(sensor.read().value, 25)

    def test_tank_ok_requires_stability_fault_is_immediate(self):
        clock = FakeClock()
        tank = DebouncedTank(clock, True, 0.2)
        self.assertEqual(tank.update(True).value, TankLevel.UNKNOWN)
        clock.advance(0.2)
        self.assertEqual(tank.update(True).value, TankLevel.OK)
        self.assertEqual(tank.update(None).value, TankLevel.UNKNOWN)
        self.assertEqual(tank.update(False).value, TankLevel.LOW)

    def test_poll_failures_are_bounded_and_recoverable(self):
        clock = FakeClock()
        def failed():
            raise OSError('unplugged')
        poller = Poller(failed, clock, 2, 'ppm', 8)
        self.assertEqual(poller.step().quality, Quality.SENSOR_ERROR)
        for _ in range(5):
            clock.advance(8)
            poller.step()
        self.assertEqual(poller.backoff, 8)
