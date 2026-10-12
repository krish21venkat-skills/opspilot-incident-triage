"""Wiring: builds the real AWS-backed dependencies once per Lambda container.

Handlers call `get_runtime()`. Tests call `set_runtime()` with in-memory fakes, which is
why no handler constructs an AWS client itself.
"""

from __future__ import annotations

from functools import cached_property
from typing import Any

from .actions import ActionExecutor
from .config import Settings
from .evidence import CloudWatchEvidence, EvidenceSource
from .graph import Deps, build_graph
from .llm import LLM, build_llm
from .messaging import JobQueue, Notifier, SnsNotifier, SqsQueue
from .retrieval import RunbookIndex
from .store import DynamoStore, IncidentStore
from .token_store import get_api_token  # noqa: F401  (kept importable from here)


class Runtime:
    def __init__(
        self,
        settings: Settings,
        store: IncidentStore,
        queue: JobQueue,
        notifier: Notifier,
        evidence: EvidenceSource,
        executor: ActionExecutor,
        index: RunbookIndex | None = None,
        llm: LLM | None = None,
    ):
        self.settings = settings
        self.store = store
        self.queue = queue
        self.notifier = notifier
        self.evidence = evidence
        self.executor = executor
        if index is not None:
            self.index = index
        if llm is not None:
            self.llm = llm

    # Built lazily so the API and authorizer Lambdas never load the LLM libraries.
    @cached_property
    def index(self) -> RunbookIndex:
        return RunbookIndex.from_directory(self.settings.runbook_dir)

    @cached_property
    def llm(self) -> LLM:
        return build_llm(self.settings)

    @cached_property
    def graph(self) -> Any:
        return build_graph(
            Deps(self.settings, self.store, self.evidence, self.index, self.llm, self.executor, self.notifier)
        )


_runtime: Runtime | None = None


def build_runtime() -> Runtime:
    settings = Settings.from_env()
    return Runtime(
        settings=settings,
        store=DynamoStore(settings.table_name, settings.aws_region),
        queue=SqsQueue(settings.queue_url, settings.aws_region),
        notifier=SnsNotifier(settings.topic_arn, settings.aws_region),
        evidence=CloudWatchEvidence(settings.managed_prefix, settings.aws_region),
        executor=ActionExecutor(settings),
    )


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = build_runtime()
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    _runtime = runtime
