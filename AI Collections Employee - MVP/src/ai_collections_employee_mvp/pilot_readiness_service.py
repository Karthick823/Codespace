"""
V3.3 controlled-pilot readiness helpers.

Uses synthetic labelled customer-response examples. Live classification tests call the configured
classifier only when explicitly requested. Pilot metric logging stores aggregate counts and
synthetic/reference IDs only; it never stores customer message text or API credentials.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable  # noqa: UP035

DB_PATH = Path(__file__).resolve().parent / "collection_workflow.db"
CATEGORIES = [
    "Payment promised", "Payment completed", "Invoice dispute", "Missing invoice",
    "Request for supporting documents", "Payment date requested", "No clear response",
]

# Synthetic benchmark only: do not replace these with real customer messages without authorization.
SYNTHETIC_CASES = [
    {"case_id":"CLS01","message":"We will pay invoice INV-100 by 30 October.","expected":"Payment promised"},
    {"case_id":"CLS02","message":"Our payment run is scheduled for Friday and we will settle this then.","expected":"Payment promised"},
    {"case_id":"CLS03","message":"We confirm payment will be made next week.","expected":"Payment promised"},
    {"case_id":"CLS04","message":"We paid this invoice yesterday; the bank reference is available.","expected":"Payment completed"},
    {"case_id":"CLS05","message":"The transfer has already been processed from our account.","expected":"Payment completed"},
    {"case_id":"CLS06","message":"This invoice was settled on 18 October.","expected":"Payment completed"},
    {"case_id":"CLS07","message":"We dispute the quantity billed on this invoice.","expected":"Invoice dispute"},
    {"case_id":"CLS08","message":"The invoice amount does not match our agreed purchase order.","expected":"Invoice dispute"},
    {"case_id":"CLS09","message":"We did not order these services and challenge this charge.","expected":"Invoice dispute"},
    {"case_id":"CLS10","message":"We have not received the invoice. Please resend it.","expected":"Missing invoice"},
    {"case_id":"CLS11","message":"Could you send the invoice copy again? It is missing from our mailbox.","expected":"Missing invoice"},
    {"case_id":"CLS12","message":"The invoice attachment did not come through. Please share it again.","expected":"Missing invoice"},
    {"case_id":"CLS13","message":"Please provide the signed delivery note and proof of delivery.","expected":"Request for supporting documents"},
    {"case_id":"CLS14","message":"Can you send the statement of account and purchase order copy?","expected":"Request for supporting documents"},
    {"case_id":"CLS15","message":"We need supporting documents before we can process this invoice.","expected":"Request for supporting documents"},
    {"case_id":"CLS16","message":"When is this invoice due for payment?","expected":"Payment date requested"},
    {"case_id":"CLS17","message":"Could you confirm the payment due date for this bill?","expected":"Payment date requested"},
    {"case_id":"CLS18","message":"Please advise the date by which payment is expected.","expected":"Payment date requested"},
    {"case_id":"CLS19","message":"Thanks, we have received your email.","expected":"No clear response"},
    {"case_id":"CLS20","message":"I will check internally and get back to you.","expected":"No clear response"},
    {"case_id":"CLS21","message":"Noted. We will review and respond later.","expected":"No clear response"},
]

READINESS_CHECKLIST = [
    ("synthetic_data_only", "Pilot uses synthetic or formally authorized data"),
    ("named_owner", "A business owner and pilot supervisor are assigned"),
    ("reviewer_access", "Reviewer identities and permissions are controlled"),
    ("human_approval", "Human approval is required before external action"),
    ("no_auto_send", "Automatic email sending and ERP updates remain disabled"),
    ("incident_process", "A process exists to report, investigate, and resolve errors"),
    ("backup_restore", "Database backup and restore have been tested"),
    ("retention_policy", "Data retention and deletion rules are documented"),
    ("success_metrics", "Pilot success measures and stop criteria are agreed"),
    ("user_training", "Pilot users understand limitations and escalation steps"),
]


def run_classification_benchmark(classifier: Callable[[str], dict] | None = None) -> dict[str, Any]:
    """Run the labelled synthetic benchmark. Pass the real classifier for an explicit live run."""
    if classifier is None:
        from response_classification_service import classify_customer_response
        classifier = classify_customer_response
    results = []
    for case in SYNTHETIC_CASES:
        try:
            output = classifier(case["message"])
            actual = output.get("category") if isinstance(output, dict) else None
            valid = actual in CATEGORIES
            passed = valid and actual == case["expected"]
            results.append({"case_id":case["case_id"], "expected_category":case["expected"],
                            "actual_category":actual, "valid_category":valid,
                            "status":"PASS" if passed else "FAIL",
                            "details":"Exact category match" if passed else "Category mismatch or invalid output"})
        except Exception as exc:  # noqa: BLE001
            results.append({"case_id":case["case_id"], "expected_category":case["expected"],
                            "actual_category":None, "valid_category":False, "status":"ERROR",
                            "details":f"{type(exc).__name__}: {exc}"})
    total = len(results)
    correct = sum(x["status"] == "PASS" for x in results)
    errors = sum(x["status"] == "ERROR" for x in results)
    by_category = {}
    for category in CATEGORIES:
        subset = [x for x in results if x["expected_category"] == category]
        by_category[category] = {"total":len(subset), "correct":sum(x["status"] == "PASS" for x in subset),
                                 "accuracy_pct":round(100*sum(x["status"] == "PASS" for x in subset)/len(subset), 2) if subset else 0}
    return {"suite_version":"3.3", "suite_name":"Synthetic customer-response classification benchmark",
            "mode":"Live classifier benchmark; sends synthetic text to the configured classifier.",
            "total":total, "correct":correct, "incorrect":total-correct-errors, "errors":errors,
            "accuracy_pct":round(100*correct/total,2) if total else 0,
            "results":results, "by_category":by_category,
            "warning":"Small synthetic benchmark; not a production accuracy guarantee. Review every mismatch before any pilot."}


def initialize_pilot_log(db_path: str | Path = DB_PATH) -> None:
    with sqlite3.connect(str(db_path), timeout=10) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS pilot_metric_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            pilot_label TEXT NOT NULL,
            sample_size INTEGER NOT NULL CHECK(sample_size >= 0),
            workflow_completed INTEGER NOT NULL CHECK(workflow_completed >= 0),
            workflow_failed INTEGER NOT NULL CHECK(workflow_failed >= 0),
            recommendations_accepted INTEGER NOT NULL CHECK(recommendations_accepted >= 0),
            recommendations_corrected INTEGER NOT NULL CHECK(recommendations_corrected >= 0),
            avg_review_minutes REAL,
            notes TEXT NOT NULL DEFAULT ''
        )""")
        conn.commit()


def record_pilot_metrics(*, pilot_label: str, sample_size: int, workflow_completed: int,
                         workflow_failed: int, recommendations_accepted: int,
                         recommendations_corrected: int, avg_review_minutes: float | None,
                         notes: str = "", db_path: str | Path = DB_PATH) -> int:
    """Store aggregate pilot metrics only. Do not put customer names, messages, or invoice data in notes."""
    vals = [sample_size, workflow_completed, workflow_failed, recommendations_accepted, recommendations_corrected]
    if any(not isinstance(v, int) or v < 0 for v in vals):
        raise ValueError("Count fields must be non-negative integers.")
    if workflow_completed + workflow_failed > sample_size:
        raise ValueError("Completed plus failed workflows cannot exceed sample size.")
    if recommendations_accepted + recommendations_corrected > workflow_completed:
        raise ValueError("Accepted plus corrected recommendations cannot exceed completed workflows.")
    if avg_review_minutes is not None and avg_review_minutes < 0:
        raise ValueError("Average review minutes cannot be negative.")
    label = pilot_label.strip()
    if not label or len(label) > 100:
        raise ValueError("Provide a pilot label between 1 and 100 characters.")
    if len(notes) > 500:
        raise ValueError("Notes must be 500 characters or fewer; do not include personal or customer data.")
    initialize_pilot_log(db_path)
    with sqlite3.connect(str(db_path), timeout=10) as conn:
        cur = conn.execute("""INSERT INTO pilot_metric_log
            (created_at,pilot_label,sample_size,workflow_completed,workflow_failed,
             recommendations_accepted,recommendations_corrected,avg_review_minutes,notes)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), label, sample_size, workflow_completed,  # noqa: UP017
             workflow_failed, recommendations_accepted, recommendations_corrected, avg_review_minutes, notes.strip()))
        conn.commit()
        return int(cur.lastrowid)


def list_pilot_metrics(db_path: str | Path = DB_PATH) -> list[dict[str, Any]]:
    initialize_pilot_log(db_path)
    with sqlite3.connect(str(db_path), timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM pilot_metric_log ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def calculate_pilot_kpis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sample = sum(int(r["sample_size"]) for r in rows)
    completed = sum(int(r["workflow_completed"]) for r in rows)
    failed = sum(int(r["workflow_failed"]) for r in rows)
    accepted = sum(int(r["recommendations_accepted"]) for r in rows)
    corrected = sum(int(r["recommendations_corrected"]) for r in rows)
    review_rows = [r for r in rows if r.get("avg_review_minutes") is not None]
    weighted_review = (sum(float(r["avg_review_minutes"])*int(r["sample_size"]) for r in review_rows) /
                       sum(int(r["sample_size"]) for r in review_rows)) if review_rows and sum(int(r["sample_size"]) for r in review_rows) else None
    return {"sample_size":sample, "completed":completed, "failed":failed,
            "completion_rate_pct":round(100*completed/sample,2) if sample else 0,
            "failure_rate_pct":round(100*failed/sample,2) if sample else 0,
            "accepted":accepted, "corrected":corrected,
            "correction_rate_pct":round(100*corrected/(accepted+corrected),2) if accepted+corrected else 0,
            "weighted_avg_review_minutes":round(weighted_review,2) if weighted_review is not None else None}