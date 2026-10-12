"""Lambda authorizer for the HTTP API (payload 2.0, simple responses).

Compares the bearer token with a SecureString in SSM Parameter Store using a
constant-time comparison. Any failure to read the secret denies the request."""

from __future__ import annotations

import hmac
from typing import Any

from ..config import Settings
from ..observability import get_logger
from ..token_store import get_api_token

log = get_logger("opspilot.authorizer")


def check_token(header: str, expected: str) -> bool:
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value or not expected:
        return False
    return hmac.compare_digest(value.encode(), expected.encode())


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    header = (event.get("headers") or {}).get("authorization", "")
    try:
        allowed = check_token(header, get_api_token(Settings.from_env()))
    except Exception:
        log.exception("could not load the API token; denying request")
        allowed = False
    return {"isAuthorized": allowed, "context": {"principal": "api-client"}}
