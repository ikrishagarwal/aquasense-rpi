"""Atomic persistence and revision-aware commit acknowledgements."""

import json
import sqlite3
from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Any

from purifier.storage.sqlite import Writer


def encode(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)


def persist_session(db: sqlite3.Connection, event: dict[str, Any]) -> None:
    event_id, revision = event['event_id'], event['revision']
    payload = encode(event)
    old = db.execute('SELECT revision, payload FROM dispense_sessions WHERE event_id=?', (event_id,)).fetchone()
    if old is not None:
        if revision == old[0] and payload == old[1]:
            return
        if revision != old[0] + 1:
            raise ValueError('revision must advance by exactly one')
        previous = json.loads(old[1])
        for field in ('volume_ml', 'pulse_count', 'start_ts', 'end_ts', 'device_id', 'volume_quality'):
            if event.get(field) != previous.get(field):
                raise ValueError('identity revision cannot change measurement')
        db.execute('UPDATE dispense_sessions SET revision=?, payload=? WHERE event_id=?', (revision, payload, event_id))
    else:
        if revision != 1:
            raise ValueError('initial revision must be one')
        db.execute('INSERT INTO dispense_sessions VALUES (?, ?, ?)', (event_id, revision, payload))
        if event['volume_quality'] == 'measured':
            db.execute("UPDATE counters SET value=value+? WHERE name='filter_ml'", (event['volume_ml'],))
    db.execute('INSERT INTO outbox(event_id, revision, payload) VALUES (?, ?, ?)', (event_id, revision, payload))
    db.execute('DELETE FROM active_sessions WHERE event_id=?', (event_id,))


class Repository:
    def __init__(self, writer: Writer, device_id: str | None) -> None:
        self.writer, self.device_id = writer, device_id

    def save(self, event: dict[str, Any]) -> Future[Any]:
        snapshot = json.loads(encode(dict(event, device_id=self.device_id)))
        return self.writer.submit(lambda db: persist_session(db, snapshot))

    def checkpoint(self, event: dict[str, Any]) -> Future[Any]:
        snapshot = dict(event, device_id=self.device_id)
        payload = encode(snapshot)
        return self.writer.submit(lambda db: db.execute(
            'INSERT INTO active_sessions VALUES (?, ?) ON CONFLICT(event_id) DO UPDATE SET payload=excluded.payload',
            (snapshot['event_id'], payload)) and None)

    def recover(self) -> Future[Any]:
        def work(db: sqlite3.Connection) -> int:
            rows = db.execute('SELECT payload FROM active_sessions').fetchall()
            for (payload,) in rows:
                event = json.loads(payload)
                event['incomplete'] = True
                event['quality_flags'] = list(set(event['quality_flags']) | {'interrupted'})
                persist_session(db, event)
            # Interrupted drains are never resumed.
            now = datetime.now(timezone.utc).isoformat()
            for command_id, raw in db.execute("SELECT command_id,payload FROM command_log WHERE status IN ('received', 'accepted', 'running')").fetchall():
                payload = json.loads(raw)
                revision = db.execute('SELECT COUNT(*) FROM command_history WHERE command_id=?', (command_id,)).fetchone()[0] + 1
                payload.update(status='failed', reason='restart', timestamp=now,
                               revision=revision, valve_state='commanded_closed')
                db.execute('INSERT INTO outbox(event_id,revision,payload) VALUES (?,?,?)',
                           (command_id, revision, encode(payload)))
                db.execute("UPDATE command_log SET status='failed', reason='restart', ts=?, payload=? WHERE command_id=?", (now, encode(payload), command_id))
                db.execute("INSERT INTO command_history(command_id,status,reason,ts) VALUES (?, 'failed', 'restart', ?)", (command_id, now))
            return len(rows)
        return self.writer.submit(work)

    def pending(self, limit: int = 20) -> Future[Any]:
        if not 1 <= limit <= 20:
            raise ValueError('outbox batch must be 1..20')
        return self.writer.submit(lambda db: [json.loads(row[0]) for row in db.execute(
            'SELECT payload FROM outbox WHERE acknowledged=0 ORDER BY rowid LIMIT ?', (limit,))])

    def acknowledge(self, event_id: str, revision: int, device_id: str) -> Future[Any]:
        if self.device_id is None or device_id != self.device_id:
            raise ValueError('ack device mismatch')
        def work(db: sqlite3.Connection) -> bool:
            row = db.execute(
                'SELECT payload FROM outbox WHERE event_id=? AND revision=?',
                (event_id, revision),
            ).fetchone()
            if row is None or json.loads(row[0]).get('device_id') != device_id:
                return False
            db.execute(
                'UPDATE outbox SET acknowledged=1 WHERE event_id=? AND revision=?',
                (event_id, revision),
            )
            return True
        return self.writer.submit(work)

    def maintain(self, sensor_limit: int = 1000) -> Future[Any]:
        if sensor_limit < 0:
            raise ValueError('invalid retention limit')
        def work(db: sqlite3.Connection) -> str:
            db.execute('DELETE FROM sensor_samples WHERE id NOT IN (SELECT id FROM sensor_samples ORDER BY id DESC LIMIT ?)', (sensor_limit,))
            db.execute('DELETE FROM system_events WHERE id NOT IN (SELECT id FROM system_events ORDER BY id DESC LIMIT 1000)')
            return str(db.execute('PRAGMA quick_check').fetchone()[0])
        return self.writer.submit(work)

    def checkpoint_wal(self) -> Future[Any]:
        return self.writer.submit(lambda db: tuple(db.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()))
