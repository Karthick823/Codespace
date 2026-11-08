"""V3.7 draft-only email tools for AI Collections Employee.

This module can create, validate, persist, and submit email drafts for human
approval. It deliberately has no email-provider client and cannot send email.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "collection_workflow.db"

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
PLACEHOLDER_RE = re.compile(r"\[[^\]]*(?:insert|enter|placeholder|customer name|invoice number|amount|date)[^\]]*\]", re.I)  # noqa: FURB167

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017

def _connect():
    db = sqlite3.connect(str(DB_PATH), timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def _actor(actor: dict | None, permission: str = "create_approval") -> dict:
    from secure_workflow_service import require_permission
    return require_permission(actor, permission)

def initialize_email_drafts() -> None:
    with _connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS email_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_uuid TEXT NOT NULL UNIQUE,
            invoice_number TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            tone TEXT NOT NULL,
            recommended_action TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Draft',
            version INTEGER NOT NULL DEFAULT 1,
            created_by_user_id INTEGER,
            created_by_username TEXT NOT NULL,
            created_by_role TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            validation_json TEXT NOT NULL DEFAULT '{}',
            approval_request_id INTEGER,
            submitted_at TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_email_drafts_invoice ON email_drafts(invoice_number, created_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_email_drafts_status ON email_drafts(status, updated_at DESC)")

def validate_email_draft(invoice_number: str, customer_name: str, recipient_email: str,
                         subject: str, body: str, *, human_review_required: bool = True) -> dict[str, Any]:
    """Validate required fields and obvious placeholders; this does not verify mailbox ownership."""
    errors: list[str] = []
    warnings: list[str] = []
    invoice = str(invoice_number or "").strip()
    customer = str(customer_name or "").strip()
    recipient = str(recipient_email or "").strip()
    subj = str(subject or "").strip()
    content = str(body or "").strip()

    if not invoice: errors.append("Invoice number is required.")
    if not customer: errors.append("Customer name is required.")
    if not recipient or not EMAIL_RE.fullmatch(recipient):
        errors.append("Enter one syntactically valid recipient email address.")
    if ";" in recipient or "," in recipient:
        errors.append("Only one recipient is permitted in draft-only mode.")
    if not subj: errors.append("Email subject is required.")
    elif len(subj) > 200: errors.append("Email subject must be 200 characters or fewer.")
    if not content: errors.append("Email body is required.")
    elif len(content) > 8000: errors.append("Email body must be 8,000 characters or fewer.")
    if len(content) < 30: errors.append("Email body must contain at least 30 characters.")
    if PLACEHOLDER_RE.search(subj) or PLACEHOLDER_RE.search(content):
        errors.append("Remove unresolved placeholders before saving the draft.")
    if not human_review_required:
        errors.append("Human review must remain required for collection email drafts.")
    if invoice and invoice.lower() not in (subj + "\n" + content).lower():
        warnings.append("The invoice number is not mentioned in the subject or body; verify whether it should be included.")
    if not any(x in content.lower() for x in ("payment", "invoice", "balance", "amount", "due")):
        warnings.append("The body does not appear to mention the payment or invoice context.")
    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings,
            "checks": {"recipient_format": bool(recipient and EMAIL_RE.fullmatch(recipient)),
                       "single_recipient": bool(recipient and ";" not in recipient and "," not in recipient),
                       "subject_present": bool(subj), "body_present": bool(content),
                       "human_review_required": bool(human_review_required)}}

def save_email_draft(actor: dict, invoice_number: str, customer_name: str,
                     recipient_email: str, subject: str, body: str,
                     tone: str = "Professional", recommended_action: str = "",
                     human_review_required: bool = True) -> dict[str, Any]:
    actor = _actor(actor, "create_approval")
    if tone not in {"Professional", "Friendly", "Firm"}:
        raise ValueError("Unsupported email tone.")
    validation = validate_email_draft(invoice_number, customer_name, recipient_email, subject, body,
                                      human_review_required=human_review_required)
    if not validation["valid"]:
        raise ValueError("Draft validation failed: " + " ".join(validation["errors"]))
    invoice = str(invoice_number).strip()
    customer = str(customer_name).strip()
    recipient = str(recipient_email).strip()
    subj = str(subject).strip()
    content = str(body).strip()
    now = _now()
    draft_uuid = str(uuid.uuid4())
    initialize_email_drafts()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute("""INSERT INTO email_drafts
            (draft_uuid,invoice_number,customer_name,recipient_email,subject,body,tone,recommended_action,
             status,version,created_by_user_id,created_by_username,created_by_role,created_at,updated_at,validation_json)
             VALUES(?,?,?,?,?,?,?,?, 'Draft',1,?,?,?,?,?,?)""",
             (draft_uuid, invoice, customer, recipient, subj, content, tone,
              str(recommended_action or "")[:500], actor.get("id"), actor["username"], actor["role"],
              now, now, json_dumps(validation)))
        if cur.lastrowid is None:
            db.rollback()
            raise RuntimeError("Email draft ID was not generated.")
        draft_id = int(cur.lastrowid)
        db.commit()
    _audit(actor, "email_draft_saved", draft_id, invoice, validation)
    return get_email_draft(actor, draft_id)

def json_dumps(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

def _audit(actor, event_type, draft_id, invoice, validation=None, extra=None):
    from audit_service import log_event
    details = {"invoice_number": invoice, "status": event_type}
    if validation:
        details["validation_valid"] = bool(validation.get("valid"))
        details["validation_warnings"] = validation.get("warnings", [])
    if extra:
        details.update(extra)
    log_event(actor["username"], actor["role"], event_type, "email_draft", str(draft_id), details)

def get_email_draft(actor: dict, draft_id: int) -> dict[str, Any]:
    actor = _actor(actor, "view_approvals")
    initialize_email_drafts()
    with _connect() as db:
        row = db.execute("SELECT * FROM email_drafts WHERE id=?", (int(draft_id),)).fetchone()
    if row is None:
        raise ValueError("Email draft not found.")
    result = dict(row)
    if actor["role"] not in ("admin", "reviewer") and result["created_by_user_id"] is not None:  # noqa: SIM102
        if actor.get("id") is None or int(actor["id"]) != int(result["created_by_user_id"]):
            raise PermissionError("You can only view email drafts you created.")
    import json
    try: result["validation"] = json.loads(result.pop("validation_json"))
    except Exception: result["validation"] = {}  # noqa: BLE001
    return result

def list_email_drafts(actor: dict, status: str = "All", limit: int = 200) -> list[dict[str, Any]]:
    actor = _actor(actor, "view_approvals")
    initialize_email_drafts()
    allowed_statuses = {"All", "Draft", "Submitting", "Pending Approval", "Approved", "Rejected", "Cancelled"}
    if status not in allowed_statuses: raise ValueError("Unsupported draft status filter.")
    limit = max(1, min(int(limit), 1000))
    query = "SELECT * FROM email_drafts"
    params: list[Any] = []
    if status != "All":
        query += " WHERE status=?"
        params.append(status)
    if actor["role"] not in ("admin", "reviewer"):
        query += (" AND" if " WHERE " in query else " WHERE ") + " created_by_user_id=?"
        params.append(actor.get("id"))
    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with _connect() as db:
        rows = db.execute(query, params).fetchall()
    import json
    out=[]
    for row in rows:
        item=dict(row)
        try: item["validation"] = json.loads(item.pop("validation_json"))
        except Exception: item["validation"]={}  # noqa: BLE001
        out.append(item)
    return out

def submit_email_draft_for_approval(actor: dict, draft_id: int) -> dict[str, Any]:
    """Create a human approval request; never sends the email."""
    actor = _actor(actor, "create_approval")
    initialize_email_drafts()
    # Reserve the draft to prevent duplicate approval requests from concurrent submissions.
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM email_drafts WHERE id=?", (int(draft_id),)).fetchone()
        if row is None:
            db.rollback()
            raise ValueError("Email draft not found.")
        if row["status"] != "Draft":
            db.rollback()
            if row["approval_request_id"]:
                return get_email_draft(actor, draft_id)
            raise ValueError(f"Only saved drafts can be submitted. Current status: {row['status']}.")
        if row["created_by_user_id"] is not None and actor.get("id") is not None and int(row["created_by_user_id"]) != int(actor["id"]):
            db.rollback()
            raise PermissionError("Only the draft creator can submit it for approval.")
        validation = validate_email_draft(row["invoice_number"], row["customer_name"], row["recipient_email"],
                                           row["subject"], row["body"], human_review_required=True)
        if not validation["valid"]:
            db.rollback()
            raise ValueError("Draft no longer passes validation: " + " ".join(validation["errors"]))
        cur = db.execute("UPDATE email_drafts SET status='Submitting',updated_at=? WHERE id=? AND status='Draft'",
                         (_now(), int(draft_id)))
        if cur.rowcount != 1:
            db.rollback()
            raise ValueError("Draft is already being submitted. Refresh and try again.")
        draft = dict(row)
        db.commit()
    try:
        from secure_workflow_service import create_approval_request
        rationale = (f"Review saved collection email draft #{draft_id}. "
                     f"Recipient: {draft['recipient_email']}. Tone: {draft['tone']}. "
                     "Review the stored draft before deciding. This approval does not authorize sending.")
        approval = create_approval_request(
            actor, draft["invoice_number"], f"Review collection email draft #{draft_id}",
            rationale, workflow_id=None, task_id=None)
        approval_id = int(approval["id"] if isinstance(approval, dict) else approval)
        with _connect() as db:
            cur = db.execute("""UPDATE email_drafts SET status='Pending Approval',approval_request_id=?,
                                submitted_at=?,updated_at=? WHERE id=? AND status='Submitting'""",
                             (approval_id, _now(), _now(), int(draft_id)))
            if cur.rowcount != 1:
                raise RuntimeError("Approval was created but draft status could not be finalized; contact an administrator.")
        _audit(actor, "email_draft_submitted_for_approval", int(draft_id), draft["invoice_number"],
               validation, {"approval_request_id": approval_id})
        return get_email_draft(actor, int(draft_id))
    except Exception:
        with _connect() as db:
            db.execute("UPDATE email_drafts SET status='Draft',updated_at=? WHERE id=? AND status='Submitting'",
                       (_now(), int(draft_id)))
        raise