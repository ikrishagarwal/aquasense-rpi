"""Read-only host inventory. Does not import drivers or open device nodes."""

import platform
import sqlite3
from pathlib import Path


def read_fact(path: Path, limit: int = 4096) -> dict[str, str]:
    """Read a bounded text fact, preserving missing and unreadable states."""
    try:
        with path.open("rb") as stream:
            value = stream.read(limit + 1)
    except FileNotFoundError:
        return {"status": "unavailable"}
    except OSError as exc:
        return {"status": "error", "error_type": type(exc).__name__}
    if len(value) > limit:
        return {"status": "error", "error_type": "oversized"}
    try:
        return {"status": "available", "value": value.decode("utf-8").strip("\x00\n")}
    except UnicodeDecodeError:
        return {"status": "error", "error_type": "invalid_utf8"}


def collect(root: Path = Path("/")) -> dict[str, object]:
    """Report observations only; this is not a hardware readiness check."""
    return {
        "schema_version": 1,
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "sqlite": sqlite3.sqlite_version,
        "os_release": read_fact(root / "etc/os-release"),
        "board_model": read_fact(root / "proc/device-tree/model"),
        "hardware_verified": False,
        "drain_enabled": False,
    }
