"""The triage workflow as a LangGraph state machine.

    analyze:  start_triage -> prepare_context -> gather_evidence -> retrieve_runbooks
              -> diagnose -> policy_gate -+-> request_approval -> END (waits for a human)
                                          +-> begin_execution -> run_action -> notify -> END
    execute:  load_for_execution -> begin_execution -> run_action -> notify -> END

The graph never sleeps while waiting for a person. It stops at `request_approval`, the
incident is saved as AWAITING_APPROVAL, and a later `execute` run resumes the work.
That keeps Lambda invocations short and cheap, and the state lives in DynamoDB.
Every node that changes state does so through a guarded transition, so a duplicate
queue message ends harmlessly at the first guard (ADR 0002).
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .actions import ActionExecutor
from .config import Settings
from .evidence import EvidenceSource, condense
from .heuristics import diagnose_from_text
from .llm import LLM, SYSTEM_PROMPT, parse_diagnosis
from .messaging import Notifier
from .models import Diagnosis, Incident, ProposedAction, Status, make_event
from .observability import emit_metric, get_logger
from .redact import redact, redact_lines
from .retrieval import RunbookIndex
from .store import IncidentStore

log = get_logger("opspilot.graph")


class TriageState(TypedDict, total=False):
    incident_id: str
    mode: str  # "analyze" or "execute"
    skip: bool
    incident: dict[str, Any]
    logs: list[str]
    runbooks: list[dict[str, Any]]
    diagnosis: dict[str, Any]
    action: dict[str, Any]
    needs_approval: bool
    outcome: str
    final_status: str
    trace: Annotated[list[str], operator.add]


@dataclass
class Deps:
    settings: Settings
    store: IncidentStore
    evidence: EvidenceSource
    index: RunbookIndex
    llm: LLM
    executor: ActionExecutor
    notifier: Notifier


def build_user_prompt(incident: Incident, logs: list[str], runbooks: list[dict[str, Any]], max_chars: int) -> str:
    runbook_text = (
        "\n\n".join(f"[{r['id']}] {r['title']}\n{r['body'][:1200]}" for r in runbooks) or "(no matching runbook)"
    )
    prompt = (
        "INCIDENT\n"
        f"title: {incident.title}\n"
        f"service: {incident.service}\n"
        f"initial severity: {incident.severity.value}\n"
        f"description: {incident.description}\n\n"
        "<logs>\n" + ("\n".join(logs) or "(no log evidence available)") + "\n</logs>\n\n"
        "RUNBOOKS\n" + runbook_text
    )
    return prompt[:max_chars]


def build_graph(deps: Deps):
    settings, store, executor = deps.settings, deps.store, deps.executor

    def _incident(state: TriageState) -> Incident:
        return Incident.model_validate(state["incident"])

    # ---- analyze path ---------------------------------------------------------------

    def start_triage(state: TriageState) -> dict[str, Any]:
        incident_id = state["incident_id"]
        if not store.transition(incident_id, {Status.RECEIVED}, Status.TRIAGING, event=make_event("triage_started")):
            return {"skip": True, "trace": [f"skip: {incident_id} is not in RECEIVED state"]}
        incident = store.get(incident_id)
        return {"skip": False, "incident": incident.model_dump(mode="json"), "trace": ["triage started"]}

    def prepare_context(state: TriageState) -> dict[str, Any]:
        incident = _incident(state)
        title, hits_a = redact(incident.title)
        description, hits_b = redact(incident.description)
        clean = incident.model_copy(update={"title": title, "description": description})
        return {
            "incident": clean.model_dump(mode="json"),
            "trace": [f"context prepared, {hits_a + hits_b} redaction(s) in incident text"],
        }

    def gather_evidence(state: TriageState) -> dict[str, Any]:
        incident = _incident(state)
        log_group = incident.resource.get("log_group")
        if not log_group and incident.resource.get("function_name"):
            log_group = f"/aws/lambda/{incident.resource['function_name']}"
        lines: list[str] = []
        if log_group:
            try:
                lines = deps.evidence.fetch_logs(log_group, settings.log_window_minutes, settings.max_log_events)
            except Exception:  # missing log group, throttling, permissions: triage goes on without logs
                log.warning("evidence fetch failed", extra={"log_group": log_group}, exc_info=True)
        cleaned, hits = redact_lines(lines)
        trimmed = condense(cleaned, settings.max_log_events, settings.max_log_chars)
        return {"logs": trimmed, "trace": [f"evidence: {len(trimmed)} log line(s) kept, {hits} redaction(s)"]}

    def retrieve_runbooks(state: TriageState) -> dict[str, Any]:
        incident = _incident(state)
        # REPORT and START lines appear in every invocation and mention memory, so they would bias retrieval.
        signal = [line for line in state.get("logs", []) if not line.startswith(("REPORT", "START"))]
        query = f"{incident.title} {incident.description} " + " ".join(signal)
        hits = deps.index.search(query, k=2)
        runbooks = [{"id": h.id, "title": h.title, "score": h.score, "body": h.body} for h in hits]
        names = ", ".join(f"{r['id']}({r['score']})" for r in runbooks) or "none"
        return {"runbooks": runbooks, "trace": [f"runbooks: {names}"]}

    def diagnose(state: TriageState) -> dict[str, Any]:
        incident = _incident(state)
        runbooks = state.get("runbooks", [])
        prompt = build_user_prompt(incident, state.get("logs", []), runbooks, settings.max_prompt_chars)
        diagnosis: Diagnosis | None = None
        source = settings.llm_provider
        try:
            diagnosis = parse_diagnosis(deps.llm.complete(SYSTEM_PROMPT, prompt))
        except Exception:  # provider outage, throttling, expired credentials
            log.warning("llm call failed", exc_info=True)
        if diagnosis is None:
            emit_metric("LLMFallback")
            evidence = f"{incident.title}\n{incident.description}\n" + "\n".join(state.get("logs", []))
            diagnosis = Diagnosis.model_validate(diagnose_from_text(evidence, [r["id"] for r in runbooks]))
            source = "heuristic-fallback"
        record = {**diagnosis.model_dump(mode="json"), "source": source}
        return {
            "diagnosis": record,
            "action": diagnosis.action.model_dump(mode="json"),
            "trace": [f"diagnosis ({source}): {diagnosis.summary} [confidence {diagnosis.confidence:.2f}]"],
        }

    def policy_gate(state: TriageState) -> dict[str, Any]:
        incident = _incident(state)
        diagnosis = state["diagnosis"]
        proposed = ProposedAction.model_validate(state["action"])
        safe = executor.sanitize(proposed, incident, float(diagnosis["confidence"]))
        needs = executor.requires_approval(safe, Diagnosis.model_validate(diagnosis).severity)
        note = " (changed by policy)" if safe.type != proposed.type else ""
        return {
            "action": safe.model_dump(mode="json"),
            "needs_approval": needs,
            "trace": [f"policy: {safe.type.value}{note}, approval {'required' if needs else 'not required'}"],
        }

    def request_approval(state: TriageState) -> dict[str, Any]:
        incident_id = state["incident_id"]
        diagnosis = state["diagnosis"]
        fields = {"diagnosis": diagnosis, "proposed_action": state["action"], "severity": diagnosis["severity"]}
        ok = store.transition(
            incident_id,
            {Status.TRIAGING},
            Status.AWAITING_APPROVAL,
            fields=fields,
            event=make_event("approval_requested", state["action"]["type"]),
        )
        if not ok:
            return {"trace": ["approval request skipped: state changed"]}
        emit_metric("ApprovalRequested")
        deps.notifier.send(
            f"[OpsPilot][{diagnosis['severity']}] Approval needed: {incident_id}",
            f"{_incident(state).title}\n\nDiagnosis: {diagnosis['summary']}\n"
            f"Proposed action: {state['action']['type']} {state['action'].get('params', {})}\n"
            f"Why: {state['action'].get('rationale', '')}\n\n"
            f"Approve:  POST /incidents/{incident_id}/approve\nReject:   POST /incidents/{incident_id}/reject",
        )
        return {"final_status": Status.AWAITING_APPROVAL.value, "trace": ["waiting for human approval"]}

    # ---- execute path ---------------------------------------------------------------

    def load_for_execution(state: TriageState) -> dict[str, Any]:
        incident = store.get(state["incident_id"])
        if incident is None or incident.status is not Status.APPROVED or not incident.proposed_action:
            return {"skip": True, "trace": ["skip: incident is not in APPROVED state"]}
        confidence = float((incident.diagnosis or {}).get("confidence", 0))
        action = executor.sanitize(ProposedAction.model_validate(incident.proposed_action), incident, confidence)
        return {
            "skip": False,
            "incident": incident.model_dump(mode="json"),
            "diagnosis": incident.diagnosis or {},
            "action": action.model_dump(mode="json"),
            "trace": ["approved action loaded and re-checked by policy"],
        }

    # ---- shared tail ----------------------------------------------------------------

    def begin_execution(state: TriageState) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if state["mode"] == "analyze":
            fields = {
                "diagnosis": state["diagnosis"],
                "proposed_action": state["action"],
                "severity": state["diagnosis"]["severity"],
            }
        ok = store.transition(
            state["incident_id"],
            {Status.TRIAGING, Status.APPROVED},
            Status.EXECUTING,
            fields=fields,
            event=make_event("executing", state["action"]["type"]),
        )
        return {"skip": not ok, "trace": ["executing" if ok else "skip: could not start execution"]}

    def run_action(state: TriageState) -> dict[str, Any]:
        incident_id = state["incident_id"]
        incident = store.get(incident_id)
        action = ProposedAction.model_validate(state["action"])
        try:
            outcome = executor.execute(action, incident)
            final = Status.RESOLVED
            emit_metric("ActionExecuted")
        except Exception as exc:
            log.exception("action failed", extra={"incident_id": incident_id})
            outcome, final = f"Action failed: {exc}", Status.FAILED
            emit_metric("ActionFailed")
        store.transition(
            incident_id,
            {Status.EXECUTING},
            final,
            fields={"result": outcome},
            event=make_event(final.value.lower(), outcome),
        )
        return {"outcome": outcome, "final_status": final.value, "trace": [f"{final.value}: {outcome}"]}

    def notify_outcome(state: TriageState) -> dict[str, Any]:
        diagnosis = state.get("diagnosis", {})
        deps.notifier.send(
            f"[OpsPilot][{diagnosis.get('severity', 'P3')}] {state['final_status']}: {state['incident_id']}",
            f"Diagnosis: {diagnosis.get('summary', '')}\nAction: {state['action']['type']}\nResult: {state['outcome']}",
        )
        emit_metric("TriageCompleted")
        return {"trace": ["notification sent"]}

    # ---- wiring ---------------------------------------------------------------------

    def after_guard(next_node: str):
        return lambda state: END if state.get("skip") else next_node

    graph = StateGraph(TriageState)
    for name, fn in {
        "start_triage": start_triage,
        "prepare_context": prepare_context,
        "gather_evidence": gather_evidence,
        "retrieve_runbooks": retrieve_runbooks,
        "diagnose": diagnose,
        "policy_gate": policy_gate,
        "request_approval": request_approval,
        "load_for_execution": load_for_execution,
        "begin_execution": begin_execution,
        "run_action": run_action,
        "notify_outcome": notify_outcome,
    }.items():
        graph.add_node(name, fn)

    graph.add_conditional_edges(
        START,
        lambda state: "start_triage" if state["mode"] == "analyze" else "load_for_execution",
        {"start_triage": "start_triage", "load_for_execution": "load_for_execution"},
    )
    graph.add_conditional_edges(
        "start_triage", after_guard("prepare_context"), {"prepare_context": "prepare_context", END: END}
    )
    graph.add_edge("prepare_context", "gather_evidence")
    graph.add_edge("gather_evidence", "retrieve_runbooks")
    graph.add_edge("retrieve_runbooks", "diagnose")
    graph.add_edge("diagnose", "policy_gate")
    graph.add_conditional_edges(
        "policy_gate",
        lambda state: "request_approval" if state["needs_approval"] else "begin_execution",
        {"request_approval": "request_approval", "begin_execution": "begin_execution"},
    )
    graph.add_edge("request_approval", END)
    graph.add_conditional_edges(
        "load_for_execution", after_guard("begin_execution"), {"begin_execution": "begin_execution", END: END}
    )
    graph.add_conditional_edges("begin_execution", after_guard("run_action"), {"run_action": "run_action", END: END})
    graph.add_edge("run_action", "notify_outcome")
    graph.add_edge("notify_outcome", END)
    return graph.compile()


def run_analyze(app: Any, incident_id: str) -> dict[str, Any]:
    return app.invoke({"incident_id": incident_id, "mode": "analyze", "trace": []})


def run_execute(app: Any, incident_id: str) -> dict[str, Any]:
    return app.invoke({"incident_id": incident_id, "mode": "execute", "trace": []})
