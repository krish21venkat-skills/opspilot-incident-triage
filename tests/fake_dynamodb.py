"""A small in-memory DynamoDB table that enforces the rules our store depends on.

It is not a full emulator. It understands exactly the condition, update and query forms that
DynamoStore uses, and it fails loudly on anything else, so a typo in an expression shows up
in a unit test instead of in AWS. It also rejects Python floats, like boto3 does.
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal
from typing import Any


class FakeClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise TypeError("Float types are not supported. Use Decimal types instead.")
    if isinstance(value, dict):
        for v in value.values():
            _reject_floats(v)
    if isinstance(value, list):
        for v in value:
            _reject_floats(v)


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    # ---- helpers ------------------------------------------------------------------

    @staticmethod
    def _name(token: str, names: dict[str, str]) -> str:
        return names[token] if token.startswith("#") else token

    def _condition_ok(self, expr: str | None, names: dict, values: dict, item: dict | None) -> bool:
        if not expr:
            return True

        def atom(a: str) -> bool:
            a = a.strip()
            if m := re.fullmatch(r"attribute_not_exists\((\w+)\)", a):
                return item is None or m.group(1) not in item
            if m := re.fullmatch(r"attribute_exists\((\w+)\)", a):
                return item is not None and m.group(1) in item
            if m := re.fullmatch(r"(#\w+) < (:\w+)", a):
                return item is not None and item.get(self._name(m.group(1), names), 0) < values[m.group(2)]
            if m := re.fullmatch(r"(#\w+) IN \(([^)]*)\)", a):
                allowed = {values[v.strip()] for v in m.group(2).split(",")}
                return item is not None and item.get(self._name(m.group(1), names)) in allowed
            raise NotImplementedError(f"fake table cannot evaluate condition: {a!r}")

        return any(all(atom(a) for a in clause.split(" AND ")) for clause in expr.split(" OR "))

    def _operand(self, token: str, item: dict, names: dict, values: dict) -> Any:
        token = token.strip()
        if token.startswith(":"):
            return values[token]
        if m := re.fullmatch(r"if_not_exists\((#?\w+), (:\w+)\)", token):
            return item.get(self._name(m.group(1), names), values[m.group(2)])
        if m := re.fullmatch(r"list_append\((.+)\)", token):
            left, right = _split_top_level(m.group(1))
            return list(self._operand(left, item, names, values)) + list(self._operand(right, item, names, values))
        if token.startswith("#"):
            return item.get(names[token])
        raise NotImplementedError(f"fake table cannot evaluate operand: {token!r}")

    # ---- table API ----------------------------------------------------------------

    def put_item(self, Item, ConditionExpression=None, ExpressionAttributeNames=None, ExpressionAttributeValues=None):
        _reject_floats(Item)
        existing = self.items.get(Item["pk"])
        if not self._condition_ok(
            ConditionExpression, ExpressionAttributeNames or {}, ExpressionAttributeValues or {}, existing
        ):
            raise FakeClientError("ConditionalCheckFailedException")
        self.items[Item["pk"]] = copy.deepcopy(Item)

    def get_item(self, Key):
        item = self.items.get(Key["pk"])
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def delete_item(self, Key):
        self.items.pop(Key["pk"], None)

    def update_item(
        self,
        Key,
        UpdateExpression,
        ConditionExpression=None,
        ExpressionAttributeNames=None,
        ExpressionAttributeValues=None,
    ):
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        _reject_floats(values)
        existing = self.items.get(Key["pk"])
        if not self._condition_ok(ConditionExpression, names, values, existing):
            raise FakeClientError("ConditionalCheckFailedException")
        item = existing if existing is not None else dict(Key)
        for keyword, body in re.findall(r"(SET|ADD)\s+(.*?)(?=\s+(?:SET|ADD)\s|$)", UpdateExpression):
            for assignment in _split_top_level(body):
                if keyword == "SET":
                    target, expr = assignment.split(" = ", 1)
                    item[self._name(target.strip(), names)] = self._operand(expr, item, names, values)
                else:
                    target, token = assignment.split(" ", 1)
                    attr = self._name(target, names)
                    item[attr] = item.get(attr, 0) + values[token.strip()]
        self.items[Key["pk"]] = item

    def query(
        self,
        IndexName,
        KeyConditionExpression,
        ExpressionAttributeNames,
        ExpressionAttributeValues,
        ScanIndexForward=True,
        Limit=None,
        FilterExpression=None,
        ExclusiveStartKey=None,
    ):
        assert IndexName == "recent-index" and KeyConditionExpression == "#e = :e"
        entity = ExpressionAttributeValues[":e"]
        rows = [i for i in self.items.values() if i.get("entity") == entity and "created_at" in i]
        rows.sort(key=lambda i: i["created_at"], reverse=not ScanIndexForward)
        start = ExclusiveStartKey["pos"] if ExclusiveStartKey else 0
        window = rows[start : start + Limit] if Limit else rows[start:]
        page: dict[str, Any] = {}
        if Limit and start + Limit < len(rows):
            page["LastEvaluatedKey"] = {"pos": start + Limit}  # Limit applies before the filter, like DynamoDB
        if FilterExpression:
            assert FilterExpression == "#st = :st"
            wanted = ExpressionAttributeValues[":st"]
            window = [i for i in window if i.get(ExpressionAttributeNames["#st"]) == wanted]
        page["Items"] = copy.deepcopy(window)
        return page


class FakeResource:
    def __init__(self) -> None:
        self.table = FakeTable()

    def Table(self, _name: str) -> FakeTable:  # noqa: N802 - mirrors the boto3 API
        return self.table


__all__ = ["Decimal", "FakeClientError", "FakeResource", "FakeTable"]
