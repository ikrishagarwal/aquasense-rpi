"""Generate deterministic offline sessions; no GPIO, camera or network access."""

import argparse
import asyncio
from pathlib import Path

from purifier.core.controller import Controller
from purifier.core.session_manager import SessionManager
from purifier.storage.repositories import Repository
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed
from purifier.utils.time import FakeClock


async def simulate(path: Path, count: int) -> None:
    clock = FakeClock()
    writer = Writer(path)
    await writer.start_async()
    repo = Repository(writer, None)
    controller = Controller(SessionManager(450), repo)
    try:
        await committed(repo.recover())
        for _ in range(count):
            # Small deterministic pulse train; volumes remain uncalibrated.
            for _ in range(5):
                await controller.pulse(clock.monotonic(), clock.utcnow())
                clock.advance(0.1)
            clock.advance(3)
            await controller.tick(clock.monotonic())
        print(f'committed {count} simulated sessions; device_id unset; no sync attempted')
    finally:
        await controller.shutdown()
        await writer.close_async()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path('var/demo.sqlite3'))
    parser.add_argument('--sessions', type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.sessions <= 1000:
        parser.error('sessions must be 1..1000')
    asyncio.run(simulate(args.database, args.sessions))


if __name__ == '__main__':
    main()
