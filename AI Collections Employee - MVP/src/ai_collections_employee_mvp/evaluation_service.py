"""V3.2 deterministic evaluation suite for AI Collections Employee.

Runs offline regression cases against deterministic collection rules and invoice retrieval.
It does not call Gemini, create tasks, write approval requests, or execute external actions.
"""
from __future__ import annotations

from dataclasses import asdict  # noqa: F401
from typing import Any

import pandas as pd
from collection_agent_v3_5 import AgentWorkflowError, get_invoice_details
from collection_rules import decide_collection_action

CATEGORIES = [
    "Payment promised",
    "Payment completed",
    "Invoice dispute",
    "Missing invoice",
    "Request for supporting documents",
    "Payment date requested",
    "No clear response",
]

# Each case checks the deterministic policy output after a response category is known.
RULE_CASES = [
    {"case_id":"R01", "name":"Overdue, no dispute", "amount":100.0, "days_overdue":15, "dispute":"No", "category":None, "expected":"Reminder draft eligible", "reminder":True},
    {"case_id":"R02", "name":"Recorded dispute blocks reminder", "amount":100.0, "days_overdue":15, "dispute":"Disputed", "category":None, "expected":"Dispute review", "reminder":False},
    {"case_id":"R03", "name":"Response dispute blocks reminder", "amount":100.0, "days_overdue":15, "dispute":"No", "category":"Invoice dispute", "expected":"Dispute review", "reminder":False},
    {"case_id":"R04", "name":"Reported payment requires verification", "amount":100.0, "days_overdue":15, "dispute":"No", "category":"Payment completed", "expected":"Payment verification", "reminder":False},
    {"case_id":"R05", "name":"Missing invoice requires review", "amount":100.0, "days_overdue":15, "dispute":"No", "category":"Missing invoice", "expected":"Invoice resend review", "reminder":False},
    {"case_id":"R06", "name":"Supporting documents request", "amount":100.0, "days_overdue":15, "dispute":"No", "category":"Request for supporting documents", "expected":"Supporting documents", "reminder":False},
    {"case_id":"R07", "name":"Payment promise is tracked", "amount":100.0, "days_overdue":15, "dispute":"No", "category":"Payment promised", "expected":"Payment promise tracking", "reminder":False},
    {"case_id":"R08", "name":"Payment date question does not trigger reminder", "amount":100.0, "days_overdue":15, "dispute":"No", "category":"Payment date requested", "expected":"Reminder draft eligible", "reminder":True},
    {"case_id":"R09", "name":"No clear response follows invoice rules", "amount":100.0, "days_overdue":15, "dispute":"No", "category":"No clear response", "expected":"Reminder draft eligible", "reminder":True},
    {"case_id":"R10", "name":"Not overdue", "amount":100.0, "days_overdue":0, "dispute":"No", "category":None, "expected":"Monitor", "reminder":False},
    {"case_id":"R11", "name":"Future due date", "amount":100.0, "days_overdue":-5, "dispute":"No", "category":None, "expected":"Monitor", "reminder":False},
    {"case_id":"R12", "name":"Zero balance", "amount":0.0, "days_overdue":30, "dispute":"No", "category":None, "expected":"No collection required", "reminder":False},
    {"case_id":"R13", "name":"Zero balance takes precedence over reported payment", "amount":0.0, "days_overdue":30, "dispute":"No", "category":"Payment completed", "expected":"No collection required", "reminder":False},
    {"case_id":"R14", "name":"Dispute takes precedence over payment completed", "amount":100.0, "days_overdue":30, "dispute":"Disputed", "category":"Payment completed", "expected":"Dispute review", "reminder":False},
]


def run_evaluation() -> dict[str, Any]:
    """Run deterministic tests and return JSON/CSV-friendly details and summary."""
    results: list[dict[str, Any]] = []
    for case in RULE_CASES:
        try:
            decision = decide_collection_action(
                invoice_number=case["case_id"],
                outstanding_amount=case["amount"],
                days_overdue=case["days_overdue"],
                dispute_status=case["dispute"],
                customer_response_category=case["category"],
            )
            actual = decision.decision
            reminder = bool(decision.reminder_allowed)
            review = bool(decision.requires_human_review)
            passed = actual == case["expected"] and reminder == case["reminder"] and review is True
            results.append({
                "case_id": case["case_id"], "test_type":"business_rule",
                "test_name":case["name"], "expected_decision":case["expected"],
                "actual_decision":actual, "expected_reminder_allowed":case["reminder"],
                "actual_reminder_allowed":reminder, "human_review_required":review,
                "status":"PASS" if passed else "FAIL", "details":decision.reason,
            })
        except Exception as error:  # noqa: BLE001
            results.append({"case_id":case["case_id"],"test_type":"business_rule","test_name":case["name"],
                "expected_decision":case["expected"],"actual_decision":None,
                "expected_reminder_allowed":case["reminder"],"actual_reminder_allowed":None,
                "human_review_required":None,"status":"ERROR","details":f"{type(error).__name__}: {error}"})

    # Invoice retrieval contract tests; no external calls or database writes.
    dataset = pd.DataFrame([{
        "invoice_number":"EVAL-001", "customer_name":"Synthetic Customer", "customer_email":"test@example.invalid",
        "invoice_date":"2026-01-01", "due_date":"2026-01-15", "invoice_amount":250.0,
        "outstanding_amount":250.0, "days_overdue":20, "dispute_status":"No", "priority":"High"
    }])
    retrieval_tests = [
        ("I01", "Unique invoice is retrieved", lambda: get_invoice_details(dataset, "EVAL-001"), "ok"),
        ("I02", "Unknown invoice is rejected", lambda: get_invoice_details(dataset, "NOT-FOUND"), "not_found"),
        ("I03", "Duplicate invoice number is rejected", lambda: get_invoice_details(pd.concat([dataset, dataset]), "EVAL-001"), "duplicate"),
        ("I04", "Blank invoice number is rejected", lambda: get_invoice_details(dataset, "  "), "blank"),
    ]
    for case_id, name, fn, expected in retrieval_tests:
        try:
            value = fn()
            if expected == "ok":
                passed = value["invoice_number"] == "EVAL-001" and value["outstanding_amount"] == 250.0
                details = "Returned invoice identity and amount matched the synthetic fixture."
                actual = "ok"
            else:
                passed = False
                details = "Expected validation error, but retrieval returned successfully."
                actual = "unexpected_success"
        except AgentWorkflowError as error:
            actual = str(error)
            if expected == "not_found": passed = "not found" in actual.lower()
            elif expected == "duplicate": passed = "not unique" in actual.lower()
            elif expected == "blank": passed = "required" in actual.lower()
            else: passed = False
            details = actual
        except Exception as error:  # noqa: BLE001
            passed = False
            actual = f"{type(error).__name__}: {error}"
            details = actual
        results.append({"case_id":case_id,"test_type":"invoice_retrieval","test_name":name,
            "expected_decision":expected,"actual_decision":actual,"expected_reminder_allowed":None,
            "actual_reminder_allowed":None,"human_review_required":None,
            "status":"PASS" if passed else "FAIL","details":details})

    total = len(results)
    passed_count = sum(row["status"] == "PASS" for row in results)
    failed_count = sum(row["status"] == "FAIL" for row in results)
    error_count = sum(row["status"] == "ERROR" for row in results)
    return {
        "suite_version":"3.2",
        "suite_name":"AI Collections Employee — Deterministic Regression Suite",
        "run_scope":"Offline business-rule and invoice-retrieval checks; no Gemini calls, no database writes, no external actions.",
        "total":total, "passed":passed_count, "failed":failed_count, "errors":error_count,
        "pass_rate":round((passed_count / total * 100) if total else 0, 2),
        "overall_status":"PASS" if passed_count == total else "FAIL",
        "results":results,
    }
