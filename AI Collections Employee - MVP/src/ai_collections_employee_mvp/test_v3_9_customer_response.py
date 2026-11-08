"""Tests for customer response service v3.9 inbox and classification handling."""
import gc
import tempfile
import unittest
from pathlib import Path

import customer_response_service_v3_9 as svc


class ResponseInboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = svc.DB_PATH
        svc.DB_PATH = Path(self.tmp.name) / "test.db"
        self.actor = {"id": 1, "username": "collector_a", "role": "collector"}
        self.viewer = {"id": 2, "username": "viewer_a", "role": "viewer"}

    def tearDown(self):
        svc.DB_PATH = self.old
        # Force garbage collection to release SQLite file handles on Windows before cleanup
        gc.collect()
        self.tmp.cleanup()

    def test_save_and_persist_response(self):
        r = svc.save_customer_response(
            self.actor,
            invoice_number="INV-TEST-1",
            customer_name="Synthetic Customer",
            message_text="We expect to pay next Friday."
        )
        self.assertEqual(r["disposition"], "Needs review")
        self.assertEqual(r["requires_human_review"], 1)
        self.assertEqual(len(svc.list_customer_responses(self.actor)), 1)

    def test_reject_invalid_category(self):
        r = svc.save_customer_response(
            self.actor,
            invoice_number="INV-TEST-2",
            customer_name="Synthetic Customer",
            message_text="Please resend invoice."
        )
        with self.assertRaises(ValueError):
            svc.update_classification(
                self.actor, r["id"], expected_version=1,
                category="Invented", sentiment="Neutral", recommended_action="Review"
            )

    def test_optimistic_locking(self):
        r = svc.save_customer_response(
            self.actor,
            invoice_number="INV-TEST-3",
            customer_name="Synthetic Customer",
            message_text="We dispute this invoice."
        )
        svc.update_classification(
            self.actor, r["id"], expected_version=1,
            category="Invoice dispute", sentiment="Negative",
            recommended_action="Escalate", disposition="Dispute escalation"
        )
        with self.assertRaises(ValueError):
            svc.update_classification(
                self.actor, r["id"], expected_version=1,
                category="Invoice dispute", sentiment="Negative", recommended_action="Escalate"
            )

    def test_viewer_cannot_create(self):
        with self.assertRaises(PermissionError):
            svc.save_customer_response(
                self.viewer,
                invoice_number="INV-X",
                customer_name="Customer",
                message_text="Hello, this is a synthetic message."
            )

    def test_response_length_is_bounded(self):
        with self.assertRaises(ValueError):
            svc.save_customer_response(
                self.actor,
                invoice_number="INV-X",
                customer_name="Customer",
                message_text="x" * 12001
            )


if __name__ == "__main__":
    unittest.main()