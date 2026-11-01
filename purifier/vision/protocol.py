"""Bounded metadata-only recognition protocol; images/embeddings are forbidden."""

import json
import math
from uuid import UUID

from purifier.core.state import Recognition
from purifier.communication.schemas import _pairs, uuid

MAX_MESSAGE = 2048


def request(request_id: str, session_id: str) -> bytes:
    UUID(request_id)
    UUID(session_id)
    return (json.dumps({'schema_version': 1, 'request_id': request_id,
                        'session_id': session_id, 'type': 'identify'}) + '\n').encode()


def parse_request(data: bytes) -> tuple[str, str]:
    if len(data) > MAX_MESSAGE:
        raise ValueError('oversized vision request')
    raw = json.loads(data, object_pairs_hook=_pairs)
    if set(raw) != {'schema_version', 'request_id', 'session_id', 'type'} or type(raw['schema_version']) is not int or raw['schema_version'] != 1 or raw['type'] != 'identify':
        raise ValueError('invalid vision request')
    uuid(raw['request_id'])
    uuid(raw['session_id'])
    return raw['request_id'], raw['session_id']


def response(result: Recognition) -> bytes:
    return (json.dumps({'schema_version': 1, 'request_id': result.request_id,
        'session_id': result.session_id, 'status': result.status,
        'user_id': result.user_id, 'similarity': result.similarity}, allow_nan=False) + '\n').encode()


def parse_response(data: bytes, request_id: str, session_id: str) -> Recognition:
    if len(data) > MAX_MESSAGE:
        raise ValueError('oversized vision result')
    raw = json.loads(data, object_pairs_hook=_pairs)
    if set(raw) != {'schema_version', 'request_id', 'session_id', 'status', 'user_id', 'similarity'}:
        raise ValueError('invalid vision result')
    if type(raw['schema_version']) is not int or raw['schema_version'] != 1 or raw['request_id'] != request_id or raw['session_id'] != session_id:
        raise ValueError('stale or mismatched vision result')
    if raw['status'] not in ('matched', 'guest', 'unavailable', 'busy'):
        raise ValueError('invalid recognition status')
    if raw['status'] == 'matched':
        uuid(raw['user_id'])
        value = raw['similarity']
        if type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1:
            raise ValueError('invalid similarity')
    elif raw['user_id'] != 'guest' or raw['similarity'] is not None:
        raise ValueError('invalid Guest result')
    return Recognition(request_id, session_id, raw['status'], raw['user_id'], raw['similarity'])
