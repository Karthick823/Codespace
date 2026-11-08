"""Deterministic, explainable collection decision rules. No emails are sent."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CollectionDecision:
    invoice_number: str
    decision: str
    recommended_action: str
    reason: str
    requires_human_review: bool = True
    reminder_allowed: bool = False

def _has_dispute(value):
    text = str(value or "").strip().lower()
    return text not in {"", "none", "no", "false", "nan", "not disputed", "n/a"}

def decide_collection_action(*, invoice_number: str, outstanding_amount: float,
                             days_overdue: int, dispute_status: object = "",
                             customer_response_category: Optional[str] = None) -> CollectionDecision:
    invoice = str(invoice_number).strip()
    if not invoice:
        raise ValueError("Invoice number is required.")
    try:
        amount = float(outstanding_amount)
        overdue = int(days_overdue)
    except (TypeError, ValueError):
        raise ValueError("Outstanding amount and days overdue must be numeric.") from None
    if amount < 0:
        raise ValueError("Outstanding amount cannot be negative.")

    category = (customer_response_category or "").strip()
    if amount == 0:
        return CollectionDecision(invoice, "No collection required",
            "Verify the source invoice record before closing the collection task.",
            "The recorded outstanding amount is zero; confirm against the accounting source.", True, False)
    if _has_dispute(dispute_status) or category == "Invoice dispute":
        return CollectionDecision(invoice, "Dispute review",
            "Pause standard payment reminders and route the invoice for dispute review.",
            "An unresolved dispute is recorded or the customer has challenged the invoice.", True, False)
    if category == "Payment completed":
        return CollectionDecision(invoice, "Payment verification",
            "Verify receipt against the accounting or bank record before changing invoice status.",
            "A customer statement alone does not prove settlement.", True, False)
    if category == "Missing invoice":
        return CollectionDecision(invoice, "Invoice resend review",
            "Verify the invoice and recipient, then prepare a resend for approval.",
            "The customer reports that the invoice was not received.", True, False)
    if category == "Request for supporting documents":
        return CollectionDecision(invoice, "Supporting documents",
            "Identify the requested documents and prepare a response for approval.",
            "The customer requests supporting evidence or documentation.", True, False)
    if category == "Payment promised":
        return CollectionDecision(invoice, "Payment promise tracking",
            "Record an explicitly stated promised date and schedule payment verification.",
            "A payment promise should be tracked; do not assume payment occurred.", True, False)
    if overdue <= 0:
        return CollectionDecision(invoice, "Monitor",
            "Monitor the invoice and schedule review around its due date.",
            "The invoice has a positive balance but is not overdue.", True, False)
    return CollectionDecision(invoice, "Reminder draft eligible",
        "Prepare a payment reminder draft for human review.",
        "The invoice is overdue, has an outstanding balance, and no dispute is indicated.", True, True)
