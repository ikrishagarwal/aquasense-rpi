import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from purifier.core.session_manager import SessionManager
from purifier.storage.repositories import Repository
from purifier.storage.sqlite import Writer
from purifier.utils.time import FakeClock


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.db'
        self.writer = Writer(self.path)
        self.writer.start()
        self.device_id = str(uuid4())
        self.repo = Repository(self.writer, self.device_id)

    def tearDown(self):
        self.writer.close()
        self.tmp.cleanup()

    def event(self):
        clock = FakeClock()
        sessions = SessionManager(450, calibrated=True)
        sessions.pulse(0, clock.utcnow())
        return sessions.finish()

    def test_ten_offline_sessions_restart_and_revision_ack(self):
        events = [self.event() for _ in range(10)]
        for event in events:
            self.repo.save(event).result(2)
            self.repo.save(event).result(2)
        self.writer.close()
        self.writer = Writer(self.path)
        self.writer.start()
        self.repo = Repository(self.writer, self.device_id)
        self.assertEqual(len(self.repo.pending().result(2)), 10)
        updated = dict(events[0], revision=2, user_id=str(uuid4()))
        self.repo.save(updated).result(2)
        for event in events:
            self.repo.acknowledge(event['event_id'], 1, self.device_id).result(2)
        pending = self.repo.pending().result(2)
        self.assertEqual([p['revision'] for p in pending], [2])
        self.assertFalse(self.repo.acknowledge(updated['event_id'], 999, self.device_id).result(2))
        self.repo.acknowledge(updated['event_id'], 2, self.device_id).result(2)
        self.assertEqual(self.repo.pending().result(2), [])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT count(*) FROM dispense_sessions').fetchone()[0], 10)
            self.assertAlmostEqual(db.execute('SELECT value FROM counters').fetchone()[0], 10000/450)

    def test_recovery_marks_checkpoint_incomplete(self):
        event = self.event()
        self.repo.checkpoint(event).result(2)
        self.assertEqual(self.repo.recover().result(2), 1)
        pending = self.repo.pending().result(2)
        self.assertTrue(pending[0]['incomplete'])
        self.assertIn('interrupted', pending[0]['quality_flags'])
        self.assertEqual(self.repo.recover().result(2), 0)

    def test_outbox_failure_rolls_back_session(self):
        self.writer.submit(lambda db: db.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'simulated full disk'); END")).result(2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.save(self.event()).result(2)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dispense_sessions').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT value FROM counters').fetchone()[0], 0)
