"""Strict bounded wire envelopes with device, UTC, expiry and replay identifiers."""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

MAX_PAYLOAD = 16384


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate message key')
        result[key] = value
    return result


def decode(data: bytes, keys: set[str]) -> dict[str, Any]:
    if len(data) > MAX_PAYLOAD:
        raise ValueError('message too large')
    raw = json.loads(data, object_pairs_hook=_pairs)
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError('invalid message fields')
    if type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
        raise ValueError('unsupported schema')
    return raw


def uuid(value: Any) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('canonical UUID required')
    return value


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError('timestamp must be text')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('UTC timestamp required')
    return parsed


@dataclass(frozen=True)
class DrainCommand:
    command_id: str
    device_id: str
    issued_at: datetime
    expires_at: datetime
    duration_s: float


def command(data: bytes, device_id: str, now: datetime, retained: bool = False,
            max_ttl: float = 30, max_duration: float = 15) -> DrainCommand:
    if retained:
        raise ValueError('retained_command')
    if now.utcoffset() is None:
        raise ValueError('trusted UTC clock unavailable')
    raw = decode(data, {'schema_version', 'command_id', 'device_id', 'type',
                        'issued_at', 'expires_at', 'duration_s'})
    if uuid(raw['device_id']) != device_id or raw['type'] != 'drain':
        raise ValueError('invalid device or command type')
    identity = uuid(raw['command_id'])
    issued, expires = timestamp(raw['issued_at']), timestamp(raw['expires_at'])
    if not issued <= now < expires or not 0 < (expires - issued).total_seconds() <= max_ttl:
        raise ValueError('invalid command expiry')
    duration = raw['duration_s']
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= max_duration:
        raise ValueError('invalid drain duration')
    return DrainCommand(identity, device_id, issued, expires, float(duration))


def persistence_ack(data: bytes, device_id: str) -> tuple[str, int]:
    raw = decode(data, {'schema_version', 'device_id', 'event_id', 'revision', 'status'})
    if uuid(raw['device_id']) != device_id or raw['status'] != 'committed':
        raise ValueError('ack is not a matching database commit')
    if type(raw['revision']) is not int or raw['revision'] < 1:
        raise ValueError('invalid revision')
    return uuid(raw['event_id']), raw['revision']
