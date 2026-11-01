"""Optional Paho v2 transport: verified TLS, bounded queues, no blocking publishes."""

import json
import queue
import random
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from purifier.communication.schemas import MAX_PAYLOAD, uuid


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes
    retained: bool


class MQTT:
    def __init__(self, device_id: str, host: str, username: str, password: str,
                 ca_file: Path | None = None, port: int = 8883, client: Any = None) -> None:
        uuid(device_id)
        if not host or not username or not password or not 1 <= port <= 65535:
            raise ValueError('broker and per-device credentials required')
        self.prefix = f'purifier/{device_id}'
        self.host, self.port = host, port
        self.messages: queue.Queue[Message] = queue.Queue(64)
        self.connected = False
        self.fault: str | None = None
        context = ssl.create_default_context(cafile=str(ca_file) if ca_file else None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if client is None:
            import paho.mqtt.client as paho
            client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=device_id)
        self.client = client
        client.tls_set_context(context)
        client.username_pw_set(username, password)
        client.max_inflight_messages_set(20)
        client.max_queued_messages_set(40)
        # Paho exponential reconnect delay with per-client startup jitter.
        client.reconnect_delay_set(min_delay=random.randint(1, 3), max_delay=60)
        client.will_set(f'{self.prefix}/status', json.dumps({'schema_version': 1,
            'device_id': device_id, 'status': 'offline'}), qos=1, retain=True)
        client.on_connect = self._connect
        client.on_disconnect = self._disconnect
        client.on_message = self._message

    def _connect(self, client: Any, userdata: Any, flags: Any, reason: Any, properties: Any) -> None:
        self.connected = reason == 0
        if self.connected:
            result, _ = client.subscribe([(f'{self.prefix}/events_ack', 1), (f'{self.prefix}/cmd', 1)])
            if result != 0:
                self.fault = 'subscription_failed'
                self.connected = False
                client.disconnect()
            else:
                self.publish('status', {'schema_version': 1,
                    'device_id': self.prefix.split('/')[1], 'status': 'online'}, 1, True)

    def _disconnect(self, client: Any, userdata: Any, flags: Any, reason: Any, properties: Any) -> None:
        self.connected = False

    def _message(self, client: Any, userdata: Any, message: Any) -> None:
        if message.topic not in (f'{self.prefix}/events_ack', f'{self.prefix}/cmd'):
            return
        if len(message.payload) > MAX_PAYLOAD:
            self.fault = 'oversized_message'
            return
        try:
            self.messages.put_nowait(Message(message.topic, bytes(message.payload), bool(message.retain)))
        except queue.Full:
            self.fault = 'inbound_queue_full'
            self.connected = False
            client.disconnect()

    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def publish(self, suffix: str, payload: dict[str, Any], qos: int = 1, retain: bool = False) -> bool:
        if suffix not in ('events', 'telemetry', 'ack', 'status') or (retain and suffix != 'status'):
            raise ValueError('invalid outbound topic/retention')
        data = json.dumps(payload, allow_nan=False).encode()
        if len(data) > MAX_PAYLOAD:
            raise ValueError('outbound message too large')
        if not self.connected:
            return False
        return bool(self.client.publish(f'{self.prefix}/{suffix}', data, qos=qos, retain=retain).rc == 0)

    def close(self) -> None:
        self.connected = False
        self.client.disconnect()
        self.client.loop_stop()
