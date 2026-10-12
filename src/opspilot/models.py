"""Domain models. Pydantic validates everything that crosses a trust boundary:
API input, DynamoDB items and, most importantly, language model output."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Severity(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Status(StrEnum):
    RECEIVED = "RECEIVED"
    TRIAGING = "TRIAGING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ActionType(StrEnum):
    NOTIFY_ONLY = "NOTIFY_ONLY"
    CREATE_TICKET = "CREATE_TICKET"
    SET_LAMBDA_MEMORY = "SET_LAMBDA_MEMORY"


class ProposedAction(BaseModel):
    type: ActionType = ActionType.NOTIFY_ONLY
    params: dict[str, str | int] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=600)


class Diagnosis(BaseModel):
    """The only shape the language model is allowed to return."""

    summary: str = Field(max_length=400)
    probable_cause: str = Field(max_length=600)
    confidence: float = Field(ge=0.0, le=1.0)
    severity: Severity
    runbook_id: str | None = None
    action: ProposedAction


def new_incident_id() -> str:
    return f"INC-{datetime.now(UTC):%Y%m%d}-{uuid.uuid4().hex[:6]}"


def fingerprint(source: str, key: str) -> str:
    return hashlib.sha256(f"{source}|{key}".encode()).hexdigest()[:16]


class Incident(BaseModel):
    incident_id: str
    source: str
    title: str
    description: str = ""
    service: str = "unknown"
    severity: Severity = Severity.P3
    fingerprint: str
    status: Status = Status.RECEIVED
    resource: dict[str, str] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    occurrences: int = 1
    diagnosis: dict[str, Any] | None = None
    proposed_action: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    result: str | None = None
    events: list[dict[str, str]] = Field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        source: str,
        title: str,
        description: str = "",
        service: str = "unknown",
        severity: Severity = Severity.P3,
        resource: dict[str, str] | None = None,
    ) -> Incident:
        now = utc_now()
        resource = resource or {}
        key = f"{title}|{resource.get('function_name', '')}"
        return cls(
            incident_id=new_incident_id(),
            source=source,
            title=title[:200],
            description=description[:4000],
            service=service[:80],
            severity=severity,
            resource=resource,
            fingerprint=fingerprint(source, key),
            created_at=now,
            updated_at=now,
            events=[{"at": now, "event": "created", "detail": source}],
        )


def make_event(name: str, detail: str = "") -> dict[str, str]:
    return {"at": utc_now(), "event": name, "detail": detail[:300]}
