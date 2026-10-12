import unittest

from opspilot.graph import run_analyze, run_execute
from opspilot.llm import parse_diagnosis
from opspilot.models import Status, make_event
from tests.helpers import BrokenLLM, ScriptedLLM, diagnosis_json, make_rig, sample_incident


def start(rig, name):
    incident, _ = rig.store.create_incident(sample_incident(name), 1800)
    return incident.incident_id


def approve(rig, incident_id):
    assert rig.store.transition(
        incident_id,
        {Status.AWAITING_APPROVAL},
        Status.APPROVED,
        fields={"approval": {"decision": "approve"}},
        event=make_event("approve"),
    )


class WorkflowTests(unittest.TestCase):
    def test_out_of_memory_waits_for_approval_then_raises_memory(self):
        rig = make_rig()
        incident_id = start(rig, "lambda_oom")
        run_analyze(rig.app, incident_id)

        waiting = rig.store.get(incident_id)
        self.assertEqual(waiting.status, Status.AWAITING_APPROVAL)
        self.assertEqual(waiting.proposed_action["type"], "SET_LAMBDA_MEMORY")
        self.assertEqual(rig.lambda_stub.memory, 128)  # nothing changed before approval
        self.assertIn("Approval needed", rig.notifier.sent[-1][0])

        approve(rig, incident_id)
        run_execute(rig.app, incident_id)
        done = rig.store.get(incident_id)
        self.assertEqual(done.status, Status.RESOLVED)
        self.assertEqual(rig.lambda_stub.memory, 256)
        self.assertIn("RESOLVED", rig.notifier.sent[-1][0])
        names = [e["event"] for e in done.events]
        self.assertEqual(names[:4], ["created", "triage_started", "approval_requested", "approve"])

    def test_dry_run_deployment_never_changes_the_function(self):
        rig = make_rig(allow_mutating=False)
        incident_id = start(rig, "lambda_oom")
        run_analyze(rig.app, incident_id)
        approve(rig, incident_id)
        run_execute(rig.app, incident_id)
        self.assertIn("DRY-RUN", rig.store.get(incident_id).result)
        self.assertEqual(rig.lambda_stub.memory, 128)

    def test_duplicate_delivery_is_skipped(self):
        rig = make_rig()
        incident_id = start(rig, "lambda_oom")
        run_analyze(rig.app, incident_id)
        second = run_analyze(rig.app, incident_id)
        self.assertTrue(second["skip"])
        self.assertEqual(len([e for e in rig.store.get(incident_id).events if e["event"] == "triage_started"]), 1)

    def test_execute_without_approval_does_nothing(self):
        rig = make_rig()
        incident_id = start(rig, "lambda_oom")
        run_analyze(rig.app, incident_id)
        result = run_execute(rig.app, incident_id)  # never approved
        self.assertTrue(result["skip"])
        self.assertEqual(rig.lambda_stub.memory, 128)
        self.assertEqual(rig.store.get(incident_id).status, Status.AWAITING_APPROVAL)

    def test_execute_cannot_run_twice(self):
        rig = make_rig()
        incident_id = start(rig, "lambda_oom")
        run_analyze(rig.app, incident_id)
        approve(rig, incident_id)
        run_execute(rig.app, incident_id)
        again = run_execute(rig.app, incident_id)
        self.assertTrue(again["skip"])
        self.assertEqual(rig.lambda_stub.memory, 256)

    def test_timeout_becomes_a_ticket_without_approval(self):
        rig = make_rig()
        incident_id = start(rig, "lambda_timeout")
        run_analyze(rig.app, incident_id)
        incident = rig.store.get(incident_id)
        self.assertEqual(incident.status, Status.RESOLVED)
        self.assertEqual(incident.proposed_action["type"], "CREATE_TICKET")
        self.assertEqual(incident.diagnosis["runbook_id"], "RB-LAMBDA-TIMEOUT")

    def test_secrets_never_reach_the_model(self):
        llm = ScriptedLLM(diagnosis_json(action="CREATE_TICKET"))
        rig = make_rig(llm=llm)
        run_analyze(rig.app, start(rig, "lambda_exception"))
        prompt = llm.prompts[0]
        self.assertNotIn("sk-test-1234567890abcdef", prompt)
        self.assertNotIn("jane.doe@example.com", prompt)
        self.assertNotIn("10.1.2.3", prompt)
        self.assertIn("<logs>", prompt)

    def test_instruction_hidden_in_logs_cannot_redirect_the_action(self):
        # A compromised model obeys the log line and asks for 10 GB on a production function.
        llm = ScriptedLLM(diagnosis_json(memory_mb=10240, confidence=0.95))
        rig = make_rig(llm=llm)
        incident_id = start(rig, "lambda_exception")
        run_analyze(rig.app, incident_id)

        waiting = rig.store.get(incident_id)
        self.assertEqual(waiting.status, Status.AWAITING_APPROVAL)
        params = waiting.proposed_action["params"]
        self.assertEqual(params["function_name"], "opspilot-demo-notifier-dev")
        self.assertEqual(params["memory_mb"], 1024)

        approve(rig, incident_id)
        run_execute(rig.app, incident_id)
        self.assertEqual(rig.lambda_stub.memory, 256)  # one step up, never 10240

    def test_low_confidence_change_is_downgraded_to_a_ticket(self):
        rig = make_rig(llm=ScriptedLLM(diagnosis_json(confidence=0.2)))
        incident_id = start(rig, "lambda_oom")
        run_analyze(rig.app, incident_id)
        incident = rig.store.get(incident_id)
        self.assertEqual(incident.proposed_action["type"], "CREATE_TICKET")
        self.assertEqual(incident.status, Status.RESOLVED)
        self.assertEqual(rig.lambda_stub.memory, 128)

    def test_p1_ticket_still_needs_a_human(self):
        rig = make_rig(llm=ScriptedLLM(diagnosis_json(action="CREATE_TICKET", severity="P1")))
        incident_id = start(rig, "lambda_timeout")
        run_analyze(rig.app, incident_id)
        self.assertEqual(rig.store.get(incident_id).status, Status.AWAITING_APPROVAL)

    def test_garbage_model_output_falls_back_to_heuristics(self):
        rig = make_rig(llm=ScriptedLLM("Sorry, I cannot help with that."))
        incident_id = start(rig, "lambda_oom")
        run_analyze(rig.app, incident_id)
        self.assertEqual(rig.store.get(incident_id).diagnosis["source"], "heuristic-fallback")

    def test_provider_outage_falls_back_to_heuristics(self):
        rig = make_rig(llm=BrokenLLM())
        incident_id = start(rig, "lambda_timeout")
        run_analyze(rig.app, incident_id)
        incident = rig.store.get(incident_id)
        self.assertEqual(incident.diagnosis["source"], "heuristic-fallback")
        self.assertEqual(incident.status, Status.RESOLVED)

    def test_model_output_is_validated(self):
        self.assertIsNotNone(parse_diagnosis("Here you go:\n" + diagnosis_json() + "\nThanks"))
        self.assertIsNone(parse_diagnosis('{"summary": "missing fields"}'))
        self.assertIsNone(parse_diagnosis(diagnosis_json(action="DELETE_EVERYTHING")))
        self.assertIsNone(parse_diagnosis(diagnosis_json(confidence=7)))


if __name__ == "__main__":
    unittest.main()
