import asyncio
import copy
import io
import json
import logging
import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
from pathlib import Path

from purifier.core.config import ConfigError, load_config
from purifier.core.runtime import run
from purifier.storage.sqlite import StorageError, Writer
from purifier.utils.logging import SafeFormatter

PROFILE = json.loads(Path('config/simulation.yaml').read_text())


class ConfigTests(unittest.TestCase):
    def test_invalid_configuration(self) -> None:
        changes = [
            lambda c: c.update(mode='typo'),
            lambda c: c.update(extra=True),
            lambda c: c['flow'].update(pulses_per_litre=True),
            lambda c: c['flow'].update(pulses_per_litre=float('nan')),
            lambda c: c['pins_bcm'].update(flow=4),
            lambda c: c['mqtt'].update(verify_tls=False),
            lambda c: c['drain'].update(enabled=True),
            lambda c: c.update(mode='hardware'),
            lambda c: c['storage'].update(writer_queue_capacity=0),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            for change in changes:
                config = copy.deepcopy(PROFILE)
                change(config)
                path.write_text(json.dumps(config))
                with self.assertRaises(ConfigError):
                    load_config(path)
            path.write_text('{"mode": "simulation", "mode": "hardware"}')
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_valid_configuration(self) -> None:
        self.assertEqual(load_config(Path('config/simulation.yaml')).merge_timeout_s, 3)

    def test_logs_exclude_message_and_exception(self) -> None:
        record = logging.LogRecord('purifier', 40, '', 0, 'secret embedding', (), None)
        record.exc_text = 'password'
        output = SafeFormatter().format(record)
        self.assertNotIn('secret', output)
        self.assertNotIn('password', output)


class WriterTests(unittest.TestCase):
    def test_commit_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = Writer(Path(tmp) / 'db')
            writer.start()
            writer.submit(lambda db: db.execute('CREATE TABLE sample (id INTEGER)')).result(2)
            def fail(db):
                db.execute('INSERT INTO sample VALUES (1)')
                raise sqlite3.OperationalError('simulated full disk')
            with self.assertRaises(sqlite3.OperationalError):
                writer.submit(fail).result(2)
            self.assertFalse(writer.healthy)
            writer.close()
            with closing(sqlite3.connect(writer.path)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sample').fetchone()[0], 0)

    def test_queue_full_fault_is_explicit_and_pending_work_drains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = Writer(Path(tmp) / 'db', 1)
            writer.start()
            started, release = threading.Event(), threading.Event()
            def slow(db):
                started.set()
                release.wait(2)
            first = writer.submit(slow)
            self.assertTrue(started.wait(1))
            second = writer.submit(lambda db: 7)
            try:
                with self.assertRaises(StorageError):
                    writer.submit(lambda db: 8)
            finally:
                release.set()
                writer.close()
            self.assertIsNone(first.result())
            self.assertEqual(second.result(), 7)

    def test_shutdown_closes_valve_before_writer(self) -> None:
        calls = []
        class Valve:
            def close(self):
                calls.append('close')
        class FailedWriter:
            async def start_async(self):
                calls.append('start')
                raise StorageError('unavailable')
            async def close_async(self):
                calls.append('flush')
        async def exercise():
            with self.assertRaises(StorageError):
                await run(FailedWriter(), Valve(), asyncio.Event())
        asyncio.run(exercise())
        self.assertEqual(calls, ['close', 'start', 'close', 'flush'])


class YAMLTests(unittest.TestCase):
    def test_yaml_and_duplicate_rejection(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('optional YAML parser unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'profile.yaml'
            path.write_text(yaml.safe_dump(PROFILE))
            self.assertEqual(load_config(path).mode, 'simulation')
            path.write_text('mode: simulation\nmode: hardware\n')
            with self.assertRaises(ConfigError):
                load_config(path)
            path.write_text('mode: &m simulation\nother: *m\n')
            with self.assertRaises(ConfigError):
                load_config(path)
