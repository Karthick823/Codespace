"""Tests for V3.7.1 draft lifecycle and V3.8 controlled email sending.
All provider calls are mocked; no real email is sent by this suite.
"""
import gc
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import email_draft_service_v3_8 as drafts
import email_provider_service_v3_8 as provider

COLLECTOR = {"id": 7, "username": "collector.one", "display_name": "Collector One", "role": "collector"}
REVIEWER = {"id": 8, "username": "reviewer.one", "display_name": "Reviewer One", "role": "reviewer"}
VIEWER = {"id": 9, "username": "viewer.one", "display_name": "Viewer One", "role": "viewer"}

@contextmanager
def open_db(path):
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

class V38ControlledEmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.db"
        self.dbpatch = patch.object(drafts, "DB_PATH", self.db)
        self.dbpatch.start()
        self.auditpatch = patch.object(drafts, "_audit")
        self.auditpatch.start()
        self.providerauditpatch = patch.object(provider, "_audit")
        self.providerauditpatch.start()
        
        # Explicitly create all required tables directly in the isolated test database
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS email_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL DEFAULT 1,
                invoice_number TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                tone TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                requires_human_review INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'Draft',
                approval_request_id INTEGER,
                approved_version INTEGER,
                send_status TEXT,
                last_send_attempt_id TEXT,
                created_by_username TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS email_draft_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                invoice_number TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                tone TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                requires_human_review INTEGER NOT NULL,
                created_by_username TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS email_send_attempts (
                attempt_id TEXT PRIMARY KEY,
                draft_id INTEGER NOT NULL,
                draft_version INTEGER NOT NULL,
                approval_request_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                provider_message_id TEXT,
                error_message TEXT
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY, status TEXT NOT NULL, rationale TEXT NOT NULL,
                requester_user_id INTEGER, reviewer_user_id INTEGER
            )""")

    def tearDown(self):
        self.providerauditpatch.stop()
        self.auditpatch.stop()
        self.dbpatch.stop()
        # Force garbage collection to release SQLite file handles on Windows before cleanup
        gc.collect()
        self.temp.cleanup()

    def save(self):
        return drafts.save_email_draft(COLLECTOR, "INV-TEST-38", "Test Customer", "ap@example.com",
            "Payment reminder for invoice INV-TEST-38",
            "Hello Test Customer, invoice INV-TEST-38 remains outstanding. Please confirm the expected payment date. Thank you.",
            "Professional", "Request payment date", True)

    def add_approval(self, draft, status="Approved"):
        digest = drafts._content_hash(draft["invoice_number"], draft["customer_name"], draft["recipient_email"], draft["subject"], draft["body"], draft["tone"], draft["recommended_action"])
        with open_db(self.db) as conn:
            cur = conn.execute("INSERT INTO approval_requests(status,rationale,requester_user_id,reviewer_user_id) VALUES(?,?,?,?)",
                (status, f"[EMAIL_DRAFT_ID:{draft['id']}] [EMAIL_DRAFT_VERSION:{draft['version']}] [EMAIL_DRAFT_HASH:{digest}] Review email draft", COLLECTOR["id"], REVIEWER["id"]))
            approval_id = cur.lastrowid
            conn.execute("UPDATE email_drafts SET approval_request_id=?,status='Pending Approval' WHERE id=?", (approval_id, draft["id"]))
        return approval_id

    def test_edit_creates_immutable_new_version(self):
        draft = self.save()
        updated = drafts.edit_email_draft(COLLECTOR, draft["id"], expected_version=1,
            recipient_email="ap@example.com", subject="Updated reminder INV-TEST-38",
            body="Hello, invoice INV-TEST-38 remains outstanding. Please confirm your payment date. Thank you.",
            tone="Friendly", change_reason="Corrected tone")
        self.assertEqual(updated["version"], 2)
        versions = drafts.list_email_draft_versions(COLLECTOR, draft["id"])
        self.assertEqual({x["version"] for x in versions}, {1, 2})

    def test_stale_version_edit_rejected(self):
        draft = self.save()
        drafts.edit_email_draft(COLLECTOR, draft["id"], expected_version=1,
            recipient_email="ap@example.com", subject="Updated reminder INV-TEST-38",
            body="Hello, invoice INV-TEST-38 remains outstanding. Please confirm your payment date. Thank you.",
            tone="Professional")
        with self.assertRaises(RuntimeError):
            drafts.edit_email_draft(COLLECTOR, draft["id"], expected_version=1,
                recipient_email="ap@example.com", subject="Another reminder INV-TEST-38",
                body="Hello, invoice INV-TEST-38 remains outstanding. Please confirm your payment date. Thank you.", tone="Professional")

    def test_submitted_draft_cannot_be_edited(self):
        draft = self.save()
        with open_db(self.db) as conn:
            conn.execute("UPDATE email_drafts SET status='Pending Approval' WHERE id=?", (draft["id"],))
        with self.assertRaises(ValueError):
            drafts.edit_email_draft(COLLECTOR, draft["id"], expected_version=1,
                recipient_email="ap@example.com", subject="Updated reminder INV-TEST-38",
                body="Hello, invoice INV-TEST-38 remains outstanding. Please confirm your payment date. Thank you.", tone="Professional")

    def test_approval_status_sync_updates_to_approved_and_version(self):
        draft = self.save()
        approval_id = self.add_approval(draft, "Approved")
        changed = drafts.sync_email_draft_approval_status(REVIEWER, approval_id)
        loaded = drafts.get_email_draft(REVIEWER, draft["id"])
        self.assertEqual(changed, 1)
        self.assertEqual(loaded["status"], "Approved")
        self.assertEqual(loaded["approved_version"], 1)

    def test_approval_rejection_syncs(self):
        draft = self.save()
        approval_id = self.add_approval(draft, "Rejected")
        drafts.sync_email_draft_approval_status(REVIEWER, approval_id)
        self.assertEqual(drafts.get_email_draft(REVIEWER, draft["id"])["status"], "Rejected")

    def test_live_send_disabled_by_default(self):
        self.assertFalse(provider.sending_enabled())
        draft = self.save()
        with self.assertRaises(PermissionError):
            provider.send_approved_email(REVIEWER, draft["id"], expected_version=1, confirm_recipient="ap@example.com")

    def test_viewer_cannot_send(self):
        draft = self.save()
        env = {"EMAIL_SENDING_ENABLED": "true", "MS_GRAPH_TENANT_ID": "t", "MS_GRAPH_CLIENT_ID": "c", "MS_GRAPH_CLIENT_SECRET": "s", "MS_GRAPH_SENDER_UPN": "sender@example.com"}
        with patch.dict(os.environ, env):  # noqa: SIM117
            with self.assertRaises(PermissionError):
                provider.send_approved_email(VIEWER, draft["id"], expected_version=1, confirm_recipient="ap@example.com")

    def test_approved_version_sends_once_with_mock_provider(self):
        draft = self.save()
        approval_id = self.add_approval(draft, "Approved")
        drafts.sync_email_draft_approval_status(REVIEWER, approval_id)
        env = {"EMAIL_SENDING_ENABLED": "true", "MS_GRAPH_TENANT_ID": "t", "MS_GRAPH_CLIENT_ID": "c", "MS_GRAPH_CLIENT_SECRET": "s", "MS_GRAPH_SENDER_UPN": "sender@example.com"}
        with patch.dict(os.environ, env), patch.object(provider, "_graph_send", return_value={"status": "Sent", "provider_message_id": "graph-req-1"}) as send:
            result = provider.send_approved_email(REVIEWER, draft["id"], expected_version=1, confirm_recipient="ap@example.com")
            self.assertEqual(result["status"], "Sent")
            send.assert_called_once()
            with self.assertRaises(ValueError):
                provider.send_approved_email(REVIEWER, draft["id"], expected_version=1, confirm_recipient="ap@example.com")

    def test_send_requires_exact_recipient_confirmation(self):
        draft = self.save()
        approval_id = self.add_approval(draft, "Approved")
        drafts.sync_email_draft_approval_status(REVIEWER, approval_id)
        env = {"EMAIL_SENDING_ENABLED": "true", "MS_GRAPH_TENANT_ID": "t", "MS_GRAPH_CLIENT_ID": "c", "MS_GRAPH_CLIENT_SECRET": "s", "MS_GRAPH_SENDER_UPN": "sender@example.com"}
        with patch.dict(os.environ, env):  # noqa: SIM117
            with self.assertRaises(ValueError):
                provider.send_approved_email(REVIEWER, draft["id"], expected_version=1, confirm_recipient="wrong@example.com")

    def test_stale_sending_attempt_recovers_to_delivery_unknown(self):
        draft = self.save()
        with open_db(self.db) as conn:
            conn.execute("INSERT INTO email_send_attempts(attempt_id,draft_id,draft_version,approval_request_id,provider,recipient_email,status,started_at) VALUES(?,?,?,?,?,?,?,datetime('now','-20 minutes'))",
                ("attempt-stale", draft["id"], 1, 123, "microsoft_graph", "ap@example.com", "Sending"))
            conn.execute("UPDATE email_drafts SET status='Sending',send_status='Sending',last_send_attempt_id='attempt-stale' WHERE id=?", (draft["id"],))
        with patch.object(provider, "_audit"):
            changed = provider.recover_stale_send_attempts(REVIEWER, older_than_seconds=300)
        self.assertEqual(changed, 1)
        with open_db(self.db) as conn:
            attempt_status = conn.execute("SELECT status FROM email_send_attempts WHERE attempt_id='attempt-stale'").fetchone()[0]
            draft_status = conn.execute("SELECT status FROM email_drafts WHERE id=?", (draft["id"],)).fetchone()[0]
        self.assertEqual(attempt_status, "Delivery Unknown")
        self.assertEqual(draft_status, "Delivery Unknown")

    def test_approval_version_mismatch_blocks_send(self):
        draft = self.save()
        approval_id = self.add_approval(draft, "Approved")  # noqa: F841
        with open_db(self.db) as conn:
            conn.execute("UPDATE email_drafts SET version=2,approved_version=1,status='Approved' WHERE id=?", (draft["id"],))
        env = {"EMAIL_SENDING_ENABLED": "true", "MS_GRAPH_TENANT_ID": "t", "MS_GRAPH_CLIENT_ID": "c", "MS_GRAPH_CLIENT_SECRET": "s", "MS_GRAPH_SENDER_UPN": "sender@example.com"}
        with patch.dict(os.environ, env):  # noqa: SIM117
            with self.assertRaises(ValueError):
                provider.send_approved_email(REVIEWER, draft["id"], expected_version=2, confirm_recipient="ap@example.com")

if __name__ == "__main__":
    unittest.main()