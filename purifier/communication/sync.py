"""Outbox retries depend solely on backend commit ACKs, never PUBACK."""

from typing import Any, Protocol

from purifier.communication.schemas import persistence_ack
from purifier.storage.repositories import Repository
from purifier.utils.async_bridge import committed


class Publisher(Protocol):
    def publish(self, suffix: str, payload: dict[str, Any], qos: int = 1,
                retain: bool = False) -> bool: ...


class Sync:
    def __init__(self, repository: Repository, publisher: Publisher, batch_size: int = 20) -> None:
        if repository.device_id is None:
            raise ValueError('registered device UUID required for sync')
        if not 1 <= batch_size <= 20:
            raise ValueError('batch size must be 1..20')
        self.batch_size = batch_size
        self.repository, self.publisher = repository, publisher
        self.device_id = repository.device_id
        self.next_attempt = 0.0
        self.backoff = 1.0

    async def step(self, monotonic: float) -> int:
        if monotonic < self.next_attempt:
            return 0
        events = await committed(self.repository.pending(self.batch_size))
        sent = 0
        for event in events:
            if event.get('device_id') != self.device_id:
                raise ValueError('outbox device mismatch')
            if not self.publisher.publish('ack' if event.get('type') == 'command_ack' else 'events', event):
                self.backoff = min(60, self.backoff * 2)
                self.next_attempt = monotonic + self.backoff
                return sent
            sent += 1
        self.backoff = 1
        self.next_attempt = monotonic + 15
        return sent

    async def ack(self, data: bytes) -> bool:
        event_id, revision = persistence_ack(data, self.device_id)
        return bool(await committed(self.repository.acknowledge(event_id, revision, self.device_id)))
