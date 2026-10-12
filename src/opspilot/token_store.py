"""Reads the shared API token from SSM Parameter Store.

Kept in its own tiny module on purpose: the authorizer runs on every request, so it must
start fast and must not import the workflow code (LangGraph, LangChain and friends).
"""

from __future__ import annotations

import time
from typing import Any

from .config import Settings

_token_cache: dict[str, Any] = {"value": None, "expires": 0.0}


def get_api_token(settings: Settings, ttl_seconds: int = 300) -> str:
    """Read the SecureString token, cached briefly."""
    now = time.time()
    if _token_cache["value"] and _token_cache["expires"] > now:
        return _token_cache["value"]
    import boto3

    ssm = boto3.client("ssm", region_name=settings.aws_region)
    value = ssm.get_parameter(Name=settings.token_param, WithDecryption=True)["Parameter"]["Value"]
    _token_cache.update(value=value, expires=now + ttl_seconds)
    return value
