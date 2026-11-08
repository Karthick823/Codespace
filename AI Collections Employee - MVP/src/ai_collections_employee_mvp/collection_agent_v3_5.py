"""V3.0 tool-using controller for the AI Collections Employee MVP.

The controller coordinates existing deterministic rules and persistence services.
The LLM is used only to classify a customer message. It does not choose financial
actions, approve work, send messages, or update accounting records.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from secure_workflow_service import create_approval_request, require_permission
from collection_rules import decide_collection_action
from follow_up_service import create_task


class AgentWorkflowError(RuntimeError):
    """Raised when the agent workflow cannot safely continue."""


def get_invoice_details(invoices, invoice_number: str) -> dict[str, Any]:
    """Tool 1: retrieve one invoice from the already validated dashboard dataframe."""
    invoice_number = str(invoice_number).strip()
    if not invoice_number:
        raise AgentWorkflowError("Invoice number is required.")
    matches = invoices.loc[invoices["invoice_number"].astype(str) == invoice_number]
    if matches.empty:
        raise AgentWorkflowError("Invoice was not found in the currently loaded dataset.")
    if len(matches) != 1:
        raise AgentWorkflowError("Invoice number is not unique in the current dataset.")
    row = matches.iloc[0]
    return {
        "invoice_number": str(row["invoice_number"]),
        "customer_name": str(row["customer_name"]),
        "customer_email": str(row.get("customer_email", "")),
        "invoice_date": str(row.get("invoice_date", "")),
        "due_date": str(row.get("due_date", "")),
        "invoice_amount": float(row["invoice_amount"]),
        "outstanding_amount": float(row["outstanding_amount"]),
        "days_overdue": int(row["days_overdue"]),
        "dispute_status": str(row.get("dispute_status", "")),
        "priority": str(row.get("priority", "")),
    }


def classify_customer_reply(message: str | None) -> dict[str, Any] | None:
    """Tool 2: classify a reply when supplied; never execute the recommendation."""
    if message is None or not str(message).strip():
        return None
    try:
        from response_classification_service import classify_customer_response
    except ImportError as error:
        raise AgentWorkflowError(
            "AI classification dependencies are not installed. Install the project requirements in the active virtual environment."
        ) from error
    result = classify_customer_response(str(message).strip())
    # Safety boundary: require human review regardless of provider output.
    result["requires_human_review"] = True
    return result


def evaluate_collection_rules(invoice: dict[str, Any], classification: dict[str, Any] | None) -> dict[str, Any]:
    """Tool 3: apply deterministic business rules; never delegate financial policy to an LLM."""
    category = classification.get("category") if classification else None
    decision = decide_collection_action(
        invoice_number=invoice["invoice_number"],
        outstanding_amount=invoice["outstanding_amount"],
        days_overdue=invoice["days_overdue"],
        dispute_status=invoice["dispute_status"],
        customer_response_category=category,
    )
    return {
        "invoice_number": decision.invoice_number,
        "decision": decision.decision,
        "recommended_action": decision.recommended_action,
        "reason": decision.reason,
        "requires_human_review": True,
        "reminder_allowed": bool(decision.reminder_allowed),
    }


def create_follow_up_task(invoice: dict[str, Any], decision: dict[str, Any], classification: dict[str, Any] | None, due_date: date | None = None) -> int:
    """Tool 4: persist a follow-up task in Awaiting approval state."""
    action_map = {
        "Dispute review": "Review invoice dispute",
        "Payment verification": "Verify reported payment",
        "Invoice resend review": "Resend invoice",
        "Supporting documents": "Provide supporting documents",
        "Payment promise tracking": "Review payment promise",
        "Reminder draft eligible": "Prepare payment reminder",
        "Monitor": "Contact customer",
        "No collection required": "Other",
    }
    task_type = action_map.get(decision["decision"], "Other")
    description = (
        f"V3.0 agent recommendation: {decision['decision']}. {decision['recommended_action']} "
        f"Reason: {decision['reason']}"
    )
    if classification:
        description += f" Response category: {classification['category']}; sentiment: {classification['sentiment']}."
    return create_task(
        invoice_number=invoice["invoice_number"],
        customer_name=invoice["customer_name"],
        task_type=task_type,
        description=description[:1000],
        due_date=due_date or date.today(),
        status="Awaiting approval",
        requires_human_review=True,
    )


def submit_human_approval(invoice: dict[str, Any], decision: dict[str, Any], task_id: int, requested_by: str, actor: dict) -> int:
    """Tool 5: create a pending approval record; does not execute the proposed action."""
    actor = require_permission(actor, "create_approval")
    requester = str(requested_by).strip()
    if requester != actor["username"]:
        raise AgentWorkflowError("Requester must match the authenticated user.")
    if not requester:
        raise AgentWorkflowError("Requester name/team is required to submit for approval.")
    action_map = {
        "Dispute review": "Review invoice dispute",
        "Payment verification": "Verify reported payment",
        "Invoice resend review": "Prepare invoice resend",
        "Supporting documents": "Prepare supporting documents",
        "Payment promise tracking": "Track payment promise",
        "Reminder draft eligible": "Prepare payment reminder",
        "Monitor": "Other",
        "No collection required": "Other",
    }
    rationale = (
        f"Created by V3.0 tool-using agent. Linked follow-up task ID: {task_id}. "
        f"Decision: {decision['decision']}. Reason: {decision['reason']} "
        f"Recommended action: {decision['recommended_action']} "
        f"Reminder permitted by deterministic rules: {decision['reminder_allowed']}. "
        "Human approval required. No email, payment, or accounting update was executed."
    )
    return create_approval_request(
        actor, invoice["invoice_number"], action_map.get(decision["decision"], "Other"), rationale, task_id=int(task_id)
    )


def run_collection_agent(
    invoices,
    invoice_number: str,
    customer_response: str | None = None,
    requested_by: str = "",
    follow_up_date: date | None = None,
    actor: dict | None = None,
) -> dict[str, Any]:
    """Run a bounded, auditable sequence of tools and return the persisted record IDs.

    This is a deterministic tool orchestration workflow, not an unconstrained autonomous
    planner. All steps are explicit, human review is mandatory, and external actions are disabled.
    """
    actor = require_permission(actor, "run_workflow")
    if str(requested_by).strip() != actor["username"]:
        raise AgentWorkflowError("Requester must match the authenticated user.")
    invoice = get_invoice_details(invoices, invoice_number)
    classification = classify_customer_reply(customer_response)
    decision = evaluate_collection_rules(invoice, classification)
    task_id = create_follow_up_task(invoice, decision, classification, follow_up_date)
    try:
        approval_id = submit_human_approval(invoice, decision, task_id, requested_by, actor)
    except Exception:
        # Do not silently pretend the complete workflow succeeded; task ID is surfaced to the caller.
        raise AgentWorkflowError(
            f"Follow-up task #{task_id} was created, but approval submission failed. "
            "Check the approval queue before retrying to avoid duplicate tasks."
        ) from None
    return {
        "agent_version": "3.0",
        "workflow_status": "Awaiting human approval",
        "invoice": invoice,
        "classification": classification,
        "decision": decision,
        "follow_up_task_id": task_id,
        "approval_request_id": approval_id,
        "external_action_executed": False,
    }
