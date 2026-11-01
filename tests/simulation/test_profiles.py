"""M0 template guardrails; deterministic device simulation begins in M2."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ProfileTests(unittest.TestCase):
    def test_shipped_profiles_disable_outputs_and_external_services(self) -> None:
        for name in ("default", "simulation", "hardware"):
            with self.subTest(profile=name):
                config = json.loads((ROOT / "config" / f"{name}.yaml").read_text())
                self.assertFalse(config["hardware_verified"])
                self.assertFalse(config["drain"]["enabled"])
                self.assertIsNone(config["drain"]["driver_active_high"])
                self.assertFalse(config["drain"]["independent_cutoff_verified"])
                self.assertFalse(config["mqtt"]["enabled"])
                self.assertTrue(config["mqtt"]["verify_tls"])
                self.assertFalse(config["vision"]["enabled"])
                self.assertFalse(config["flow"]["calibrated"])
                self.assertIsNone(config["device_id"])
                pins = list(config["pins_bcm"].values())
                self.assertEqual(len(pins), len(set(pins)))
                self.assertTrue(
                    all(type(pin) is int and 0 <= pin <= 27 for pin in pins)
                )

    def test_default_is_simulation(self) -> None:
        config = json.loads((ROOT / "config/default.yaml").read_text())
        self.assertEqual(config["mode"], "simulation")
