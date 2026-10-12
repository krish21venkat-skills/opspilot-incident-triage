import unittest
from dataclasses import replace

from opspilot.actions import ActionExecutor
from opspilot.cli import StubLambda
from opspilot.config import Settings
from opspilot.models import ActionType, Incident, ProposedAction, Severity, Status, make_event
from opspilot.store import MemoryStore


def make_incident(function_name="opspilot-demo-checkout-dev", title="Errors alarm") -> Incident:
    return Incident.new(source="test", title=title, resource={"function_name": function_name})


class StoreTests(unittest.TestCase):
    def test_duplicate_alert_returns_existing_incident_and_counts(self):
        store = MemoryStore()
        first, created_a = store.create_incident(make_incident(), 1800)
        second, created_b = store.create_incident(make_incident(), 1800)
        self.assertTrue(created_a)
        self.assertFalse(created_b)
        self.assertEqual(second.incident_id, first.incident_id)
        self.assertEqual(store.get(first.incident_id).occurrences, 2)

    def test_dedup_window_expires(self):
        now = [1000.0]
        store = MemoryStore(clock=lambda: now[0])
        first, _ = store.create_incident(make_incident(), 60)
        now[0] += 61
        second, created = store.create_incident(make_incident(), 60)
        self.assertTrue(created)
        self.assertNotEqual(first.incident_id, second.incident_id)

    def test_different_functions_are_not_duplicates(self):
        store = MemoryStore()
        store.create_incident(make_incident("opspilot-demo-a-dev"), 1800)
        _, created = store.create_incident(make_incident("opspilot-demo-b-dev"), 1800)
        self.assertTrue(created)

    def test_transition_is_a_compare_and_set(self):
        store = MemoryStore()
        incident, _ = store.create_incident(make_incident(), 1800)
        wrong = store.transition(incident.incident_id, {Status.APPROVED}, Status.EXECUTING)
        right = store.transition(
            incident.incident_id,
            {Status.RECEIVED},
            Status.TRIAGING,
            fields={"result": "x"},
            event=make_event("triage_started"),
        )
        again = store.transition(incident.incident_id, {Status.RECEIVED}, Status.TRIAGING)
        self.assertEqual((wrong, right, again), (False, True, False))
        saved = store.get(incident.incident_id)
        self.assertEqual(saved.status, Status.TRIAGING)
        self.assertEqual(saved.result, "x")
        self.assertEqual(saved.events[-1]["event"], "triage_started")

    def test_transition_on_missing_incident_is_false(self):
        self.assertFalse(MemoryStore().transition("nope", {Status.RECEIVED}, Status.TRIAGING))

    def test_list_recent_filters_by_status(self):
        store = MemoryStore()
        a, _ = store.create_incident(make_incident("opspilot-demo-a-dev"), 1800)
        store.create_incident(make_incident("opspilot-demo-b-dev"), 1800)
        store.transition(a.incident_id, {Status.RECEIVED}, Status.TRIAGING)
        self.assertEqual(len(store.list_recent()), 2)
        self.assertEqual([i.incident_id for i in store.list_recent(Status.TRIAGING)], [a.incident_id])


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(Settings(), allow_mutating_actions=True)
        self.stub = StubLambda(128)
        self.executor = ActionExecutor(self.settings, lambda_client=self.stub)
        self.incident = make_incident()

    def memory_action(self, memory=256, target="anything"):
        return ProposedAction(type=ActionType.SET_LAMBDA_MEMORY, params={"function_name": target, "memory_mb": memory})

    def test_target_always_comes_from_the_incident_not_the_model(self):
        safe = self.executor.sanitize(self.memory_action(target="prod-payments-api"), self.incident, 0.9)
        self.assertEqual(safe.params["function_name"], "opspilot-demo-checkout-dev")

    def test_memory_request_is_clamped(self):
        def clamped(request):
            return self.executor.sanitize(self.memory_action(request), self.incident, 0.9).params["memory_mb"]

        self.assertEqual(clamped(10240), 1024)
        self.assertEqual(clamped(1), 128)

    def test_unmanaged_target_becomes_a_ticket(self):
        outsider = make_incident("prod-payments-api")
        safe = self.executor.sanitize(self.memory_action(), outsider, 0.9)
        self.assertEqual(safe.type, ActionType.CREATE_TICKET)

    def test_low_confidence_infrastructure_change_becomes_a_ticket(self):
        safe = self.executor.sanitize(self.memory_action(), self.incident, 0.3)
        self.assertEqual(safe.type, ActionType.CREATE_TICKET)

    def test_approval_rules(self):
        change = self.memory_action()
        ticket = ProposedAction(type=ActionType.CREATE_TICKET)
        notify = ProposedAction(type=ActionType.NOTIFY_ONLY)
        self.assertTrue(self.executor.requires_approval(change, Severity.P3))
        self.assertFalse(self.executor.requires_approval(ticket, Severity.P3))
        self.assertTrue(self.executor.requires_approval(ticket, Severity.P1))
        self.assertFalse(self.executor.requires_approval(notify, Severity.P1))

    def test_memory_change_is_applied_one_step_at_a_time(self):
        safe = self.executor.sanitize(self.memory_action(1024), self.incident, 0.9)
        result = self.executor.execute(safe, self.incident)
        self.assertEqual(self.stub.memory, 256)  # capped at double the current 128
        self.assertIn("128 MB to 256 MB", result)

    def test_dry_run_changes_nothing(self):
        dry = ActionExecutor(replace(self.settings, allow_mutating_actions=False), lambda_client=self.stub)
        safe = dry.sanitize(self.memory_action(256), self.incident, 0.9)
        self.assertIn("DRY-RUN", dry.execute(safe, self.incident))
        self.assertEqual(self.stub.memory, 128)

    def test_no_change_when_already_large_enough(self):
        self.stub.memory = 512
        safe = self.executor.sanitize(self.memory_action(256), self.incident, 0.9)
        self.assertIn("No change", self.executor.execute(safe, self.incident))
        self.assertEqual(self.stub.memory, 512)

    def test_ticket_is_drafted_locally_without_a_bucket(self):
        result = self.executor.execute(ProposedAction(type=ActionType.CREATE_TICKET), self.incident)
        self.assertIn("drafted locally", result)
