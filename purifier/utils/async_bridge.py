"""Bounded polling bridge for the dedicated writer, without executor threads.

The writer never needs to send bytes to an event-loop socket. Polling is limited
at 20 Hz while a write is outstanding; there is no idle polling task.
"""

import asyncio
from concurrent.futures import Future
from typing import TypeVar

T = TypeVar('T')


async def committed(future: Future[T], timeout: float = 5) -> T:
    async with asyncio.timeout(timeout):
        while not future.done():
            await asyncio.sleep(0.05)
        return future.result()
