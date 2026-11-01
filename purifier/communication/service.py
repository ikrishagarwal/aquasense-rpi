"""Optional network supervision; malformed input never reaches valve operations."""

import asyncio
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from purifier.communication.mqtt import MQTT, Message
from purifier.communication.schemas import command
from purifier.communication.sync import Sync
from purifier.storage.repositories import Repository, encode
from purifier.utils.async_bridge import committed
from purifier.utils.logging import event


class NetworkService:
    def __init__(self, repository: Repository, transport: MQTT, batch_size: int = 20) -> None:
        self.repository, self.transport = repository, transport
        self.sync = Sync(repository, transport, batch_size)
        self.stop = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.transport.start()
        self.task = asyncio.create_task(self.run())

    async def receive(self, message: Message) -> None:
        if message.topic == f'{self.transport.prefix}/events_ack':
            await self.sync.ack(message.payload)
            return
        if message.topic != f'{self.transport.prefix}/cmd':
            raise ValueError('wrong topic')
        cmd = command(message.payload, self.sync.device_id, datetime.now(timezone.utc), message.retained)
        timestamp = datetime.now(timezone.utc).isoformat()
        def reject(db: sqlite3.Connection) -> None:
            row = db.execute('SELECT payload FROM command_log WHERE command_id=?', (cmd.command_id,)).fetchone()
            if row:
                # Re-arm durable final ACK on command replay without re-executing.
                db.execute('UPDATE outbox SET acknowledged=0 WHERE event_id=?', (cmd.command_id,))
                return
            db.execute('INSERT INTO command_log VALUES (?, ?, ?, ?, ?)',
                       (cmd.command_id, 'rejected', 'actuation_disabled', timestamp, '{}'))
            for revision, status in ((1, 'received'), (2, 'rejected')):
                payload: dict[str, Any] = {'schema_version': 1, 'device_id': cmd.device_id,
                    'event_id': cmd.command_id, 'command_id': cmd.command_id, 'revision': revision,
                    'type': 'command_ack', 'status': status, 'reason': 'actuation_disabled',
                    'timestamp': timestamp, 'valve_state': 'commanded_closed'}
                db.execute('INSERT INTO command_history(command_id,status,reason,ts) VALUES (?,?,?,?)',
                           (cmd.command_id, status, 'actuation_disabled', timestamp))
                db.execute('INSERT INTO outbox(event_id,revision,payload) VALUES (?,?,?)',
                           (cmd.command_id, revision, encode(payload)))
                db.execute('UPDATE command_log SET payload=? WHERE command_id=?', (encode(payload), cmd.command_id))
        await committed(self.repository.writer.submit(reject))

    async def run(self) -> None:
        try:
            while not self.stop.is_set():
                for _ in range(20):
                    try:
                        message = self.transport.messages.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        await self.receive(message)
                    except (ValueError, TypeError, KeyError):
                        event('mqtt_message_rejected', 30)
                await self.sync.step(asyncio.get_running_loop().time())
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=0.1)
                except TimeoutError:
                    continue
        except (OSError, RuntimeError, sqlite3.Error, ValueError):
            event('mqtt_worker_unavailable', 40)

    async def close(self) -> None:
        self.stop.set()
        if self.task is not None:
            done, _ = await asyncio.wait({self.task}, timeout=1)
            if not done:
                self.task.cancel()
                await asyncio.wait({self.task}, timeout=0.2)
        # Paho stop may wait on a network thread. Never block valve closure/core.
        thread = threading.Thread(target=self.transport.close, name='mqtt-stop', daemon=True)
        thread.start()
        deadline = asyncio.get_running_loop().time() + 2
        while thread.is_alive() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        if thread.is_alive():
            event('mqtt_shutdown_deadline', 40)
