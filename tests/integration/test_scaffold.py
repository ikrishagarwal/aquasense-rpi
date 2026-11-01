import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ScaffoldTests(unittest.TestCase):
    def test_cli_refuses_runtime_for_every_profile(self) -> None:
        for name in ("hardware", "absent"):
            with self.subTest(profile=name):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "purifier",
                        "--config",
                        f"config/{name}.yaml",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("configuration error", result.stderr)

    def test_inventory_runs_without_pi(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.check_hardware"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        self.assertFalse(json.loads(result.stdout)["hardware_verified"])
