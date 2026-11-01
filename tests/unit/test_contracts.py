import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from purifier.communication.schemas import command, persistence_ack

ROOT = Path(__file__).resolve().parents[2]/'contracts'


class ContractFixtureTests(unittest.TestCase):
    def test_wire_fixtures(self):
        event = json.loads((ROOT/'dispense-v1.json').read_text())
        ack = persistence_ack((ROOT/'events-ack-v1.json').read_bytes(), event['device_id'])
        self.assertEqual(ack, (event['event_id'], event['revision']))
        cmd = command((ROOT/'drain-v1.json').read_bytes(), event['device_id'],
                      datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertEqual(cmd.duration_s, 5)
        with self.assertRaises(ValueError):
            command((ROOT/'drain-v1.json').read_bytes(), event['device_id'],
                    datetime(2026, 11, 2, tzinfo=timezone.utc))
