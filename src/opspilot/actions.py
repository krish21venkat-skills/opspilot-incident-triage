"""The action layer: an allow-list, parameter clamping and a dry-run default.

The language model can only *suggest* one of three action types. This module decides
whether the suggestion is allowed, rewrites its parameters from trusted data, and runs
it. Nothing the model or a log line says can widen what happens here (ADR 0003).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .models import ActionType, Incident, ProposedAction, Severity

MIN_MEMORY_MB = 128
MAX_MEMORY_MB = 1024


@dataclass(frozen=True)
class ActionSpec:
    type: ActionType
    mutating: bool
    description: str


SPECS: dict[ActionType, ActionSpec] = {
    ActionType.NOTIFY_ONLY: ActionSpec(ActionType.NOTIFY_ONLY, False, "Send a notification, change nothing"),
    ActionType.CREATE_TICKET: ActionSpec(ActionType.CREATE_TICKET, False, "Record a ticket for a human"),
    ActionType.SET_LAMBDA_MEMORY: ActionSpec(
        ActionType.SET_LAMBDA_MEMORY, True, "Raise memory on a managed Lambda function"
    ),
}


class ActionExecutor:
    def __init__(self, settings: Settings, lambda_client: Any = None, s3_client: Any = None):
        self._settings = settings
        self._lambda = lambda_client
        self._s3 = s3_client

    # ---- policy -------------------------------------------------------------------

    def is_mutating(self, action: ProposedAction) -> bool:
        return SPECS[action.type].mutating

    def requires_approval(self, action: ProposedAction, severity: Severity) -> bool:
        """Anything that changes infrastructure needs a human. So does any action on a P1."""
        if action.type is ActionType.NOTIFY_ONLY:
            return False
        return self.is_mutating(action) or severity is Severity.P1

    def sanitize(self, action: ProposedAction, incident: Incident, confidence: float) -> ProposedAction:
        """Rebuild the action from trusted facts. The target comes from the incident record,
        never from model output, so an instruction hidden in a log line cannot redirect it."""
        if action.type is not ActionType.SET_LAMBDA_MEMORY:
            return action
        target = incident.resource.get("function_name", "")
        if not target.startswith(self._settings.managed_prefix):
            return ProposedAction(
                type=ActionType.CREATE_TICKET,
                params={},
                rationale=f"Target '{target or 'unknown'}' is not a managed workload; ticket instead.",
            )
        if confidence < self._settings.min_action_confidence:
            return ProposedAction(
                type=ActionType.CREATE_TICKET,
                params={},
                rationale=f"Confidence {confidence:.2f} is below the {self._settings.min_action_confidence} "
                "needed for an infrastructure change; ticket instead.",
            )
        try:
            requested = int(action.params.get("memory_mb", 256))
        except (TypeError, ValueError):
            requested = 256
        requested = max(MIN_MEMORY_MB, min(requested, MAX_MEMORY_MB))
        return ProposedAction(
            type=ActionType.SET_LAMBDA_MEMORY,
            params={"function_name": target, "memory_mb": requested},
            rationale=action.rationale,
        )

    # ---- execution ----------------------------------------------------------------

    def execute(self, action: ProposedAction, incident: Incident) -> str:
        if action.type is ActionType.NOTIFY_ONLY:
            return "No automated change. The on-call engineer was notified."
        if action.type is ActionType.CREATE_TICKET:
            return self._create_ticket(incident, action)
        if action.type is ActionType.SET_LAMBDA_MEMORY:
            return self._set_lambda_memory(action)
        raise ValueError(f"unsupported action {action.type}")

    def _create_ticket(self, incident: Incident, action: ProposedAction) -> str:
        ticket = {
            "incident_id": incident.incident_id,
            "title": incident.title,
            "service": incident.service,
            "severity": incident.severity.value,
            "diagnosis": incident.diagnosis,
            "note": action.rationale,
        }
        if not self._settings.ticket_bucket:
            return f"Ticket drafted locally for {incident.incident_id}."
        s3 = self._s3 or self._boto("s3")
        key = f"tickets/{incident.incident_id}.json"
        s3.put_object(
            Bucket=self._settings.ticket_bucket,
            Key=key,
            Body=json.dumps(ticket, indent=2).encode(),
            ContentType="application/json",
        )
        return f"Ticket written to s3://{self._settings.ticket_bucket}/{key}"

    def _set_lambda_memory(self, action: ProposedAction) -> str:
        function = str(action.params["function_name"])
        requested = int(action.params["memory_mb"])
        client = self._lambda or self._boto("lambda")
        current = int(client.get_function_configuration(FunctionName=function)["MemorySize"])
        # One step at a time: never more than double, never past the hard cap.
        target = min(requested, current * 2, MAX_MEMORY_MB)
        if target <= current:
            return f"No change: {function} already has {current} MB (requested {requested} MB)."
        if not self._settings.allow_mutating_actions:
            return f"DRY-RUN: would raise {function} from {current} MB to {target} MB."
        client.update_function_configuration(FunctionName=function, MemorySize=target)
        return f"Raised {function} from {current} MB to {target} MB."

    def _boto(self, service: str) -> Any:
        import boto3

        return boto3.client(service, region_name=self._settings.aws_region)
