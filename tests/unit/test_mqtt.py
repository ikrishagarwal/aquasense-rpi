import ssl
import unittest
from types import SimpleNamespace
from uuid import uuid4

from purifier.communication.mqtt import MQTT


class MQTTTests(unittest.TestCase):
    def test_tls_queue_limits_and_overflow_disconnect(self):
        class FakeClient:
            def __init__(self):
                self.calls = {}
            def __getattr__(self, name):
                def called(*args, **kwargs):
                    self.calls[name] = (args, kwargs)
                    return None
                return called
        client = FakeClient()
        transport = MQTT(str(uuid4()), 'broker.example', 'user', 'private', client=client)
        context = client.calls['tls_set_context'][0][0]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(client.calls['max_inflight_messages_set'][0], (20,))
        self.assertEqual(client.calls['max_queued_messages_set'][0], (40,))
        message = SimpleNamespace(topic=transport.prefix+'/cmd', payload=b'{}', retain=False)
        for _ in range(65):
            transport._message(client, None, message)
        self.assertEqual(transport.messages.qsize(), 64)
        self.assertEqual(transport.fault, 'inbound_queue_full')
        self.assertIn('disconnect', client.calls)
        self.assertFalse(transport.publish('events', {}))
        with self.assertRaises(ValueError):
            transport.publish('cmd', {}, retain=True)
