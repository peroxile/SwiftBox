"""
Append a StateRecord as a structured JSON line to a log file.
This is the audit trail. Every action SwiftBox takes should appear here.
Log path is read from host config (notifications.log_path).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.schemas import StateRecord

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = "logs/swiftbox.log"


def _serialize(obj: Any) -> Any:
    """Recursively serialize dataclasses and enums to JSON-safe types."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _serialize(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    if hasattr(obj, "value"):   # Enum
        return obj.value
    return obj


def emit(record: StateRecord, log_path: str | Path = DEFAULT_LOG_PATH) -> None:
    """Append one StateRecord as a JSON line to the log file."""
    path = Path(log_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = _serialize(record)
        # Ensure timestamp is always present in the log line
        if "timestamp" not in entry or not entry["timestamp"]:
            entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        # Never let a logging failure crash the engine
        logger.error("filelog: could not write to %s: %s", log_path, e)


def emit_all(records: list[StateRecord], log_path: str | Path = DEFAULT_LOG_PATH) -> None:
    for record in records:
        emit(record, log_path)
