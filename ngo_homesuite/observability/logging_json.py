from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ngo_homesuite.observability.context import get_request_id


class JsonLogFormatter(logging.Formatter):
    """Minimal JSON formatter for structured application logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        event_id = getattr(record, "event_id", None)
        if event_id:
            payload["event_id"] = event_id
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True)


def configure_json_logging(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.setFormatter(JsonLogFormatter())
