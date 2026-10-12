import unittest

from opspilot.config import Settings
from opspilot.redact import redact, redact_lines
from opspilot.retrieval import RunbookIndex


class RedactTests(unittest.TestCase):
    def test_removes_secrets_and_personal_data(self):
        text = (
            "key AKIAABCDEFGHIJKLMNOP failed for jane.doe@example.com from 10.1.2.3 "
            "with api_key=sk-test-123 and Bearer abcdefghijklmnop1234"
        )
        cleaned, count = redact(text)
        secrets = ("AKIAABCDEFGHIJKLMNOP", "jane.doe@example.com", "10.1.2.3", "sk-test-123", "abcdefghijklmnop1234")
        for secret in secrets:
            self.assertNotIn(secret, cleaned)
        self.assertGreaterEqual(count, 5)

    def test_plain_text_is_untouched(self):
        cleaned, count = redact("Task timed out after 3.00 seconds")
        self.assertEqual((cleaned, count), ("Task timed out after 3.00 seconds", 0))

    def test_redact_lines_counts_across_lines(self):
        cleaned, count = redact_lines(["ok", "password=hunter2", "mail bob@example.org"])
        self.assertEqual(count, 2)
        self.assertEqual(cleaned[0], "ok")


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = RunbookIndex.from_directory(Settings().runbook_dir)

    def test_out_of_memory_query_finds_the_memory_runbook(self):
        hits = self.index.search("Runtime exited with error: signal: killed Runtime.OutOfMemory Max Memory Used")
        self.assertEqual(hits[0].id, "RB-LAMBDA-OOM")

    def test_timeout_query_finds_the_timeout_runbook(self):
        hits = self.index.search("Task timed out after 3.00 seconds")
        self.assertEqual(hits[0].id, "RB-LAMBDA-TIMEOUT")

    def test_exception_query_finds_the_error_runbook(self):
        hits = self.index.search("Traceback ValueError unhandled exception stack trace")
        self.assertEqual(hits[0].id, "RB-LAMBDA-ERRORS")

    def test_nonsense_query_returns_nothing(self):
        self.assertEqual(self.index.search("zzzz qqqq"), [])

    def test_k_limits_results(self):
        self.assertLessEqual(len(self.index.search("lambda error timeout memory", k=1)), 1)
