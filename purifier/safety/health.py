"""Low-frequency local health snapshot; missing metrics remain null."""

import shutil
from pathlib import Path
from typing import Any

from purifier.storage.sqlite import Writer


def snapshot(writer: Writer, root: Path = Path('/')) -> dict[str, Any]:
    rss_kib = None
    temperature_c = None
    try:
        with (root / 'proc/self/status').open() as stream:
            status = stream.read(16384)
        for line in status.splitlines():
            if line.startswith('VmRSS:'):
                rss_kib = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        with (root / 'sys/class/thermal/thermal_zone0/temp').open() as stream:
            temperature_c = int(stream.read(32)) / 1000
    except (OSError, ValueError):
        pass
    try:
        free_bytes = shutil.disk_usage(writer.path.parent).free
    except OSError:
        free_bytes = None
    return {'core_rss_kib': rss_kib, 'cpu_temperature_c': temperature_c,
            'disk_free_bytes': free_bytes, 'writer_depth': writer.depth,
            'storage_healthy': writer.healthy, 'throttling': None,
            'vision': 'disabled', 'mqtt': 'disabled'}
