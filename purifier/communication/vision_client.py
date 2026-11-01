"""Core-side IPC client; imports no image processing or vision runtime."""

import asyncio
import math
from pathlib import Path

from purifier.core.state import Recognition
from purifier.vision.protocol import MAX_MESSAGE, parse_response, request


class VisionClient:
    def __init__(self, socket: Path, deadline: float = 5) -> None:
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError('positive deadline required')
        self.socket, self.deadline = socket, deadline
        self.outstanding = 0

    async def identify(self, request_id: str, session_id: str) -> Recognition:
        guest = Recognition(request_id, session_id, 'unavailable')
        if self.outstanding >= 2:
            return Recognition(request_id, session_id, 'busy')
        self.outstanding += 1
        writer = None
        try:
            async with asyncio.timeout(self.deadline):
                reader, writer = await asyncio.open_unix_connection(str(self.socket), limit=MAX_MESSAGE)
                writer.write(request(request_id, session_id))
                await writer.drain()
                return parse_response(await reader.readline(), request_id, session_id)
        except (OSError, TimeoutError, ValueError, TypeError, KeyError):
            return guest
        finally:
            self.outstanding -= 1
            if writer is not None:
                writer.close()
                try:
                    async with asyncio.timeout(0.2):
                        await writer.wait_closed()
                except (OSError, TimeoutError):
                    pass
