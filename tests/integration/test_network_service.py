import asyncio
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from purifier.communication.mqtt import Message
from purifier.communication.service import NetworkService
from purifier.storage.repositories import Repository
from purifier.storage.sqlite import Writer
from purifier.utils.async_bridge import committed
from purifier.utils.time import SystemClock


class NetworkServiceTests(unittest.TestCase):
    def test_runtime_rejects_drain_durably_and_replays_ack_without_action(self):
        async def exercise(path):
            writer = Writer(path)
            await writer.start_async()
            device = str(uuid4())
            repo = Repository(writer, device)
            transport = SimpleNamespace(prefix=f'purifier/{device}')
            service = NetworkService(repo, transport)
            now = SystemClock().utcnow()
            payload = {'schema_version': 1, 'command_id': str(uuid4()), 'device_id': device,
                'type': 'drain', 'issued_at': now.isoformat(),
                'expires_at': (now+timedelta(seconds=15)).isoformat(), 'duration_s': 5}
            message = Message(f'purifier/{device}/cmd', json.dumps(payload).encode(), False)
            try:
                await service.receive(message)
                await service.receive(message)
                pending = await committed(repo.pending())
                self.assertEqual([p['status'] for p in pending], ['received', 'rejected'])
                self.assertEqual(pending[-1]['reason'], 'actuation_disabled')
                self.assertEqual(pending[-1]['valve_state'], 'commanded_closed')
                with self.assertRaises(ValueError):
                    await service.receive(Message(message.topic, message.payload, True))
            finally:
                await writer.close_async()
        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(exercise(Path(tmp)/'db'))
