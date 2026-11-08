"""V3.6 reliability tests. Uses an isolated temporary SQLite database."""
import gc
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import workflow_reliability_service as rel


class V36ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        self.patch = patch.object(rel, "DB_PATH", self.db_path)
        self.patch.start()
        rel.initialize_reliability()
        # workflow_runs is the parent table required by transition_workflow
        import sqlite3
        with sqlite3.connect(self.db_path) as db:
            db.execute("""CREATE TABLE workflow_runs (
                workflow_id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            db.execute("INSERT INTO workflow_runs VALUES ('wf-1','Queued','now')")

    def tearDown(self):
        self.patch.stop()
        # Force garbage collection to release SQLite file handles on Windows before cleanup
        gc.collect()
        self.temp.cleanup()

    def test_same_idempotency_key_and_payload_returns_original(self):
        first, created = rel.resolve_idempotency_key("key-1", {"invoice": "INV-1"}, "wf-a")
        second, created2 = rel.resolve_idempotency_key("key-1", {"invoice": "INV-1"}, "wf-b")
        self.assertEqual(first, "wf-a")
        self.assertEqual(second, "wf-a")
        self.assertTrue(created)
        self.assertFalse(created2)

    def test_same_key_different_payload_rejected(self):
        rel.resolve_idempotency_key("key-1", {"invoice": "INV-1"}, "wf-a")
        with self.assertRaises(ValueError):
            rel.resolve_idempotency_key("key-1", {"invoice": "INV-2"}, "wf-b")

    def test_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            rel.resolve_idempotency_key("", {"invoice": "INV-1"}, "wf-a")

    def test_live_lease_blocks_second_execution(self):
        token = rel.acquire_execution_lease("wf-1", "collector")
        try:
            with self.assertRaises(RuntimeError):
                rel.acquire_execution_lease("wf-1", "collector-2")
        finally:
            self.assertTrue(rel.release_execution_lease("wf-1", token))

    def test_expired_lease_can_be_reclaimed(self):
        import sqlite3
        import time
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO workflow_execution_leases VALUES ('wf-1','old','collector','now',?)", (time.time() - 1,))
        token = rel.acquire_execution_lease("wf-1", "collector-2")
        self.assertTrue(rel.release_execution_lease("wf-1", token))

    def test_valid_transition(self):
        rel.transition_workflow("wf-1", "Queued", "Running", actor="collector")
        import sqlite3
        with sqlite3.connect(self.db_path) as db:
            status = db.execute("SELECT status FROM workflow_runs WHERE workflow_id='wf-1'").fetchone()[0]
        self.assertEqual(status, "Running")

    def test_invalid_transition_rejected(self):
        with self.assertRaises(ValueError):
            rel.transition_workflow("wf-1", "Queued", "Completed")

    def test_stale_transition_rejected(self):
        with self.assertRaises(RuntimeError):
            rel.transition_workflow("wf-1", "Running", "Completed")

    def test_reliability_event_is_persisted(self):
        rel.log_reliability_event("wf-1", "test_event", "collector", details={"attempt": 1})
        events = rel.list_reliability_events("wf-1")
        self.assertEqual(events[0]["event_type"], "test_event")
        self.assertEqual(events[0]["details"]["attempt"], 1)


if __name__ == "__main__":
    unittest.main()