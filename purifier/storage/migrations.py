"""Transactional schema installation; refuse unknown future database versions."""

import sqlite3

SCHEMA = (
    'CREATE TABLE dispense_sessions (event_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)',
    'CREATE TABLE active_sessions (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)',
    'CREATE TABLE outbox (event_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(event_id, revision))',
    'CREATE TABLE command_log (command_id TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL, ts TEXT NOT NULL, payload TEXT NOT NULL)',
    'CREATE TABLE command_history (id INTEGER PRIMARY KEY, command_id TEXT NOT NULL REFERENCES command_log(command_id), status TEXT NOT NULL, reason TEXT NOT NULL, ts TEXT NOT NULL)',
    'CREATE TABLE sensor_samples (id INTEGER PRIMARY KEY, ts TEXT NOT NULL, payload TEXT NOT NULL)',
    'CREATE TABLE enrollments (user_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL)',
    'CREATE TABLE counters (name TEXT PRIMARY KEY, value REAL NOT NULL)',
    "INSERT INTO counters VALUES ('filter_ml', 0)",
    'CREATE TABLE alert_state (name TEXT PRIMARY KEY, active INTEGER NOT NULL)',
)


def migrate(db: sqlite3.Connection) -> None:
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version not in (0, 1):
        raise RuntimeError('unsupported database schema version')
    if version == 0:
        for statement in SCHEMA:
            db.execute(statement)
        db.execute('PRAGMA user_version=1')
