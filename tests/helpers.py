"""Shared builders so every test wires the system the same way, with no AWS."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from opspilot.actions import ActionExecutor
from opspilot.bootstrap import Runtime
from opspilot.cli import StubLambda, load_incident
from opspilot.config import Settings
from opspilot.evidence import SampleEvidence
from opspilot.graph import Deps, build_graph
from opspilot.llm import MockLLM
from opspilot.messaging import MemoryNotifier, MemoryQueue
from opspilot.models import Incident
from opspilot.retrieval import RunbookIndex
from opspilot.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


@dataclass
class Rig:
    settings: Settings
    store: MemoryStore
    notifier: MemoryNotifier
    queue: MemoryQueue
    lambda_stub: StubLambda
    deps: Deps
    app: object


class ScriptedLLM:
    """Returns a fixed reply and remembers what it was asked."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        return self.reply


class BrokenLLM:
    def complete(self, system: str, user: str) -> str:
        raise TimeoutError("provider unavailable")


def diagnosis_json(
    memory_mb: int = 256, confidence: float = 0.9, action: str = "SET_LAMBDA_MEMORY", severity: str = "P2"
) -> str:
    return json.dumps(
        {
            "summary": "Scripted diagnosis",
            "probable_cause": "Scripted cause",
            "confidence": confidence,
            "severity": severity,
            "runbook_id": None,
            "action": {"type": action, "params": {"memory_mb": memory_mb}, "rationale": "scripted"},
        }
    )


def make_rig(llm=None, allow_mutating: bool = True, **overrides) -> Rig:
    settings = replace(Settings(), allow_mutating_actions=allow_mutating, **overrides)
    store, notifier, queue, stub = MemoryStore(), MemoryNotifier(), MemoryQueue(), StubLambda(128)
    deps = Deps(
        settings=settings,
        store=store,
        evidence=SampleEvidence(SAMPLES / "logs"),
        index=RunbookIndex.from_directory(settings.runbook_dir),
        llm=llm or MockLLM(),
        executor=ActionExecutor(settings, lambda_client=stub),
        notifier=notifier,
    )
    return Rig(settings, store, notifier, queue, stub, deps, build_graph(deps))


def make_runtime(rig: Rig) -> Runtime:
    return Runtime(
        settings=rig.settings,
        store=rig.store,
        queue=rig.queue,
        notifier=rig.notifier,
        evidence=rig.deps.evidence,
        executor=rig.deps.executor,
        index=rig.deps.index,
        llm=rig.deps.llm,
    )


def sample_incident(name: str) -> Incident:
    return load_incident(SAMPLES / "incidents" / f"{name}.json")
