import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from purifier.communication.vision_client import VisionClient
from purifier.core.state import Recognition
from purifier.vision.embedding_store import EmbeddingStore
from purifier.vision.matcher import match
from purifier.vision.protocol import parse_request, parse_response, request, response


class VisionTests(unittest.TestCase):
    def test_margin_is_between_people_not_samples(self):
        self.assertEqual(match([1, 0], {'a': [[1, 0], [1, 0]], 'b': [[0, 1]]}, .8, .1).user_id, 'a')
        self.assertEqual(match([1, 0], {'a': [[1, 0]], 'b': [[1, 0]]}, .8, .1).status, 'ambiguous')
        with self.assertRaises(ValueError):
            match([float('nan')], {}, .8, .1)

    def test_protocol_rejects_stale_or_image_fields(self):
        rid, sid = str(uuid4()), str(uuid4())
        self.assertEqual(parse_request(request(rid, sid)), (rid, sid))
        with self.assertRaises(ValueError):
            parse_request(b'{"image": "forbidden"}')
        with self.assertRaises(ValueError):
            parse_response(response(Recognition(rid, sid, 'guest')), str(uuid4()), sid)

    def test_missing_worker_is_guest(self):
        result = asyncio.run(VisionClient(Path('/nonexistent/worker.sock')).identify(str(uuid4()), str(uuid4())))
        self.assertEqual(result.user_id, 'guest')

    def test_encrypted_store_roundtrip_reenroll_delete_and_tamper(self):
        try:
            from cryptography.exceptions import InvalidTag
        except ImportError:
            self.skipTest('cryptography optional dependency unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / 'key'
            key.write_bytes(os.urandom(32))
            key.chmod(0o600)
            store = EmbeddingStore(Path(tmp)/'enrollments', key, 'test-model', 2)
            user = str(uuid4())
            store.enroll(user, [[1, 0]] * 3)
            self.assertEqual(store.load(user), [[1, 0]] * 3)
            store.enroll(user, [[0, 1]] * 3)
            self.assertEqual(store.load(user), [[0, 1]] * 3)
            path = store._path(user)
            encrypted = path.read_bytes()
            path.write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
            with self.assertRaises(InvalidTag):
                store.load(user)
            store.delete(user)
            self.assertFalse(path.exists())
