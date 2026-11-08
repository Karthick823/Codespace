"""V3.2 unittest regression tests. Run: python -m unittest -v test_v3_2_regression.py"""
import unittest

import pandas as pd
from collection_agent_v3_5 import AgentWorkflowError, get_invoice_details
from collection_rules import decide_collection_action


class CollectionRuleRegressionTests(unittest.TestCase):
    def assertDecision(self, amount, overdue, dispute, category, expected, reminder):
        result = decide_collection_action(invoice_number="TEST-001", outstanding_amount=amount,
            days_overdue=overdue, dispute_status=dispute, customer_response_category=category)
        self.assertEqual(result.decision, expected)
        self.assertEqual(result.reminder_allowed, reminder)
        self.assertTrue(result.requires_human_review)

    def test_overdue_clean_invoice_allows_draft_only(self):
        self.assertDecision(100, 15, "No", None, "Reminder draft eligible", True)
    def test_recorded_dispute_blocks_reminder(self):
        self.assertDecision(100, 15, "Disputed", None, "Dispute review", False)
    def test_dispute_category_blocks_reminder(self):
        self.assertDecision(100, 15, "No", "Invoice dispute", "Dispute review", False)
    def test_reported_payment_requires_verification(self):
        self.assertDecision(100, 15, "No", "Payment completed", "Payment verification", False)
    def test_missing_invoice_routes_to_review(self):
        self.assertDecision(100, 15, "No", "Missing invoice", "Invoice resend review", False)
    def test_supporting_documents_routes_correctly(self):
        self.assertDecision(100, 15, "No", "Request for supporting documents", "Supporting documents", False)
    def test_payment_promise_is_not_payment_completion(self):
        self.assertDecision(100, 15, "No", "Payment promised", "Payment promise tracking", False)
    def test_not_overdue_is_monitored(self):
        self.assertDecision(100, 0, "No", None, "Monitor", False)
    def test_future_due_date_is_monitored(self):
        self.assertDecision(100, -5, "No", None, "Monitor", False)
    def test_zero_balance_requires_source_verification(self):
        self.assertDecision(0, 30, "No", None, "No collection required", False)
    def test_zero_balance_precedes_payment_category(self):
        self.assertDecision(0, 30, "No", "Payment completed", "No collection required", False)
    def test_dispute_precedes_payment_category(self):
        self.assertDecision(100, 30, "Disputed", "Payment completed", "Dispute review", False)
    def test_blank_invoice_rejected(self):
        with self.assertRaises(ValueError):
            decide_collection_action(invoice_number=" ", outstanding_amount=10, days_overdue=1)
    def test_negative_balance_rejected(self):
        with self.assertRaises(ValueError):
            decide_collection_action(invoice_number="TEST", outstanding_amount=-1, days_overdue=1)
    def test_non_numeric_inputs_rejected(self):
        with self.assertRaises(ValueError):
            decide_collection_action(invoice_number="TEST", outstanding_amount="bad", days_overdue=1)

class InvoiceRetrievalRegressionTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame([{"invoice_number":"EVAL-001", "customer_name":"Synthetic Customer",
            "customer_email":"test@example.invalid", "invoice_date":"2026-01-01", "due_date":"2026-01-15",
            "invoice_amount":250.0, "outstanding_amount":250.0, "days_overdue":20,
            "dispute_status":"No", "priority":"High"}])
    def test_unique_invoice_retrieved(self):
        result = get_invoice_details(self.df, "EVAL-001")
        self.assertEqual(result["invoice_number"], "EVAL-001")
        self.assertEqual(result["outstanding_amount"], 250.0)
    def test_unknown_invoice_rejected(self):
        with self.assertRaisesRegex(AgentWorkflowError, "not found"):
            get_invoice_details(self.df, "NOPE")
    def test_duplicate_invoice_rejected(self):
        with self.assertRaisesRegex(AgentWorkflowError, "not unique"):
            get_invoice_details(pd.concat([self.df, self.df]), "EVAL-001")
    def test_blank_invoice_rejected(self):
        with self.assertRaisesRegex(AgentWorkflowError, "required"):
            get_invoice_details(self.df, " ")

if __name__ == "__main__":
    unittest.main(verbosity=2)