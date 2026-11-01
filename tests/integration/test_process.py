import json
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ProcessTests(unittest.TestCase):
    def test_sigterm_and_sigint_shutdown(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=sig), tempfile.TemporaryDirectory() as tmp:
                config = json.loads((ROOT/'config/simulation.yaml').read_text())
                database = Path(tmp)/'state.db'
                config['storage']['path'] = str(database)
                path = Path(tmp)/'profile.yaml'
                path.write_text(json.dumps(config))
                proc = subprocess.Popen([sys.executable, '-m', 'purifier', '--config', str(path)],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    ready, _, _ = select.select([proc.stderr], [], [], 3)
                    self.assertTrue(ready, 'startup did not report ready')
                    self.assertIn('controller_started', proc.stderr.readline())
                    proc.send_signal(sig)
                    _, stderr = proc.communicate(timeout=3)
                    self.assertEqual(proc.returncode, 0, stderr)
                    self.assertIn('controller_stopped', stderr)
                    with closing(sqlite3.connect(database)) as db:
                        self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.communicate()

    def test_unwritable_database_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = json.loads((ROOT/'config/simulation.yaml').read_text())
            config['storage']['path'] = tmp  # a directory is not a SQLite database
            path = Path(tmp)/'profile.yaml'
            path.write_text(json.dumps(config))
            result = subprocess.run([sys.executable, '-m', 'purifier', '--config', str(path), '--run-for', '.2'],
                cwd=ROOT, capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('Traceback', result.stderr)
