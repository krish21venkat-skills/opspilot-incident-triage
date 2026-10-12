"""Run the whole workflow on a laptop, with no AWS account.

    python -m opspilot.cli triage samples/incidents/lambda_oom.json --approve
    python -m opspilot.cli triage samples/incidents/lambda_exception.json --provider ollama

Everything AWS-shaped is replaced by an in-memory twin: DynamoDB by MemoryStore, CloudWatch
Logs by sample files, SNS by the console, and Lambda by a tiny stub.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from .actions import ActionExecutor
from .config import Settings
from .evidence import SampleEvidence
from .graph import Deps, build_graph, run_analyze, run_execute
from .llm import build_llm
from .messaging import ConsoleNotifier
from .models import Incident, Severity, Status, make_event, utc_now
from .observability import get_logger
from .retrieval import RunbookIndex
from .store import MemoryStore

REPO_ROOT = Path(__file__).resolve().parents[2]


class StubLambda:
    """Stands in for the Lambda API so the memory change can be seen without an account."""

    def __init__(self, memory: int = 128):
        self.memory = memory

    def get_function_configuration(self, FunctionName: str) -> dict:
        return {"MemorySize": self.memory}

    def update_function_configuration(self, FunctionName: str, MemorySize: int) -> None:
        self.memory = MemorySize


def load_incident(path: Path) -> Incident:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Incident.new(
        source="cli",
        title=data["title"],
        description=data.get("description", ""),
        service=data.get("service", "unknown"),
        severity=Severity(data.get("severity", "P3")),
        resource=data.get("resource", {}),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opspilot", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    triage = sub.add_parser("triage", help="triage a sample incident end to end")
    triage.add_argument("incident_file", type=Path)
    triage.add_argument("--provider", choices=["mock", "ollama", "bedrock"], default="mock")
    triage.add_argument("--logs-dir", type=Path, default=REPO_ROOT / "samples" / "logs")
    group = triage.add_mutually_exclusive_group()
    group.add_argument("--approve", action="store_true", help="approve automatically if approval is needed")
    group.add_argument("--reject", action="store_true", help="reject automatically if approval is needed")
    args = parser.parse_args(argv)

    os.environ.setdefault("EMF_DISABLED", "1")  # no CloudWatch locally, so no metric lines
    get_logger().setLevel(os.environ.get("LOG_LEVEL", "WARNING").upper())

    settings = replace(Settings.from_env(), llm_provider=args.provider, allow_mutating_actions=True)
    store = MemoryStore()
    executor = ActionExecutor(settings, lambda_client=StubLambda())
    app = build_graph(
        Deps(
            settings=settings,
            store=store,
            evidence=SampleEvidence(args.logs_dir),
            index=RunbookIndex.from_directory(settings.runbook_dir),
            llm=build_llm(settings),
            executor=executor,
            notifier=ConsoleNotifier(),
        )
    )

    incident, _ = store.create_incident(load_incident(args.incident_file), settings.dedup_ttl_seconds)
    print(f"Incident {incident.incident_id}: {incident.title}\n")
    state = run_analyze(app, incident.incident_id)
    for line in state["trace"]:
        print(f"  - {line}")

    current = store.get(incident.incident_id)
    if current.status is Status.AWAITING_APPROVAL:
        answer = "y" if args.approve else "n" if args.reject else None
        if answer is None and sys.stdin.isatty():
            answer = input("\nApprove this action? [y/N] ").strip().lower()
        if answer == "y":
            store.transition(
                incident.incident_id,
                {Status.AWAITING_APPROVAL},
                Status.APPROVED,
                fields={"approval": {"decision": "approve", "by": "cli", "at": utc_now()}},
                event=make_event("approve", "cli"),
            )
            for line in run_execute(app, incident.incident_id)["trace"]:
                print(f"  - {line}")
        elif answer == "n":
            store.transition(
                incident.incident_id, {Status.AWAITING_APPROVAL}, Status.REJECTED, event=make_event("reject", "cli")
            )
        else:
            print("\nLeft awaiting approval (pass --approve or --reject to continue).")

    final = store.get(incident.incident_id)
    print(f"\nFinal status: {final.status.value}")
    if final.result:
        print(f"Result: {final.result}")
    print("\nTimeline:")
    for event in final.events:
        print(f"  {event['at']}  {event['event']:<20} {event['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
