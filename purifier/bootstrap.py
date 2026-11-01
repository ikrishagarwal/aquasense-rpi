"""Composition root; simulation is explicit and hardware is blocked at config."""

from purifier.core.config import Config
from purifier.communication.vision_client import VisionClient
from purifier.core.controller import Controller
from purifier.core.session_manager import SessionManager
from purifier.hardware.gpio_flow import PulseCounter
from purifier.hardware.hardware_factory import optional_sensor
from purifier.hardware.sensors import Poller
from purifier.storage.repositories import Repository
from purifier.storage.sqlite import Writer
from purifier.utils.time import Clock


def assemble(config: Config, clock: Clock) -> tuple[Writer, Controller, PulseCounter, dict[str, Poller[float]]]:
    writer = Writer(config.database, config.writer_capacity)
    repository = Repository(writer, config.device_id)
    controller = Controller(SessionManager(config.pulses_per_litre, config.merge_timeout_s,
        config.max_session_s, config.calibrated), repository, recognizer=VisionClient(config.vision_socket, config.recognition_deadline_s) if config.vision_socket else None, clock=clock,
        recognition_deadline=config.recognition_deadline_s)
    sensors = {}
    for name, interval, unit in (('tds_in', config.tds_poll_s, 'ppm'), ('tds_out', config.tds_poll_s, 'ppm'),
                                 ('temperature_c', config.temperature_poll_s, 'C'), ('ph_approx', config.ph_poll_s, 'unitless')):
        sensor = optional_sensor(config.mode, clock)
        sensor.unit = unit
        sensors[name] = Poller(sensor.read, clock, interval, unit, max_backoff=max(60, interval))
    return writer, controller, PulseCounter(), sensors
