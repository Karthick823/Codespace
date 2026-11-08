"""Isolated V3.5 service-layer authorization tests using a temporary SQLite database."""
import gc
import tempfile
import unittest
from pathlib import Path


class V35Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        import approval_service
        import audit_service
        import secure_workflow_service as svc
        
        cls.svc = svc
        cls.old_svc = svc.DB_PATH
        cls.old_approval = approval_service.DB_PATH
        cls.old_audit = audit_service.DB_PATH
        
        test_path = Path(cls.tmp.name) / "test.db"
        svc.DB_PATH = test_path
        approval_service.DB_PATH = test_path
        audit_service.DB_PATH = test_path
        
        svc.initialize_secure_workflows()
        
        cls.admin = {"id": 1, "username": "admin1", "role": "admin"}
        cls.collector = {"id": 2, "username": "collector1", "role": "collector"}
        cls.reviewer = {"id": 3, "username": "reviewer1", "role": "reviewer"}

    @classmethod
    def tearDownClass(cls):
        import approval_service
        import audit_service
        
        cls.svc.DB_PATH = cls.old_svc
        approval_service.DB_PATH = cls.old_approval
        audit_service.DB_PATH = cls.old_audit
        
        # Force garbage collection to release SQLite file handles on Windows before cleanup
        gc.collect()
        cls.tmp.cleanup()

    def test_collector_can_create_request(self):
        rid = self.svc.create_approval_request(
            self.collector, "INV-1", "Review dispute", "Test rationale"
        )
        self.assertGreater(rid, 0)

    def test_collector_cannot_decide(self):
        rid = self.svc.create_approval_request(
            self.collector, "INV-2", "Review dispute", "Test rationale"
        )
        with self.assertRaises(PermissionError):
            self.svc.decide_approval_request(self.collector, rid, "Approved")

    def test_requester_cannot_self_approve(self):
        rid = self.svc.create_approval_request(
            self.collector, "INV-3", "Review dispute", "Test rationale"
        )
        # The collector role is denied first; a role change with the same user ID also cannot self-approve.
        same_user_reviewer = {
            "id": self.collector["id"],
            "username": "collector1",
            "role": "reviewer",
        }
        with self.assertRaises(PermissionError):
            self.svc.decide_approval_request(same_user_reviewer, rid, "Approved")

    def test_reviewer_can_decide_other_users_request(self):
        rid = self.svc.create_approval_request(
            self.collector, "INV-4", "Review dispute", "Test rationale"
        )
        self.assertTrue(
            self.svc.decide_approval_request(
                self.reviewer, rid, "Rejected", "Need documents"
            )
        )

    def test_viewer_cannot_create_request(self):
        viewer = {"id": 4, "username": "viewer1", "role": "viewer"}
        with self.assertRaises(PermissionError):
            self.svc.create_approval_request(
                viewer, "INV-5", "Other", "Test"
            )

    def test_unauthenticated_actor_rejected(self):
        with self.assertRaises(PermissionError):
            self.svc.create_approval_request(
                None, "INV-6", "Other", "Test"
            )

    def test_workflow_link_persists(self):
        self.svc.register_workflow_link(
            self.collector, "wf-1", "INV-7", task_id=8, approval_id=9
        )
        links = self.svc.list_workflow_links(self.collector)
        self.assertTrue(
            any(x["workflow_id"] == "wf-1" and x["task_id"] == 8 for x in links)
        )

    def test_double_decision_rejected(self):
        rid = self.svc.create_approval_request(
            self.collector, "INV-8", "Other", "Test"
        )
        self.svc.decide_approval_request(self.reviewer, rid, "Approved")
        with self.assertRaises(ValueError):
            self.svc.decide_approval_request(self.admin, rid, "Rejected")


if __name__ == "__main__":
    unittest.main(verbosity=2)