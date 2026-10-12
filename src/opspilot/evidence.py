"""Where the incident logs come from: CloudWatch Logs in AWS, sample files locally."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# CloudWatch filter syntax: `?term` means "any of these terms".
FILTER_PATTERN = '?ERROR ?Error ?Exception ?Traceback ?"Task timed out" ?"Runtime exited" ?REPORT'


def condense(lines: list[str], max_lines: int, max_chars: int, line_chars: int = 300) -> list[str]:
    """Drop blanks and consecutive repeats, shorten long lines, cap total size."""
    out: list[str] = []
    previous = None
    used = 0
    for raw in lines:
        line = raw.strip()
        if not line or line == previous:
            continue
        previous = line
        line = line[:line_chars]
        if used + len(line) > max_chars or len(out) >= max_lines:
            break
        out.append(line)
        used += len(line) + 1
    return out


class EvidenceSource(ABC):
    @abstractmethod
    def fetch_logs(self, log_group: str, window_minutes: int, limit: int) -> list[str]: ...


class CloudWatchEvidence(EvidenceSource):
    """Read-only. Refuses log groups outside the managed prefix, matching the IAM policy."""

    def __init__(self, managed_prefix: str, region: str | None = None, client: Any = None):
        if client is None:
            import boto3

            client = boto3.client("logs", region_name=region)
        self._client = client
        self._allowed = f"/aws/lambda/{managed_prefix}"

    def fetch_logs(self, log_group: str, window_minutes: int, limit: int) -> list[str]:
        if not log_group.startswith(self._allowed):
            return []
        now_ms = int(time.time() * 1000)
        response = self._client.filter_log_events(
            logGroupName=log_group,
            startTime=now_ms - window_minutes * 60 * 1000,
            endTime=now_ms,
            filterPattern=FILTER_PATTERN,
            limit=min(max(limit, 1) * 3, 200),
        )
        messages = [e["message"] for e in response.get("events", [])]
        return messages[-limit * 3 :]


class SampleEvidence(EvidenceSource):
    """Offline mode: reads samples/logs/<function name>.log."""

    def __init__(self, directory: str | Path):
        self._dir = Path(directory)

    def fetch_logs(self, log_group: str, window_minutes: int, limit: int) -> list[str]:
        path = self._dir / f"{log_group.rsplit('/', 1)[-1]}.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []
