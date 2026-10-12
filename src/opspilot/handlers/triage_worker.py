"""SQS worker. Runs the LangGraph workflow for one job at a time.

Uses partial batch responses: only failed messages return to the queue, and after three
receives they land in the dead-letter queue, which has its own alarm."""

from __future__ import annotations

import json
from typing import Any

from ..bootstrap import get_runtime
from ..graph import run_analyze, run_execute
from ..models import Status, make_event
from ..observability import emit_metric, get_logger

log = get_logger("opspilot.worker")


def process(message: dict[str, Any], rt) -> dict[str, Any]:
    action = message.get("action")
    incident_id = message.get("incident_id")
    if action not in {"analyze", "execute"} or not incident_id:
        raise ValueError(f"malformed job: {message}")
    app = rt.graph
    try:
        return run_analyze(app, incident_id) if action == "analyze" else run_execute(app, incident_id)
    except Exception:
        if action == "analyze":
            # Hand the incident back so the redelivered message is not skipped as "already started".
            rt.store.transition(
                incident_id,
                {Status.TRIAGING},
                Status.RECEIVED,
                event=make_event("released", "worker error, will retry"),
            )
        raise


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    rt = get_runtime()
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        try:
            result = process(json.loads(record["body"]), rt)
            log.info("job done", extra={"message_id": record["messageId"], "trace": result.get("trace", [])})
        except Exception:
            log.exception("job failed", extra={"message_id": record.get("messageId")})
            emit_metric("WorkerFailure")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
