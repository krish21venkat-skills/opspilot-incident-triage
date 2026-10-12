"""HTTP API Lambda (payload format 2.0). One function serves every route; the
authorizer Lambda has already checked the bearer token before this code runs."""

from __future__ import annotations

import base64
import json
from typing import Any

from ..bootstrap import Runtime, get_runtime
from ..models import Incident, Severity, Status, make_event, utc_now
from ..observability import emit_metric, get_logger

log = get_logger("opspilot.api")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _resp(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "body must be a JSON object")
    return body


def _summary(incident: Incident) -> dict[str, Any]:
    return {
        "incident_id": incident.incident_id,
        "title": incident.title,
        "status": incident.status.value,
        "severity": incident.severity.value,
        "occurrences": incident.occurrences,
        "created_at": incident.created_at,
    }


def create_incident(event: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    body = _json_body(event)
    title = str(body.get("title", "")).strip()
    if not title:
        raise ApiError(400, "title is required")
    if len(title) > 200:
        raise ApiError(400, "title must be at most 200 characters")
    try:
        severity = Severity(body.get("severity", "P3"))
    except ValueError as exc:
        raise ApiError(400, "severity must be P1, P2 or P3") from exc
    resource_in = body.get("resource") or {}
    if not isinstance(resource_in, dict):
        raise ApiError(400, "resource must be an object")
    resource = {k: str(v)[:200] for k, v in resource_in.items() if k in {"function_name", "log_group", "alarm_name"}}
    if resource.get("function_name") and not resource.get("log_group"):
        resource["log_group"] = f"/aws/lambda/{resource['function_name']}"

    incident = Incident.new(
        source="api",
        title=title,
        description=str(body.get("description", "")),
        service=str(body.get("service", "unknown")),
        severity=severity,
        resource=resource,
    )
    stored, created = rt.store.create_incident(incident, rt.settings.dedup_ttl_seconds)
    if created:
        emit_metric("IncidentCreated")
    else:
        emit_metric("IncidentDeduplicated")
    if created or stored.status is Status.RECEIVED:
        # Re-sending for a RECEIVED duplicate repairs a lost enqueue; the workflow guards make it safe.
        rt.queue.send({"action": "analyze", "incident_id": stored.incident_id})
    return _resp(201 if created else 200, {"duplicate": not created, "incident": stored.model_dump(mode="json")})


def list_incidents(event: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    query = event.get("queryStringParameters") or {}
    status = None
    if query.get("status"):
        try:
            status = Status(query["status"].upper())
        except ValueError as exc:
            raise ApiError(400, f"unknown status '{query['status']}'") from exc
    try:
        limit = max(1, min(int(query.get("limit", 20)), 50))
    except ValueError as exc:
        raise ApiError(400, "limit must be an integer") from exc
    return _resp(200, {"incidents": [_summary(i) for i in rt.store.list_recent(status, limit)]})


def get_incident(incident_id: str, rt: Runtime) -> dict[str, Any]:
    incident = rt.store.get(incident_id)
    if incident is None:
        raise ApiError(404, "incident not found")
    return _resp(200, incident.model_dump(mode="json"))


def decide(event: dict[str, Any], rt: Runtime, incident_id: str, approve: bool) -> dict[str, Any]:
    body = _json_body(event)
    approval = {
        "decision": "approve" if approve else "reject",
        "by": (str(body.get("by", "")).strip() or "unknown")[:80],
        "comment": str(body.get("comment", ""))[:500],
        "at": utc_now(),
    }
    new_status = Status.APPROVED if approve else Status.REJECTED
    moved = rt.store.transition(
        incident_id,
        {Status.AWAITING_APPROVAL},
        new_status,
        fields={"approval": approval},
        event=make_event(approval["decision"], approval["by"]),
    )
    if not moved:
        current = rt.store.get(incident_id)
        if current is None:
            raise ApiError(404, "incident not found")
        raise ApiError(409, f"incident is {current.status.value}, not AWAITING_APPROVAL")
    if approve:
        try:
            rt.queue.send({"action": "execute", "incident_id": incident_id})
        except Exception:
            # Put the incident back so the approval can be retried instead of getting stuck.
            rt.store.transition(
                incident_id,
                {Status.APPROVED},
                Status.AWAITING_APPROVAL,
                event=make_event("approval_reverted", "queue unavailable"),
            )
            log.exception("could not enqueue execution", extra={"incident_id": incident_id})
            raise ApiError(503, "could not queue the action; please retry") from None
        emit_metric("ApprovalGranted")
    else:
        emit_metric("ApprovalRejected")
    return _resp(202, {"incident_id": incident_id, "status": new_status.value})


def route(event: dict[str, Any], rt: Runtime) -> dict[str, Any]:
    key = event.get("routeKey", "")
    incident_id = (event.get("pathParameters") or {}).get("id", "")
    try:
        if key == "POST /incidents":
            return create_incident(event, rt)
        if key == "GET /incidents":
            return list_incidents(event, rt)
        if key == "GET /incidents/{id}":
            return get_incident(incident_id, rt)
        if key == "POST /incidents/{id}/approve":
            return decide(event, rt, incident_id, approve=True)
        if key == "POST /incidents/{id}/reject":
            return decide(event, rt, incident_id, approve=False)
        raise ApiError(404, "route not found")
    except ApiError as err:
        return _resp(err.status, {"error": err.message})
    except Exception:
        log.exception("unhandled error", extra={"route": key})
        return _resp(500, {"error": "internal error"})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return route(event, get_runtime())
