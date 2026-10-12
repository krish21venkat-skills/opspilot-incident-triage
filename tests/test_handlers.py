import contextlib
import io
import json
import unittest

from opspilot import bootstrap
from opspilot.cli import main as cli_main
from opspilot.handlers import alarm_ingest, api, authorizer, triage_worker
from opspilot.messaging import JobQueue
from opspilot.models import Status, make_event
from tests.helpers import SAMPLES, make_rig, make_runtime


def request(route, body=None, path=None, query=None):
    event = {"routeKey": route, "pathParameters": path or {}, "queryStringParameters": query or {}}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def body_of(response):
    return json.loads(response["body"])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.rig = make_rig()
        self.rt = make_runtime(self.rig)
        self.payload = {
            "title": "Errors alarm on opspilot-demo-checkout-dev",
            "severity": "P2",
            "resource": {"function_name": "opspilot-demo-checkout-dev"},
        }

    def test_create_stores_the_incident_and_queues_a_job(self):
        response = api.route(request("POST /incidents", self.payload), self.rt)
        self.assertEqual(response["statusCode"], 201)
        incident = body_of(response)["incident"]
        self.assertEqual(incident["resource"]["log_group"], "/aws/lambda/opspilot-demo-checkout-dev")
        self.assertEqual(self.rig.queue.messages, [{"action": "analyze", "incident_id": incident["incident_id"]}])

    def test_repeat_alert_is_deduplicated(self):
        first = body_of(api.route(request("POST /incidents", self.payload), self.rt))["incident"]
        second = api.route(request("POST /incidents", self.payload), self.rt)
        self.assertEqual(second["statusCode"], 200)
        self.assertTrue(body_of(second)["duplicate"])
        self.assertEqual(body_of(second)["incident"]["incident_id"], first["incident_id"])
        self.assertEqual(body_of(second)["incident"]["occurrences"], 2)

    def test_input_validation(self):
        for bad in ({}, {"title": "x" * 201}, {"title": "ok", "severity": "P9"}, {"title": "ok", "resource": "nope"}):
            self.assertEqual(api.route(request("POST /incidents", bad), self.rt)["statusCode"], 400, bad)
        broken = {"routeKey": "POST /incidents", "body": "{not json"}
        self.assertEqual(api.route(broken, self.rt)["statusCode"], 400)

    def test_get_and_list(self):
        created = body_of(api.route(request("POST /incidents", self.payload), self.rt))["incident"]
        found = api.route(request("GET /incidents/{id}", path={"id": created["incident_id"]}), self.rt)
        self.assertEqual(found["statusCode"], 200)
        listing = body_of(api.route(request("GET /incidents", query={"status": "received"}), self.rt))
        self.assertEqual(len(listing["incidents"]), 1)
        self.assertEqual(api.route(request("GET /incidents/{id}", path={"id": "INC-0"}), self.rt)["statusCode"], 404)
        self.assertEqual(api.route(request("GET /incidents", query={"status": "bogus"}), self.rt)["statusCode"], 400)

    def awaiting(self):
        created = body_of(api.route(request("POST /incidents", self.payload), self.rt))["incident"]
        self.rig.store.transition(created["incident_id"], {Status.RECEIVED}, Status.AWAITING_APPROVAL)
        return created["incident_id"]

    def test_approve_moves_state_and_queues_execution_once(self):
        incident_id = self.awaiting()
        first = api.route(request("POST /incidents/{id}/approve", {"by": "krish"}, {"id": incident_id}), self.rt)
        second = api.route(request("POST /incidents/{id}/approve", {"by": "krish"}, {"id": incident_id}), self.rt)
        self.assertEqual((first["statusCode"], second["statusCode"]), (202, 409))
        self.assertEqual(self.rig.queue.messages[-1], {"action": "execute", "incident_id": incident_id})
        self.assertEqual(self.rig.store.get(incident_id).approval["by"], "krish")

    def test_cannot_approve_an_incident_that_is_not_waiting(self):
        created = body_of(api.route(request("POST /incidents", self.payload), self.rt))["incident"]
        response = api.route(request("POST /incidents/{id}/approve", {}, {"id": created["incident_id"]}), self.rt)
        self.assertEqual(response["statusCode"], 409)

    def test_reject_closes_the_incident_without_queueing(self):
        incident_id = self.awaiting()
        before = len(self.rig.queue.messages)
        response = api.route(request("POST /incidents/{id}/reject", {"by": "krish"}, {"id": incident_id}), self.rt)
        self.assertEqual(response["statusCode"], 202)
        self.assertEqual(self.rig.store.get(incident_id).status, Status.REJECTED)
        self.assertEqual(len(self.rig.queue.messages), before)

    def test_failed_enqueue_reverts_the_approval(self):
        incident_id = self.awaiting()

        class DownQueue(JobQueue):
            def send(self, message):
                raise ConnectionError("sqs down")

        self.rt.queue = DownQueue()
        response = api.route(request("POST /incidents/{id}/approve", {}, {"id": incident_id}), self.rt)
        self.assertEqual(response["statusCode"], 503)
        self.assertEqual(self.rig.store.get(incident_id).status, Status.AWAITING_APPROVAL)

    def test_unknown_route_and_missing_incident(self):
        self.assertEqual(api.route({"routeKey": "GET /nope"}, self.rt)["statusCode"], 404)
        response = api.route(request("POST /incidents/{id}/approve", {}, {"id": "INC-0"}), self.rt)
        self.assertEqual(response["statusCode"], 404)


class AuthorizerTests(unittest.TestCase):
    def test_token_check(self):
        self.assertTrue(authorizer.check_token("Bearer s3cret-token", "s3cret-token"))
        self.assertTrue(authorizer.check_token("bearer s3cret-token", "s3cret-token"))
        for header in ("", "Bearer", "Bearer wrong", "Basic s3cret-token", "s3cret-token"):
            self.assertFalse(authorizer.check_token(header, "s3cret-token"), header)
        self.assertFalse(authorizer.check_token("Bearer ", ""))

    def test_denies_when_the_secret_cannot_be_loaded(self):
        original = authorizer.get_api_token

        def boom(_settings):
            raise RuntimeError("ssm unavailable")

        authorizer.get_api_token = boom
        try:
            result = authorizer.handler({"headers": {"authorization": "Bearer anything"}}, None)
        finally:
            authorizer.get_api_token = original
        self.assertFalse(result["isAuthorized"])


ALARM_EVENT = {
    "source": "aws.cloudwatch",
    "detail-type": "CloudWatch Alarm State Change",
    "detail": {
        "alarmName": "opspilot-demo-workload-errors-dev",
        "state": {"value": "ALARM", "reason": "Threshold Crossed: 1 datapoint [3.0] >= 1.0"},
        "configuration": {
            "description": "The demo workload reported errors.",
            "metrics": [
                {
                    "metricStat": {
                        "metric": {
                            "namespace": "AWS/Lambda",
                            "name": "Errors",
                            "dimensions": {"FunctionName": "opspilot-demo-workload-dev"},
                        }
                    }
                }
            ],
        },
    },
}


class IngestAndWorkerTests(unittest.TestCase):
    def setUp(self):
        self.rig = make_rig()
        bootstrap.set_runtime(make_runtime(self.rig))
        self.addCleanup(bootstrap.set_runtime, None)

    def test_alarm_event_becomes_an_incident_with_its_function_and_log_group(self):
        incident = alarm_ingest.incident_from_alarm_event(ALARM_EVENT)
        self.assertEqual(incident.resource["function_name"], "opspilot-demo-workload-dev")
        self.assertEqual(incident.resource["log_group"], "/aws/lambda/opspilot-demo-workload-dev")
        self.assertIn("Threshold Crossed", incident.description)

    def test_alarm_without_a_function_still_creates_an_incident(self):
        event = {"detail": {"alarmName": "opspilot-demo-queue-depth", "state": {"reason": "deep"}}}
        incident = alarm_ingest.incident_from_alarm_event(event)
        self.assertNotIn("function_name", incident.resource)

    def test_alarm_handler_stores_and_queues_once(self):
        first = alarm_ingest.handler(ALARM_EVENT, None)
        second = alarm_ingest.handler(ALARM_EVENT, None)
        self.assertEqual((first["created"], second["created"]), (True, False))
        self.assertEqual(self.rig.store.get(first["incident_id"]).occurrences, 2)
        # The duplicate re-queues only because the first job has not started, which the guards make safe.
        self.assertEqual({m["incident_id"] for m in self.rig.queue.messages}, {first["incident_id"]})

    def test_worker_runs_a_job_and_reports_no_failures(self):
        incident_id = alarm_ingest.handler(ALARM_EVENT, None)["incident_id"]
        job = json.dumps({"action": "analyze", "incident_id": incident_id})
        event = {"Records": [{"messageId": "m1", "body": job}]}
        self.assertEqual(triage_worker.handler(event, None), {"batchItemFailures": []})
        # No logs exist for the demo function in the sample folder, so the evidence is inconclusive.
        self.assertEqual(self.rig.store.get(incident_id).status, Status.RESOLVED)

    def test_worker_returns_only_the_bad_messages(self):
        event = {
            "Records": [
                {"messageId": "bad-json", "body": "{nope"},
                {"messageId": "bad-action", "body": json.dumps({"action": "explode", "incident_id": "x"})},
            ]
        }
        failures = triage_worker.handler(event, None)["batchItemFailures"]
        self.assertEqual([f["itemIdentifier"] for f in failures], ["bad-json", "bad-action"])

    def test_worker_releases_the_incident_when_triage_crashes(self):
        incident_id = alarm_ingest.handler(ALARM_EVENT, None)["incident_id"]
        self.rig.store.transition(incident_id, {Status.RECEIVED}, Status.TRIAGING, event=make_event("triage_started"))

        class Crashing:
            def invoke(self, _state):
                raise RuntimeError("dynamodb throttled")

        bootstrap.get_runtime().graph = Crashing()
        job = json.dumps({"action": "analyze", "incident_id": incident_id})
        event = {"Records": [{"messageId": "m1", "body": job}]}
        failures = triage_worker.handler(event, None)["batchItemFailures"]
        self.assertEqual(failures, [{"itemIdentifier": "m1"}])
        self.assertEqual(self.rig.store.get(incident_id).status, Status.RECEIVED)  # eligible for retry


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli_main(list(args))
        return code, out.getvalue()

    def test_offline_demo_runs_end_to_end(self):
        code, text = self.run_cli("triage", str(SAMPLES / "incidents" / "lambda_oom.json"), "--approve")
        self.assertEqual(code, 0)
        self.assertIn("Final status: RESOLVED", text)
        self.assertIn("Raised opspilot-demo-checkout-dev from 128 MB to 256 MB", text)

    def test_rejecting_closes_the_incident(self):
        _, text = self.run_cli("triage", str(SAMPLES / "incidents" / "lambda_oom.json"), "--reject")
        self.assertIn("Final status: REJECTED", text)

    def test_without_a_decision_it_stays_pending(self):
        _, text = self.run_cli("triage", str(SAMPLES / "incidents" / "lambda_oom.json"))
        self.assertIn("Final status: AWAITING_APPROVAL", text)


if __name__ == "__main__":
    unittest.main()
