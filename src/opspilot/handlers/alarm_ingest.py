"""Turns a CloudWatch "Alarm State Change" EventBridge event into an incident and a queue job."""

from __future__ import annotations

from typing import Any

from ..bootstrap import get_runtime
from ..models import Incident, Status
from ..observability import emit_metric, get_logger

log = get_logger("opspilot.alarm_ingest")


def _function_name(detail: dict[str, Any]) -> str:
    for metric in detail.get("configuration", {}).get("metrics", []):
        dimensions = metric.get("metricStat", {}).get("metric", {}).get("dimensions", {})
        if dimensions.get("FunctionName"):
            return str(dimensions["FunctionName"])
    return ""


def incident_from_alarm_event(event: dict[str, Any]) -> Incident:
    detail = event.get("detail", {})
    alarm = str(detail.get("alarmName", "unknown-alarm"))
    reason = str(detail.get("state", {}).get("reason", ""))
    description = str(detail.get("configuration", {}).get("description", ""))
    function_name = _function_name(detail)
    resource = {"alarm_name": alarm}
    if function_name:
        resource["function_name"] = function_name
        resource["log_group"] = f"/aws/lambda/{function_name}"
    return Incident.new(
        source="cloudwatch-alarm",
        title=f"Alarm {alarm} entered ALARM state",
        description=f"{reason} {description}".strip(),
        service=function_name or alarm,
        resource=resource,
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    rt = get_runtime()
    stored, created = rt.store.create_incident(incident_from_alarm_event(event), rt.settings.dedup_ttl_seconds)
    emit_metric("IncidentCreated" if created else "IncidentDeduplicated")
    if created or stored.status is Status.RECEIVED:
        rt.queue.send({"action": "analyze", "incident_id": stored.incident_id})
    log.info("alarm ingested", extra={"incident_id": stored.incident_id, "created": created})
    return {"incident_id": stored.incident_id, "created": created}
