"""Allowlisted structured events: arbitrary messages/exception text never logged."""

import json
import logging
from datetime import datetime, timezone


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "event": getattr(record, "event_code", "redacted_log"),
        }
        return json.dumps(payload)


def configure() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter())
    root = logging.getLogger("purifier")
    root.handlers = [handler]
    root.propagate = False
    root.setLevel(logging.INFO)


def event(code: str, level: int = logging.INFO) -> None:
    logging.getLogger("purifier").log(level, "", extra={"event_code": code})
