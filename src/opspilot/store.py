"""Incident persistence behind one interface, with a DynamoDB and an in-memory backend.

Two ideas matter here:
  * `transition` is a compare-and-set on the status field. Queues deliver at least
    once, so every state change must be safe to attempt twice.
  * `create_incident` de-duplicates repeat alerts with a short-lived marker item, so
    an alarm that flaps produces one incident with a counter, not fifty incidents.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Any

from .models import Incident, Status, utc_now


class IncidentStore(ABC):
    @abstractmethod
    def create_incident(self, incident: Incident, dedup_ttl: int) -> tuple[Incident, bool]:
        """Store a new incident. Returns (incident, created). When a recent incident has
        the same fingerprint, returns that incident with created=False."""

    @abstractmethod
    def get(self, incident_id: str) -> Incident | None: ...

    @abstractmethod
    def list_recent(self, status: Status | None = None, limit: int = 20) -> list[Incident]: ...

    @abstractmethod
    def transition(
        self,
        incident_id: str,
        expected: Iterable[Status],
        new: Status,
        *,
        fields: dict[str, Any] | None = None,
        event: dict[str, str] | None = None,
    ) -> bool:
        """Move to `new` only if the current status is in `expected`. Returns False
        when the condition fails, which callers treat as "someone else got there first"."""


class MemoryStore(IncidentStore):
    """Used by tests and the offline CLI. Mirrors the DynamoDB semantics."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._items: dict[str, dict[str, Any]] = {}
        self._dedup: dict[str, tuple[str, float]] = {}

    def create_incident(self, incident: Incident, dedup_ttl: int) -> tuple[Incident, bool]:
        now = self._clock()
        marker = self._dedup.get(incident.fingerprint)
        if marker and marker[1] > now and marker[0] in self._items:
            existing = self._items[marker[0]]
            existing["occurrences"] += 1
            existing["updated_at"] = utc_now()
            return Incident.model_validate(existing), False
        self._dedup[incident.fingerprint] = (incident.incident_id, now + dedup_ttl)
        self._items[incident.incident_id] = incident.model_dump(mode="json")
        return incident, True

    def get(self, incident_id: str) -> Incident | None:
        item = self._items.get(incident_id)
        return Incident.model_validate(item) if item else None

    def list_recent(self, status: Status | None = None, limit: int = 20) -> list[Incident]:
        items = sorted(self._items.values(), key=lambda i: (i["created_at"], i["incident_id"]), reverse=True)
        if status:
            items = [i for i in items if i["status"] == status.value]
        return [Incident.model_validate(i) for i in items[:limit]]

    def transition(self, incident_id, expected, new, *, fields=None, event=None) -> bool:
        item = self._items.get(incident_id)
        if item is None or item["status"] not in {s.value for s in expected}:
            return False
        item["status"] = new.value
        item["updated_at"] = utc_now()
        for key, value in (fields or {}).items():
            item[key] = value
        if event:
            item["events"].append(event)
        return True


def _to_ddb(value: Any) -> Any:
    """DynamoDB rejects Python floats; convert to Decimal recursively."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value]
    return value


def _from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    return value


class DynamoStore(IncidentStore):
    """Single table. Items: `INC#<id>` (incidents) and `DEDUP#<fingerprint>` (markers with
    a TTL). The `recent-index` GSI (entity, created_at) serves "latest incidents"."""

    def __init__(self, table_name: str, region: str | None = None, resource: Any = None):
        if resource is None:
            import boto3  # imported lazily so unit tests need no AWS SDK

            resource = boto3.resource("dynamodb", region_name=region)
        self._table = resource.Table(table_name)

    @staticmethod
    def _is_conditional_failure(exc: Exception) -> bool:
        response = getattr(exc, "response", None) or {}
        return response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"

    def create_incident(self, incident: Incident, dedup_ttl: int) -> tuple[Incident, bool]:
        now = int(time.time())
        marker_key = {"pk": f"DEDUP#{incident.fingerprint}"}
        try:
            self._table.put_item(
                Item={**marker_key, "incident_id": incident.incident_id, "ttl": now + dedup_ttl},
                # TTL deletion can lag by hours, so expiry is checked explicitly.
                ConditionExpression="attribute_not_exists(pk) OR #t < :now",
                ExpressionAttributeNames={"#t": "ttl"},
                ExpressionAttributeValues={":now": now},
            )
        except Exception as exc:
            if not self._is_conditional_failure(exc):
                raise
            existing_id = self._table.get_item(Key=marker_key)["Item"]["incident_id"]
            self._table.update_item(
                Key={"pk": f"INC#{existing_id}"},
                UpdateExpression="ADD occurrences :one SET updated_at = :now",
                ExpressionAttributeValues={":one": 1, ":now": utc_now()},
            )
            existing = self.get(existing_id)
            if existing is not None:
                return existing, False
            # Marker outlived its incident; fall through and create a fresh one.
        item = {"pk": f"INC#{incident.incident_id}", "entity": "INCIDENT", **incident.model_dump(mode="json")}
        try:
            self._table.put_item(Item=_to_ddb(item), ConditionExpression="attribute_not_exists(pk)")
        except Exception:
            self._table.delete_item(Key=marker_key)
            raise
        return incident, True

    def get(self, incident_id: str) -> Incident | None:
        item = self._table.get_item(Key={"pk": f"INC#{incident_id}"}).get("Item")
        return Incident.model_validate(_from_ddb(item)) if item else None

    def list_recent(self, status: Status | None = None, limit: int = 20) -> list[Incident]:
        found: list[Incident] = []
        kwargs: dict[str, Any] = {
            "IndexName": "recent-index",
            "KeyConditionExpression": "#e = :e",
            "ExpressionAttributeNames": {"#e": "entity"},
            "ExpressionAttributeValues": {":e": "INCIDENT"},
            "ScanIndexForward": False,
            "Limit": max(limit, 20),
        }
        if status:
            kwargs["FilterExpression"] = "#st = :st"
            kwargs["ExpressionAttributeNames"]["#st"] = "status"
            kwargs["ExpressionAttributeValues"][":st"] = status.value
        for _ in range(5):  # bounded paging so one request cannot read the whole table
            page = self._table.query(**kwargs)
            found.extend(Incident.model_validate(_from_ddb(i)) for i in page.get("Items", []))
            if len(found) >= limit or "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return found[:limit]

    def transition(self, incident_id, expected, new, *, fields=None, event=None) -> bool:
        names = {"#st": "status", "#ua": "updated_at"}
        values: dict[str, Any] = {":new": new.value, ":now": utc_now()}
        sets = ["#st = :new", "#ua = :now"]
        for i, (key, value) in enumerate((fields or {}).items()):
            names[f"#f{i}"] = key
            values[f":f{i}"] = _to_ddb(value)
            sets.append(f"#f{i} = :f{i}")
        if event:
            names["#ev"] = "events"
            values[":ev"] = [event]
            values[":empty"] = []
            sets.append("#ev = list_append(if_not_exists(#ev, :empty), :ev)")
        allowed = list(expected)
        for i, status in enumerate(allowed):
            values[f":e{i}"] = status.value
        condition = "attribute_exists(pk) AND #st IN (" + ", ".join(f":e{i}" for i in range(len(allowed))) + ")"
        try:
            self._table.update_item(
                Key={"pk": f"INC#{incident_id}"},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except Exception as exc:
            if self._is_conditional_failure(exc):
                return False
            raise
        return True
