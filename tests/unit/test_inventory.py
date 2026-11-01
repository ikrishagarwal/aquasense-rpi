import tempfile
import unittest
from pathlib import Path

from purifier.utils.inventory import collect, read_fact


class InventoryTests(unittest.TestCase):
    def test_available_board_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model"
            path.write_bytes(b"Raspberry Pi 3 Model B Plus\x00")
            self.assertEqual(read_fact(path)["value"], "Raspberry Pi 3 Model B Plus")

    def test_missing_fact_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_fact(Path(tmp) / "absent"), {"status": "unavailable"})

    def test_unreadable_fact_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_fact(Path(tmp))["status"], "error")

    def test_bounded_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model"
            path.write_bytes(b"x" * 4097)
            self.assertEqual(read_fact(path)["error_type"], "oversized")

    def test_invalid_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model"
            path.write_bytes(b"\xff")
            self.assertEqual(read_fact(path)["error_type"], "invalid_utf8")

    def test_missing_hardware_never_reports_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts = collect(Path(tmp))
            self.assertFalse(facts["hardware_verified"])
            self.assertFalse(facts["drain_enabled"])
            self.assertEqual(facts["board_model"], {"status": "unavailable"})
