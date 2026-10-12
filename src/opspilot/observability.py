"""Structured JSON logs and CloudWatch Embedded Metric Format (EMF) metrics.

Both are plain stdout lines, so there is nothing to install and nothing to pay for:
CloudWatch Logs ingests the line and, for EMF, turns it into a custom metric.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

NAMESPACE = "OpsPilot"
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                doc[key] = value
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


def get_logger(name: str = "opspilot") -> logging.Logger:
    """Children of the "opspilot" logger share one JSON handler and one level."""
    root = logging.getLogger("opspilot")
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        root.addHandler(handler)
        root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
        root.propagate = False
    return logging.getLogger(name)


def emit_metric(name: str, value: float = 1, unit: str = "Count") -> None:
    """Write one EMF line. A single Environment dimension keeps the metric count low,
    which matters because CloudWatch gives 10 custom metrics free.
    Set EMF_DISABLED=1 to silence it (tests and the offline CLI do)."""
    if os.environ.get("EMF_DISABLED"):
        return
    environment = os.environ.get("ENVIRONMENT", "dev")
    doc = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [["Environment"]],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        "Environment": environment,
        name: value,
    }
    print(json.dumps(doc), flush=True)
