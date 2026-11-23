from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

REDACTED_KEYS = frozenset({
    "applicant_name", "date_of_birth", "dob", "ssn", "notes", "secret",
    "authorization", "password", "token",
})


def sanitize_mapping(data: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in REDACTED_KEYS:
            cleaned[key] = "[REDACTED]"
        elif isinstance(value, dict):
            cleaned[key] = sanitize_mapping(value)
        elif isinstance(value, (list, tuple)):
            cleaned[key] = [sanitize_mapping(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value
    return cleaned


class JsonRedactingFormatter(logging.Formatter):
    """Emit one JSON object per log line and redact sensitive structured fields."""

    RESERVED = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message",
    })

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra_fields = {key: value for key, value in record.__dict__.items() if key not in self.RESERVED}
        if extra_fields:
            payload["context"] = sanitize_mapping(extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str = "beast_care_automation", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonRedactingFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
