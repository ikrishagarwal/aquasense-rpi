"""Wall-clock simulation soak; explicit duration, bounded memory, periodic health."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from purifier.core.controller import Controller
from purifier.core.session_manager import SessionManager
from purifier.safety.health import snapshot
from purifier.simulation.devices import SimulatedValve
from purifier.storage.repositories import Repository
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed
from purifier.utils.time import FakeClock


async def soak(path: Path, seconds: float) -> None:
    writer = Writer(path)
    await writer.start_async()
    clock = FakeClock()
    repo = Repository(writer, None)
    controller = Controller(SessionManager(450), repo, clock=clock)
    valve = SimulatedValve()
    end, next_report, sessions = time.monotonic()+seconds, 0.0, 0
    try:
        await committed(repo.recover())
        while time.monotonic() < end:
            await controller.pulse(clock.monotonic(), clock.utcnow())
            clock.advance(4)
            await controller.tick(clock.monotonic())
            sessions += 1
            if time.monotonic() >= next_report:
                report = snapshot(writer)
                report.update(simulated_sessions=sessions, valve_open_count=valve.open_count,
                              integrity=await committed(repo.maintain()))
                print(json.dumps(report), flush=True)
                next_report = time.monotonic()+60
            await asyncio.sleep(min(1, max(0, end-time.monotonic())))
        print(json.dumps({'completed': True, 'sessions': sessions, 'valve_open_count': valve.open_count,
                          'integrity': await committed(repo.maintain())}), flush=True)
    finally:
        valve.close()
        await controller.shutdown()
        await writer.close_async()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, required=True)
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    import math
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 604800:
        parser.error('duration must be positive and at most one week')
    asyncio.run(soak(args.database, args.seconds))


if __name__ == '__main__':
    main()
