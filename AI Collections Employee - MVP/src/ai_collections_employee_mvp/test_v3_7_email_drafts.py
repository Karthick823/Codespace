"""V3.7 email draft-only service tests; no email provider is used."""
import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import email_draft_service as drafts

ACTOR = {"id": 7, "username": "collector.one", "display_name": "Collector One", "role": "collector"}
REVIEWER = {"id": 8, "username": "reviewer.one", "display_name": "Reviewer One", "role": "reviewer"}
VIEWER = {"id": 9, "username": "viewer.one", "display_name": "Viewer One", "role": "viewer"}

class V37EmailDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "email_test.db"
        self.db_patch = patch.object(drafts, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.audit_patch = patch.object(drafts, "_audit")
        self.audit_patch.start()
        drafts.initialize_email_drafts()

    def tearDown(self):
        self.audit_patch.stop()
        self.db_patch.stop()
        # Force garbage collection to release SQLite file handles on Windows before cleanup
        gc.collect()
        self.temp.cleanup()

    def _save(self):
        return drafts.save_email_draft(
            ACTOR, "INV-TEST-100", "Example Customer", "ap@example.com",
            "Payment reminder for invoice INV-TEST-100",
            "Hello Example Customer, our records show invoice INV-TEST-100 remains outstanding. "
            "Please confirm the expected payment date. Thank you.",
            "Professional", "Request payment date", True
        )

    def test_valid_draft_saves_persistently(self):
        saved = self._save()
        self.assertEqual(saved["status"], "Draft")
        self.assertEqual(saved["invoice_number"], "INV-TEST-100")
        self.assertEqual(saved["recipient_email"], "ap@example.com")
        loaded = drafts.get_email_draft(ACTOR, saved["id"])
        self.assertEqual(loaded["body"], saved["body"])

    def test_invalid_recipient_rejected(self):
        with self.assertRaises(ValueError):
            drafts.save_email_draft(ACTOR, "INV-1", "Customer", "not-an-email",
                                    "Payment reminder INV-1",
                                    "Hello, please review the invoice and confirm the payment date. Thank you.",
                                    human_review_required=True)

    def test_multiple_recipients_rejected(self):
        result = drafts.validate_email_draft("INV-1", "Customer", "a@example.com,b@example.com",
                    "Payment reminder INV-1",
                    "Hello, please review invoice INV-1 and confirm the payment date. Thank you.")
        self.assertFalse(result["valid"])
        self.assertTrue(any("one recipient" in x.lower() for x in result["errors"]))

    def test_unresolved_placeholder_rejected(self):
        result = drafts.validate_email_draft("INV-1", "Customer", "a@example.com",
                    "Payment reminder INV-1",
                    "Hello [insert customer name], please pay invoice INV-1 and confirm the payment date.")
        self.assertFalse(result["valid"])

    def test_human_review_cannot_be_disabled(self):
        result = drafts.validate_email_draft("INV-1", "Customer", "a@example.com",
                    "Payment reminder INV-1",
                    "Hello, please review invoice INV-1 and confirm the payment date. Thank you.",
                    human_review_required=False)
        self.assertFalse(result["valid"])

    def test_viewer_cannot_create_draft(self):
        with self.assertRaises(PermissionError):
            drafts.save_email_draft(VIEWER, "INV-1", "Customer", "a@example.com",
                                    "Payment reminder INV-1",
                                    "Hello, please review invoice INV-1 and confirm the payment date. Thank you.")

    def test_collector_cannot_read_another_users_draft(self):
        saved = self._save()
        another = {"id": 10, "username": "collector.two", "role": "collector"}
        with self.assertRaises(PermissionError):
            drafts.get_email_draft(another, saved["id"])

    def test_submit_creates_approval_and_does_not_send(self):
        saved = self._save()
        with patch("secure_workflow_service.create_approval_request", return_value=123) as create_approval:
            submitted = drafts.submit_email_draft_for_approval(ACTOR, saved["id"])
        self.assertEqual(submitted["status"], "Pending Approval")
        self.assertEqual(submitted["approval_request_id"], 123)
        create_approval.assert_called_once()
        self.assertIn("does not authorize sending", create_approval.call_args.args[3])

    def test_duplicate_submission_does_not_create_second_approval(self):
        saved = self._save()
        with patch("secure_workflow_service.create_approval_request", return_value=123) as create_approval:
            drafts.submit_email_draft_for_approval(ACTOR, saved["id"])
            second = drafts.submit_email_draft_for_approval(ACTOR, saved["id"])
        self.assertEqual(second["approval_request_id"], 123)
        create_approval.assert_called_once()

    def test_only_creator_can_submit(self):
        saved = self._save()
        other = {"id": 11, "username": "collector.two", "role": "collector"}
        with patch("secure_workflow_service.create_approval_request", return_value=123):  # noqa: SIM117
            with self.assertRaises(PermissionError):
                drafts.submit_email_draft_for_approval(other, saved["id"])

    def test_list_filters_to_creator_for_collectors(self):
        self._save()
        other = {"id": 12, "username": "collector.two", "role": "collector"}
        with patch.object(drafts, "save_email_draft", wraps=drafts.save_email_draft):
            # Create a second draft under another identity.
            drafts.save_email_draft(other, "INV-TEST-200", "Other Customer", "other@example.com",
                "Payment reminder for invoice INV-TEST-200",
                "Hello Other Customer, invoice INV-TEST-200 remains outstanding. Please confirm the payment date.",
                "Friendly", "Request payment date", True)
        rows = drafts.list_email_drafts(ACTOR)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["created_by_username"], ACTOR["username"])

if __name__ == "__main__":
    unittest.main()