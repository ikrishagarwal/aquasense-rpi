import os
import pty
import termios
import unittest

from purifier.arduino_monitor import PourMonitor, open_port, read_reports


class PourMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages: list[str] = []
        self.monitor = PourMonitor(self.messages.append)

    def test_short_pause_merges_and_latest_tds_is_reported(self) -> None:
        for now, line in [
            (0.0, b"80,0,100"),
            (0.5, b"81,2.22,102.22"),
            (1.0, b"82,0,102.22"),
            (2.5, b"83,0,102.22"),
            (3.5, b"84,4.44,106.66"),
            (4.0, b"85,0,106.66"),
            (5.5, b"86,0,106.66"),
            (6.5, b"87,0,106.66"),
        ]:
            self.monitor.receive(line, now)
        self.assertEqual(self.messages, [])
        self.monitor.receive(b"88,0,106.66", 7.0)
        self.monitor.receive(b"89,0,106.66", 7.5)
        self.assertEqual(
            self.messages, ["User: Guest | TDS estimate: 88.0 ppm | Dispensed: 6.7 mL"]
        )

    def test_new_flow_at_idle_boundary_starts_separate_pour(self) -> None:
        self.monitor.receive(b"80,2.22,2.22", 0.0)
        for now in (0.5, 1.5, 2.5):
            self.monitor.receive(b"81,0,2.22", now)
        self.monitor.receive(b"90,4.44,6.67", 3.5)
        for now in (4.0, 5.5, 7.0):
            self.monitor.receive(b"91,0,6.67", now)
        self.assertEqual(
            self.messages,
            [
                "User: Guest | TDS estimate: 81.0 ppm | Dispensed: 2.2 mL",
                "User: Guest | TDS estimate: 91.0 ppm | Dispensed: 4.4 mL",
            ],
        )

    def test_unavailable_tds_does_not_invalidate_volume(self) -> None:
        self.monitor.receive(b",2.22,2.22", 0.0)
        for now, tds in [(0.5, b"nan"), (1.5, b"bad"), (2.5, b"-1"), (3.5, b"inf")]:
            self.monitor.receive(tds + b",0,2.22", now)
        self.assertEqual(
            self.messages,
            ["User: Guest | TDS estimate: unavailable | Dispensed: 2.2 mL"],
        )

    def test_missing_tds_preserves_last_valid_reading_of_pour(self) -> None:
        self.monitor.receive(b"80,2.22,2.22", 0.0)
        for now in (0.5, 1.5, 2.5, 3.5):
            self.monitor.receive(b",0,2.22", now)
        self.assertEqual(
            self.messages[-1],
            "User: Guest | TDS estimate: 80.0 ppm | Dispensed: 2.2 mL",
        )

    def test_corrupt_report_or_timeout_finishes_pour_and_rebaselines(self) -> None:
        cases = [
            (b"partial", "malformed flow report"),
            (b"82,nan,102.22", "malformed flow report"),
            (b"82,-1,102.22", "malformed flow report"),
            (b"82,4.44,2.22", "malformed flow report"),
            (b"82,0,104.44", "inconsistent cumulative total"),
            (b"82,2.22,110", "inconsistent cumulative total"),
            (None, "serial timeout"),
        ]
        for broken, reason in cases:
            with self.subTest(broken=broken):
                messages: list[str] = []
                monitor = PourMonitor(messages.append)
                monitor.receive(b"80,2.22,102.22", 0.0)
                monitor.receive(b"81,0,102.22", 0.5)
                monitor.receive(broken, 2.5)
                monitor.receive(b"82,4.44,110.00", 3.0)
                for now in (3.5, 4.5, 5.5, 6.5):
                    monitor.receive(b"83,0,110.00", now)
                self.assertEqual(
                    messages,
                    [
                        "User: Guest | TDS estimate: 81.0 ppm | "
                        f"Incomplete pour ({reason}): 2.2 mL",
                        "User: Guest | TDS estimate: 83.0 ppm | Dispensed: 4.4 mL",
                    ],
                )

    def test_reset_report_becomes_new_baseline_even_when_total_increases(self) -> None:
        for old_total in (b"2.22", b"102.22"):
            with self.subTest(old_total=old_total):
                messages: list[str] = []
                monitor = PourMonitor(messages.append)
                monitor.receive(b"80,2.22," + old_total, 0.0)
                monitor.receive(b"81,4.44,4.44", 0.5)
                for now in (1.0, 2.0, 3.0, 4.0):
                    monitor.receive(b"82,0,4.44", now)
                self.assertEqual(
                    messages,
                    [
                        "User: Guest | TDS estimate: 80.0 ppm | Incomplete pour "
                        "(Arduino reset or decreasing total): 2.2 mL",
                        "User: Guest | TDS estimate: 82.0 ppm | Dispensed: 4.4 mL",
                    ],
                )


class SerialReaderTests(unittest.TestCase):
    def test_serial_configuration_framing_timeout_and_recovery(self) -> None:
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        with open_port(os.ttyname(slave)) as fd:
            settings = termios.tcgetattr(fd)
            self.assertEqual(settings[4:6], [termios.B9600, termios.B9600])
            self.assertFalse(settings[3] & termios.ICANON)
            reports = read_reports(fd, timeout_s=0.05)
            os.write(master, b"80,2.22,2.22\r\n81,0,")
            self.assertEqual(next(reports), b"80,2.22,2.22\r")
            os.write(master, b"2.22\r\n82,0,")
            self.assertEqual(next(reports), b"81,0,2.22\r")
            self.assertIsNone(next(reports))
            os.write(master, b"2.22\r\n" + b"x" * 300 + b"83,0,2.22\n84,0,2.22\n")
            self.assertEqual(next(reports), b"")
            self.assertEqual(next(reports), b"")
            self.assertEqual(next(reports), b"84,0,2.22")

    def test_disconnect_interrupts_active_pour(self) -> None:
        master, slave = pty.openpty()
        self.addCleanup(os.close, slave)
        messages: list[str] = []
        monitor = PourMonitor(messages.append)
        try:
            with open_port(os.ttyname(slave)) as fd:
                reports = read_reports(fd)
                os.write(master, b"80,2.22,2.22\n")
                monitor.receive(next(reports), 0.0)
                os.close(master)
                master = -1
                with self.assertRaises(OSError):
                    next(reports)
                monitor.interrupt("serial connection lost")
        finally:
            if master >= 0:
                os.close(master)
        self.assertEqual(
            messages,
            [
                "User: Guest | TDS estimate: 80.0 ppm | "
                "Incomplete pour (serial connection lost): 2.2 mL",
            ],
        )
