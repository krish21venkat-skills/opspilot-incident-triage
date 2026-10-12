import time
import unittest
from decimal import Decimal

from opspilot.models import Incident, Status, make_event
from opspilot.store import DynamoStore, _from_ddb, _to_ddb
from tests.fake_dynamodb import FakeResource


def make_incident(function_name="opspilot-demo-checkout-dev") -> Incident:
    return Incident.new(source="test", title="Errors alarm", resource={"function_name": function_name})


class DynamoStoreTests(unittest.TestCase):
    def setUp(self):
        self.resource = FakeResource()
        self.store = DynamoStore("incidents", resource=self.resource)
        self.table = self.resource.table

    def test_round_trip_with_floats_and_nested_data(self):
        incident, created = self.store.create_incident(make_incident(), 1800)
        self.assertTrue(created)
        self.store.transition(
            incident.incident_id,
            {Status.RECEIVED},
            Status.AWAITING_APPROVAL,
            fields={"diagnosis": {"confidence": 0.82, "summary": "x"}},
            event=make_event("approval_requested"),
        )
        saved = self.store.get(incident.incident_id)
        self.assertEqual(saved.diagnosis["confidence"], 0.82)
        self.assertEqual(saved.status, Status.AWAITING_APPROVAL)
        self.assertEqual([e["event"] for e in saved.events], ["created", "approval_requested"])

    def test_floats_are_converted_before_writing(self):
        self.assertEqual(_to_ddb({"a": [0.5, {"b": 1.25}]}), {"a": [Decimal("0.5"), {"b": Decimal("1.25")}]})
        self.assertEqual(_from_ddb({"a": Decimal("2"), "b": Decimal("0.5")}), {"a": 2, "b": 0.5})

    def test_duplicate_alert_returns_the_original_and_counts_it(self):
        first, _ = self.store.create_incident(make_incident(), 1800)
        again, created = self.store.create_incident(make_incident(), 1800)
        self.assertFalse(created)
        self.assertEqual(again.incident_id, first.incident_id)
        self.assertEqual(self.store.get(first.incident_id).occurrences, 2)
        self.assertEqual(len([k for k in self.table.items if k.startswith("INC#")]), 1)

    def test_expired_marker_allows_a_new_incident(self):
        first, _ = self.store.create_incident(make_incident(), 1800)
        marker = next(v for k, v in self.table.items.items() if k.startswith("DEDUP#"))
        marker["ttl"] = int(time.time()) - 10  # TTL cleanup can lag, so expiry is checked in the condition
        second, created = self.store.create_incident(make_incident(), 1800)
        self.assertTrue(created)
        self.assertNotEqual(first.incident_id, second.incident_id)

    def test_transition_only_succeeds_from_an_expected_status(self):
        incident, _ = self.store.create_incident(make_incident(), 1800)
        self.assertFalse(self.store.transition(incident.incident_id, {Status.APPROVED}, Status.EXECUTING))
        self.assertTrue(self.store.transition(incident.incident_id, {Status.RECEIVED}, Status.TRIAGING))
        self.assertFalse(self.store.transition(incident.incident_id, {Status.RECEIVED}, Status.TRIAGING))

    def test_transition_never_creates_a_missing_incident(self):
        self.assertFalse(self.store.transition("INC-missing", {Status.RECEIVED}, Status.TRIAGING))
        self.assertNotIn("INC#INC-missing", self.table.items)

    def test_list_recent_is_newest_first_and_filters_by_status(self):
        ids = []
        for i in range(3):
            incident = make_incident(f"opspilot-demo-fn{i}-dev")
            incident.created_at = f"2026-10-12T00:00:0{i}+00:00"
            ids.append(self.store.create_incident(incident, 1800)[0].incident_id)
        self.store.transition(ids[0], {Status.RECEIVED}, Status.TRIAGING)
        listed = [i.incident_id for i in self.store.list_recent()]
        self.assertEqual(listed, [ids[2], ids[1], ids[0]])
        self.assertEqual([i.incident_id for i in self.store.list_recent(Status.TRIAGING)], [ids[0]])

    def test_list_recent_pages_past_a_filter(self):
        for i in range(30):
            incident = make_incident(f"opspilot-demo-fn{i:02d}-dev")
            incident.created_at = f"2026-10-12T00:00:{i:02d}+00:00"
            stored, _ = self.store.create_incident(incident, 1800)
            if i == 0:  # the oldest incident is the only match, so it sits on the second page
                self.store.transition(stored.incident_id, {Status.RECEIVED}, Status.TRIAGING)
        found = self.store.list_recent(Status.TRIAGING, limit=5)
        self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
