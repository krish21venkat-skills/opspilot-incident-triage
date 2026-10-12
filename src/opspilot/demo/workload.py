"""A deliberately fragile Lambda used to generate real failures for OpsPilot to triage.

Invoke it with {"mode": "ok" | "oom" | "slow" | "error"}. It is configured with 128 MB
and a 3 second timeout in the template, so "oom" and "slow" fail on purpose."""

from __future__ import annotations

import json
import time
from typing import Any


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    mode = (event or {}).get("mode", "ok")
    print(json.dumps({"message": "demo workload invoked", "mode": mode}))
    if mode == "oom":
        hog: list[bytes] = []
        while True:  # b"x" * n writes every byte, so the memory is really used
            hog.append(b"x" * (8 * 1024 * 1024))
    if mode == "slow":
        time.sleep(10)
    if mode == "error":
        raise ValueError("invalid order payload: missing customer_id")
    return {"ok": True, "mode": mode}
