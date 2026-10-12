"""Runtime configuration, read once from environment variables.

Everything that changes between environments (table names, model choice, safety
switches) lives here so the rest of the code never touches os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    environment: str = "dev"
    aws_region: str = "ap-southeast-2"

    # LLM selection: "mock" (offline, deterministic), "ollama" (local), "bedrock" (AWS).
    llm_provider: str = "mock"
    model_id: str = "apac.amazon.nova-lite-v1:0"
    ollama_model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"
    max_output_tokens: int = 600
    max_prompt_chars: int = 8000

    # AWS resources (empty when running locally).
    table_name: str = ""
    queue_url: str = ""
    topic_arn: str = ""
    ticket_bucket: str = ""
    token_param: str = "/opspilot/dev/api-token"

    # Safety rails.
    managed_prefix: str = "opspilot-demo-"
    allow_mutating_actions: bool = False
    min_action_confidence: float = 0.6

    # Evidence and de-duplication.
    max_log_events: int = 40
    max_log_chars: int = 6000
    log_window_minutes: int = 30
    dedup_ttl_seconds: int = 1800

    runbook_dir: str = str(Path(__file__).parent / "runbooks")

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            environment=env.get("ENVIRONMENT", "dev"),
            aws_region=env.get("AWS_REGION", env.get("AWS_DEFAULT_REGION", "ap-southeast-2")),
            llm_provider=env.get("LLM_PROVIDER", "mock").lower(),
            model_id=env.get("MODEL_ID", cls.model_id),
            ollama_model=env.get("OLLAMA_MODEL", cls.ollama_model),
            ollama_base_url=env.get("OLLAMA_BASE_URL", cls.ollama_base_url),
            max_output_tokens=_int("MAX_OUTPUT_TOKENS", cls.max_output_tokens),
            max_prompt_chars=_int("MAX_PROMPT_CHARS", cls.max_prompt_chars),
            table_name=env.get("TABLE_NAME", ""),
            queue_url=env.get("QUEUE_URL", ""),
            topic_arn=env.get("TOPIC_ARN", ""),
            ticket_bucket=env.get("TICKET_BUCKET", ""),
            token_param=env.get("TOKEN_PARAM", cls.token_param),
            managed_prefix=env.get("MANAGED_PREFIX", cls.managed_prefix),
            allow_mutating_actions=_flag("ALLOW_MUTATING_ACTIONS", False),
            max_log_events=_int("MAX_LOG_EVENTS", cls.max_log_events),
            max_log_chars=_int("MAX_LOG_CHARS", cls.max_log_chars),
            log_window_minutes=_int("LOG_WINDOW_MINUTES", cls.log_window_minutes),
            dedup_ttl_seconds=_int("DEDUP_TTL_SECONDS", cls.dedup_ttl_seconds),
        )
