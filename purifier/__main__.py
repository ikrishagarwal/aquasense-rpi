"""Simulation controller entry point; hardware remains blocked."""

import argparse
import asyncio
import logging
import math
import os
import ssl
import signal
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from purifier.core.config import ConfigError, load_config
from purifier.bootstrap import assemble
from purifier.communication.mqtt import MQTT
from purifier.communication.service import NetworkService
from purifier.safety.alerts import Alerts
from purifier.utils.time import SystemClock
from purifier.core.runtime import run
from purifier.hardware.valve import DisabledValve
from purifier.storage.sqlite import StorageError
from purifier.utils.logging import configure, event


async def serve(config_path: Path, run_for: float | None, demo: bool = False) -> None:
    config = load_config(config_path)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    valve = DisabledValve()

    def request_stop() -> None:
        valve.close()
        pulses.stop()
        stop.set()

    installed: list[signal.Signals] = []
    timer = None
    clock = SystemClock()
    writer, controller, pulses, sensors = assemble(config, clock)
    pulse_timers = []
    network = None
    if config.mqtt is not None and config.device_id is not None:
        settings = config.mqtt
        try:
            transport = MQTT(config.device_id, settings.host,
                os.environ.get(settings.username_env, ''), os.environ.get(settings.password_env, ''),
                Path(settings.ca_file) if settings.ca_file else None, settings.port)
            network = NetworkService(controller.repository, transport, settings.batch_size)
        except (ImportError, OSError, ValueError, ssl.SSLError):
            event('mqtt_configuration_unavailable', logging.ERROR)
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, request_stop)
            installed.append(sig)
        if run_for is not None:
            timer = loop.call_later(run_for, request_stop)
        if demo:
            for index in range(5):
                pulse_timers.append(loop.call_later(0.2 + index * 0.1,
                    lambda: pulses.pulse(clock.monotonic())))
        alerts = Alerts(writer, config.device_id, config.tds_threshold,
            config.tds_hysteresis, config.alert_debounce, config.filter_capacity, config.filter_fraction)
        await run(writer, valve, stop, controller, pulses, sensors, clock, network,
                  alerts, config.telemetry_s)
    finally:
        valve.close()
        if timer is not None:
            timer.cancel()
        for handle in pulse_timers:
            handle.cancel()
        for sig in installed:
            loop.remove_signal_handler(sig)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AquaSense simulation controller")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-for", type=float, help="Stop simulation after seconds")
    parser.add_argument("--demo", action="store_true", help="Inject five simulated flow pulses")
    args = parser.parse_args(argv)
    if args.run_for is not None and (not math.isfinite(args.run_for) or args.run_for <= 0):
        parser.error("--run-for must be positive and finite")
    configure()
    try:
        asyncio.run(serve(args.config, args.run_for, args.demo))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except (StorageError, OSError, RuntimeError, sqlite3.Error) as exc:
        event("controller_failed", logging.CRITICAL)
        print(f"controller failure: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
