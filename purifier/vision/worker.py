"""Isolated simulation worker; real inference requires validated model/camera."""

import argparse
import asyncio
import os
import signal
from pathlib import Path

from purifier.core.state import Recognition
from purifier.vision.protocol import MAX_MESSAGE, parse_request, response


class Worker:
    def __init__(self) -> None:
        self.jobs = 0
        self.lock = asyncio.Lock()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.jobs >= 2:
            writer.close()
            return
        self.jobs += 1
        try:
            async with asyncio.timeout(5):
                ids = parse_request(await reader.readline())
                async with self.lock:
                    # Explicit simulation: no model and no camera access.
                    result = Recognition(ids[0], ids[1], 'guest')
                    writer.write(response(result))
                    await writer.drain()
        except (OSError, TimeoutError, ValueError, TypeError, KeyError):
            pass  # Invalid peer gets no result; core retains Guest.
        finally:
            self.jobs -= 1
            writer.close()
            try:
                async with asyncio.timeout(0.2):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass


async def serve(path: Path) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.parent.stat().st_mode & 0o007:
        raise ValueError('socket directory must exclude other users; group must be trusted')
    # Never unlink an existing socket that may belong to a running worker.
    if path.exists() or path.is_symlink():
        raise FileExistsError('remove stale socket only after checking its owner process')
    previous = os.umask(0o077)
    try:
        server = await asyncio.start_unix_server(Worker().handle, path=str(path), limit=MAX_MESSAGE, backlog=2)
    finally:
        os.umask(previous)
    path.chmod(0o660)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with server:
            await stop.wait()
    finally:
        path.unlink(missing_ok=True)
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--simulation', action='store_true', required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.socket))


if __name__ == '__main__':
    main()
