"""Deterministic diagnosis from keywords.

Two uses: the offline MockLLM answers with it, and the workflow falls back to it when the
real model is unavailable or returns something unparseable. An incident is therefore
never stuck waiting on a model provider.
"""

from __future__ import annotations

from typing import Any


def _result(summary, cause, confidence, severity, runbook, action_type, params=None, why="") -> dict[str, Any]:
    return {
        "summary": summary,
        "probable_cause": cause,
        "confidence": confidence,
        "severity": severity,
        "runbook_id": runbook,
        "action": {"type": action_type, "params": params or {}, "rationale": why},
    }


def diagnose_from_text(text: str, runbook_ids: list[str]) -> dict[str, Any]:
    lowered = text.lower()

    def rb(identifier: str) -> str | None:
        return identifier if identifier in runbook_ids else None

    if "outofmemory" in lowered or "signal: killed" in lowered:
        return _result(
            "Lambda function ran out of memory",
            "Peak memory reached the configured limit and the runtime was killed.",
            0.82,
            "P2",
            rb("RB-LAMBDA-OOM"),
            "SET_LAMBDA_MEMORY",
            {"memory_mb": 256},
            "Raise memory one step, capped at double the current size, then watch usage.",
        )
    if "task timed out" in lowered:
        return _result(
            "Lambda function is timing out",
            "Invocations run until the configured timeout, which points at a slow dependency.",
            0.7,
            "P2",
            rb("RB-LAMBDA-TIMEOUT"),
            "CREATE_TICKET",
            {},
            "Raising the timeout would hide the cause; a human should check the dependency.",
        )
    if "traceback" in lowered or "exception" in lowered or "[error]" in lowered:
        return _result(
            "Lambda function raises unhandled exceptions",
            "The logs show a stack trace, which suggests a code or input problem.",
            0.65,
            "P3",
            rb("RB-LAMBDA-ERRORS"),
            "CREATE_TICKET",
            {},
            "Record the exception and affected input for the owning team.",
        )
    return _result(
        "Alarm fired but the evidence is inconclusive",
        "No known error pattern was found in the available logs.",
        0.3,
        "P3",
        rb("RB-GENERAL"),
        "NOTIFY_ONLY",
        {},
        "Notify on-call; do not change infrastructure without evidence.",
    )
