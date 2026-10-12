"""Remove secrets and personal data from text before it reaches a language model.

Logs are the riskiest input in an incident workflow: they routinely contain tokens,
emails and addresses. Redaction runs before retrieval and before the prompt is built,
and the same function is applied to anything that is stored or sent in an email.
"""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]{12,}"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\beyJ[a-zA-Z0-9_\-]{8,}\.[a-zA-Z0-9_\-]{8,}\.[a-zA-Z0-9_\-]{8,}\b"), "[REDACTED_JWT]"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)(\s*[=:]\s*)(\S+)"),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
]


def redact(text: str) -> tuple[str, int]:
    """Return the cleaned text and how many substitutions were made."""
    total = 0
    for pattern, replacement in _RULES:
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


def redact_lines(lines: list[str]) -> tuple[list[str], int]:
    cleaned: list[str] = []
    total = 0
    for line in lines:
        new_line, count = redact(line)
        cleaned.append(new_line)
        total += count
    return cleaned, total
