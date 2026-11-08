"""V3.7 draft-only email tools for AI Collections Employee.

This module can create, validate, persist, and submit email drafts for human
approval. It deliberately has no email-provider client and cannot send email.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone  # noqa: F401
from pathlib import Path
from typing import Any, Iterator  # noqa: UP035

DB_PATH = Path(__file__).resolve().parent / "collection_workflow.db"

EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

PLACEHOLDER_RE = re.compile(
    r"\[[^\]]*(?:insert|enter|placeholder|customer name|invoice number|"
    r"amount|date)[^\]]*\]",
    re.IGNORECASE,
)

DRAFT_STATUSES = {
    "All",
    "Draft",
    "Submitting",
    "Pending Approval",
    "Approved",
    "Approval Stale",
    "Rejected",
    "Cancelled",
    "Sending",
    "Sent",
    "Delivery Unknown",
    "Send Failed",
}

EMAIL_TONES = {"Professional", "Friendly", "Firm"}

MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 8000
MIN_BODY_LENGTH = 30
MAX_RECOMMENDED_ACTION_LENGTH = 500
MAX_CHANGE_REASON_LENGTH = 250


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(str(DB_PATH), timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 20000")
    db.execute("PRAGMA foreign_keys = ON")

    try:
        yield db
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    finally:
        db.close()


def _actor(actor: dict[str, Any] | None, permission: str = "create_approval") -> dict[str, Any]:
    from secure_workflow_service import require_permission

    return require_permission(actor, permission)


def _parse_positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid integer.") from exc

    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer.")

    return parsed


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _audit(
    actor: dict[str, Any],
    event_type: str,
    draft_id: int | str,
    invoice: str,
    validation: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    from audit_service import log_event

    details: dict[str, Any] = {
        "invoice_number": invoice,
        "status": event_type,
    }

    if validation is not None:
        details["validation_valid"] = bool(validation.get("valid"))
        details["validation_warnings"] = validation.get("warnings", [])

    if extra:
        details.update(extra)

    log_event(
        actor["username"],
        actor["role"],
        event_type,
        "email_draft",
        str(draft_id),
        details,
    )


def initialize_email_drafts() -> None:
    """Create and migrate the email draft schema safely.

    This function can be called repeatedly and supports callers
    that configure different database paths during testing.
    """

    with _connect() as db, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS email_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_uuid TEXT NOT NULL UNIQUE,
                invoice_number TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                tone TEXT NOT NULL,
                recommended_action TEXT NOT NULL DEFAULT '',
                requires_human_review INTEGER NOT NULL DEFAULT 1,
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
            )
            """
        )

        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(email_drafts)")
        }

        additions = {
            "draft_uuid": "TEXT",
            "created_by_user_id": "INTEGER",
            "created_by_role": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "validation_json": "TEXT NOT NULL DEFAULT '{}'",
            "approval_request_id": "INTEGER",
            "submitted_at": "TEXT",
            "approved_version": "INTEGER",
            "send_status": "TEXT NOT NULL DEFAULT 'Not sent'",
            "provider_message_id": "TEXT",
            "last_send_attempt_id": "TEXT",
            "requires_human_review": "INTEGER NOT NULL DEFAULT 1",
        }

        for column, column_type in additions.items():
            if column not in columns:
                db.execute(
                    f"ALTER TABLE email_drafts ADD COLUMN {column} {column_type}"
                )

        # Backfill values for records created using the older schema.
        rows = db.execute(
            """
            SELECT id, created_at
            FROM email_drafts
            WHERE updated_at IS NULL OR updated_at = ''
            """
        ).fetchall()

        for row in rows:
            db.execute(
                "UPDATE email_drafts SET updated_at = ? WHERE id = ?",
                (row["created_at"] if hasattr(row, "keys") else row[1], row["id"] if hasattr(row, "keys") else row[0]),
            )

        # Assign UUIDs to older records that do not have one.
        rows = db.execute(
            """
            SELECT id
            FROM email_drafts
            WHERE draft_uuid IS NULL OR draft_uuid = ''
            """
        ).fetchall()

        for row in rows:
            draft_id = row["id"] if hasattr(row, "keys") else row[0]
            db.execute(
                "UPDATE email_drafts SET draft_uuid = ? WHERE id = ?",
                (str(uuid.uuid4()), draft_id),
            )

        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_email_drafts_draft_uuid
            ON email_drafts(draft_uuid)
            """
        )


def validate_email_draft(
    invoice_number: Any,
    customer_name: Any,
    recipient_email: Any,
    subject: Any,
    body: Any,
    *,
    human_review_required: bool = True,
) -> dict[str, Any]:
    """Validate required fields and obvious placeholders.

    This does not verify mailbox ownership or deliverability.
    """
    errors: list[str] = []
    warnings: list[str] = []

    invoice = _clean_text(invoice_number)
    customer = _clean_text(customer_name)
    recipient = _clean_text(recipient_email)
    subject_text = _clean_text(subject)
    content = _clean_text(body)

    recipient_is_valid = bool(recipient and EMAIL_RE.fullmatch(recipient))
    single_recipient = bool(
        recipient and ";" not in recipient and "," not in recipient
    )

    if not invoice:
        errors.append("Invoice number is required.")

    if not customer:
        errors.append("Customer name is required.")

    if not recipient_is_valid:
        errors.append("Enter one syntactically valid recipient email address.")

    if not single_recipient:
        errors.append("Only one recipient is permitted in draft-only mode.")

    if not subject_text:
        errors.append("Email subject is required.")
    elif len(subject_text) > MAX_SUBJECT_LENGTH:
        errors.append(
            f"Email subject must be {MAX_SUBJECT_LENGTH} characters or fewer."
        )

    if not content:
        errors.append("Email body is required.")
    elif len(content) > MAX_BODY_LENGTH:
        errors.append(
            f"Email body must be {MAX_BODY_LENGTH:,} characters or fewer."
        )
    elif len(content) < MIN_BODY_LENGTH:
        errors.append(
            f"Email body must contain at least {MIN_BODY_LENGTH} characters."
        )

    if PLACEHOLDER_RE.search(subject_text) or PLACEHOLDER_RE.search(content):
        errors.append(
            "Remove unresolved placeholders before saving the draft."
        )

    if not human_review_required:
        errors.append(
            "Human review must remain required for collection email drafts."
        )

    if invoice and invoice.lower() not in (
        subject_text + "\n" + content
    ).lower():
        warnings.append(
            "The invoice number is not mentioned in the subject or body; "
            "verify whether it should be included."
        )

    payment_terms = ("payment", "invoice", "balance", "amount", "due")

    if content and not any(term in content.lower() for term in payment_terms):
        warnings.append(
            "The body does not appear to mention the payment or invoice context."
        )

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "recipient_format": recipient_is_valid,
            "single_recipient": single_recipient,
            "subject_present": bool(subject_text),
            "body_present": bool(content),
            "human_review_required": bool(human_review_required),
        },
    }


def _content_hash(
    invoice_number: Any,
    customer_name: Any,
    recipient_email: Any,
    subject: Any,
    body: Any,
    tone: Any,
    recommended_action: Any,
) -> str:
    data = json.dumps(
        {
            "invoice_number": invoice_number,
            "customer_name": customer_name,
            "recipient_email": recipient_email,
            "subject": subject,
            "body": body,
            "tone": tone,
            "recommended_action": recommended_action,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _record_version(
    db: sqlite3.Connection,
    draft_id: int,
    version: int,
    invoice_number: str,
    customer_name: str,
    recipient_email: str,
    subject: str,
    body: str,
    tone: str,
    recommended_action: str,
    actor: dict[str, Any],
    reason: str,
) -> str:
    digest = _content_hash(
        invoice_number,
        customer_name,
        recipient_email,
        subject,
        body,
        tone,
        recommended_action,
    )

    db.execute(
        """
        INSERT INTO email_draft_versions (
            draft_id,
            version,
            invoice_number,
            customer_name,
            recipient_email,
            subject,
            body,
            tone,
            recommended_action,
            content_hash,
            changed_by_user_id,
            changed_by_username,
            changed_by_role,
            change_reason,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            version,
            invoice_number,
            customer_name,
            recipient_email,
            subject,
            body,
            tone,
            recommended_action,
            digest,
            actor.get("id"),
            actor["username"],
            actor["role"],
            reason,
            _now(),
        ),
    )

    return digest


def save_email_draft(
    actor: dict[str, Any],
    invoice_number: Any,
    customer_name: Any,
    recipient_email: Any,
    subject: Any,
    body: Any,
    tone: str = "Professional",
    recommended_action: Any = "",
    human_review_required: bool = True,
) -> dict[str, Any]:
    actor = _actor(actor, "create_approval")

    if tone not in EMAIL_TONES:
        raise ValueError("Unsupported email tone.")

    validation = validate_email_draft(
        invoice_number,
        customer_name,
        recipient_email,
        subject,
        body,
        human_review_required=human_review_required,
    )

    if not validation["valid"]:
        raise ValueError(
            "Draft validation failed: " + " ".join(validation["errors"])
        )

    invoice = _clean_text(invoice_number)
    customer = _clean_text(customer_name)
    recipient = _clean_text(recipient_email)
    subject_text = _clean_text(subject)
    content = _clean_text(body)
    action = _clean_text(recommended_action)[:MAX_RECOMMENDED_ACTION_LENGTH]

    now = _now()
    draft_uuid = str(uuid.uuid4())

    initialize_email_drafts()

    with _connect() as db, db:
        db.execute("BEGIN IMMEDIATE")

        cursor = db.execute(
            """
            INSERT INTO email_drafts (
                draft_uuid,
                invoice_number,
                customer_name,
                recipient_email,
                subject,
                body,
                tone,
                recommended_action,
                requires_human_review,
                status,
                version,
                created_by_user_id,
                created_by_username,
                created_by_role,
                created_at,
                updated_at,
                validation_json
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'Draft', 1, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                draft_uuid,
                invoice,
                customer,
                recipient,
                subject_text,
                content,
                tone,
                action,
                int(human_review_required),
                actor.get("id"),
                actor["username"],
                actor["role"],
                now,
                now,
                json_dumps(validation),
            ),
        )

        draft_id = int(cursor.lastrowid)

        _record_version(
            db,
            draft_id,
            1,
            invoice,
            customer,
            recipient,
            subject_text,
            content,
            tone,
            action,
            actor,
            "Initial draft",
        )

    _audit(
        actor,
        "email_draft_saved",
        draft_id,
        invoice,
        validation,
    )

    return get_email_draft(actor, draft_id)


def get_email_draft(actor: dict[str, Any], draft_id: Any) -> dict[str, Any]:
    actor = _actor(actor, "view_approvals")
    draft_id = _parse_positive_int(draft_id, "Draft ID")

    initialize_email_drafts()

    with _connect() as db:
        row = db.execute(
            "SELECT * FROM email_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()

    if row is None:
        raise ValueError("Email draft not found.")

    result = dict(row)

    if (
        actor["role"] not in {"admin", "reviewer"}
        and result["created_by_user_id"] is not None
    ):
        actor_id = actor.get("id")

        if actor_id is None or int(actor_id) != int(
            result["created_by_user_id"]
        ):
            raise PermissionError(
                "You can only view email drafts you created."
            )

    try:
        result["validation"] = json.loads(result.pop("validation_json"))
    except (TypeError, ValueError, json.JSONDecodeError):
        result["validation"] = {}

    return result


def list_email_drafts(
    actor: dict[str, Any],
    status: str = "All",
    limit: Any = 200,
) -> list[dict[str, Any]]:
    actor = _actor(actor, "view_approvals")
    initialize_email_drafts()

    if status not in DRAFT_STATUSES:
        raise ValueError("Unsupported draft status filter.")

    limit = _parse_positive_int(limit, "Limit")
    limit = min(limit, 1000)

    query = "SELECT * FROM email_drafts"
    params: list[Any] = []

    if status != "All":
        query += " WHERE status = ?"
        params.append(status)

    if actor["role"] not in {"admin", "reviewer"}:
        query += " AND" if " WHERE " in query else " WHERE"
        query += " created_by_user_id = ?"
        params.append(actor.get("id"))

    query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    params.append(limit)

    with _connect() as db:
        rows = db.execute(query, params).fetchall()

    results: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)

        try:
            item["validation"] = json.loads(item.pop("validation_json"))
        except (TypeError, ValueError, json.JSONDecodeError):
            item["validation"] = {}

        results.append(item)

    return results


def _find_linked_approval_id(
    draft_id: int,
    version: int,
    content_hash: str,
) -> int | None:
    draft_marker = f"[EMAIL_DRAFT_ID:{draft_id}]"
    version_marker = f"[EMAIL_DRAFT_VERSION:{version}]"
    hash_marker = f"[EMAIL_DRAFT_HASH:{content_hash}]"

    try:
        with _connect() as db:
            row = db.execute(
                """
                SELECT id
                FROM approval_requests
                WHERE rationale LIKE ?
                  AND rationale LIKE ?
                  AND rationale LIKE ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (f"%{draft_marker}%", f"%{version_marker}%", f"%{hash_marker}%"),
            ).fetchone()
    except sqlite3.Error:
        return None

    return int(row["id"]) if row else None


def submit_email_draft_for_approval(
    actor: dict[str, Any],
    draft_id: Any,
) -> dict[str, Any]:
    """Create a human approval request; never sends the email."""
    actor = _actor(actor, "create_approval")
    draft_id = _parse_positive_int(draft_id, "Draft ID")

    initialize_email_drafts()

    with _connect() as db, db:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            "SELECT * FROM email_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()

        if row is None:
            raise ValueError("Email draft not found.")

        if row["status"] != "Draft":
            if row["approval_request_id"]:
                draft_id_result = int(row["id"])
                raise _DraftAlreadySubmitted(draft_id_result)

            raise ValueError(
                "Only saved drafts can be submitted. "
                f"Current status: {row['status']}."
            )

        if (
            row["created_by_user_id"] is not None
            and actor.get("id") is not None
            and int(row["created_by_user_id"]) != int(actor["id"])
        ):
            raise PermissionError(
                "Only the draft creator can submit it for approval."
            )

        validation = validate_email_draft(
            row["invoice_number"],
            row["customer_name"],
            row["recipient_email"],
            row["subject"],
            row["body"],
            human_review_required=True,
        )

        if not validation["valid"]:
            raise ValueError(
                "Draft no longer passes validation: "
                + " ".join(validation["errors"])
            )

        cursor = db.execute(
            """
            UPDATE email_drafts
            SET status = 'Submitting', updated_at = ?
            WHERE id = ? AND status = 'Draft'
            """,
            (_now(), draft_id),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "Draft is already being submitted. Refresh and try again."
            )

        draft = dict(row)

    try:
        from secure_workflow_service import create_approval_request

        content_hash = _content_hash(
            draft["invoice_number"],
            draft["customer_name"],
            draft["recipient_email"],
            draft["subject"],
            draft["body"],
            draft["tone"],
            draft["recommended_action"],
        )

        rationale = (
            f"[EMAIL_DRAFT_ID:{draft_id}] "
            f"[EMAIL_DRAFT_VERSION:{int(draft['version'])}] "
            f"[EMAIL_DRAFT_HASH:{content_hash}] "
            f"Review saved collection email draft #{draft_id}. "
            f"Recipient: {draft['recipient_email']}. "
            f"Tone: {draft['tone']}. "
            "Review the stored draft before deciding. "
            "This approval does not authorize sending."
        )

        approval = create_approval_request(
            actor,
            draft["invoice_number"],
            f"Review collection email draft #{draft_id}",
            rationale,
            workflow_id=None,
            task_id=None,
        )

        approval_id = int(
            approval["id"] if isinstance(approval, dict) else approval
        )

        with _connect() as db, db:
            cursor = db.execute(
                """
                UPDATE email_drafts
                SET
                    status = 'Pending Approval',
                    approval_request_id = ?,
                    submitted_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'Submitting'
                """,
                (approval_id, _now(), _now(), draft_id),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "Approval was created but draft status could not be "
                    "finalized; contact an administrator."
                )

        _audit(
            actor,
            "email_draft_submitted_for_approval",
            draft_id,
            draft["invoice_number"],
            validation,
            {"approval_request_id": approval_id},
        )

        return get_email_draft(actor, draft_id)

    except _DraftAlreadySubmitted:
        raise
    except Exception:
        recovered_id = _find_linked_approval_id(
            draft_id,
            int(draft.get("version", 1)),
            _content_hash(
                draft["invoice_number"],
                draft["customer_name"],
                draft["recipient_email"],
                draft["subject"],
                draft["body"],
                draft["tone"],
                draft["recommended_action"],
            ),
        )

        if recovered_id:
            with _connect() as db, db:
                db.execute(
                    """
                    UPDATE email_drafts
                    SET
                        status = 'Pending Approval',
                        approval_request_id = ?,
                        submitted_at = COALESCE(submitted_at, ?),
                        updated_at = ?
                    WHERE id = ? AND status = 'Submitting'
                    """,
                    (recovered_id, _now(), _now(), draft_id),
                )

            _audit(
                actor,
                "email_draft_submission_recovered",
                draft_id,
                draft["invoice_number"],
                validation,
                {"approval_request_id": recovered_id},
            )

            return get_email_draft(actor, draft_id)

        with _connect() as db, db:
            db.execute(
                """
                UPDATE email_drafts
                SET status = 'Draft', updated_at = ?
                WHERE id = ? AND status = 'Submitting'
                """,
                (_now(), draft_id),
            )

        raise


class _DraftAlreadySubmitted(Exception):
    def __init__(self, draft_id: int) -> None:
        super().__init__("Draft has already been submitted for approval.")
        self.draft_id = draft_id


def edit_email_draft(
    actor: dict[str, Any],
    draft_id: Any,
    *,
    expected_version: Any,
    recipient_email: Any,
    subject: Any,
    body: Any,
    tone: str,
    change_reason: Any = "Edited draft",
) -> dict[str, Any]:
    """Optimistic-lock edit for Draft or Rejected drafts."""
    actor = _actor(actor, "create_approval")
    draft_id = _parse_positive_int(draft_id, "Draft ID")
    expected_version = _parse_positive_int(
        expected_version,
        "Expected version",
    )

    if tone not in EMAIL_TONES:
        raise ValueError("Unsupported email tone.")

    initialize_email_drafts()

    with _connect() as db, db:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            "SELECT * FROM email_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()

        if row is None:
            raise ValueError("Email draft not found.")

        if (
            row["created_by_user_id"] is not None
            and int(row["created_by_user_id"]) != int(actor.get("id") or -1)
        ):
            raise PermissionError(
                "Only the draft creator can edit this draft."
            )

        if row["status"] not in {"Draft", "Rejected"}:
            raise ValueError(
                "Only Draft or Rejected drafts can be edited. "
                "Submitted/approved drafts are immutable."
            )

        if int(row["version"]) != expected_version:
            raise RuntimeError(
                "Draft changed since you opened it. "
                "Reload the latest version before editing."
            )

        recipient = _clean_text(recipient_email)
        subject_text = _clean_text(subject)
        content = _clean_text(body)
        reason = _clean_text(change_reason)[:MAX_CHANGE_REASON_LENGTH]

        validation = validate_email_draft(
            row["invoice_number"],
            row["customer_name"],
            recipient,
            subject_text,
            content,
            human_review_required=True,
        )

        if not validation["valid"]:
            raise ValueError(
                "Draft validation failed: "
                + " ".join(validation["errors"])
            )

        new_version = int(row["version"]) + 1
        now = _now()

        cursor = db.execute(
            """
            UPDATE email_drafts
            SET
                recipient_email = ?,
                subject = ?,
                body = ?,
                tone = ?,
                version = ?,
                status = 'Draft',
                approval_request_id = NULL,
                submitted_at = NULL,
                approved_version = NULL,
                send_status = 'Not sent',
                validation_json = ?,
                updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                recipient,
                subject_text,
                content,
                tone,
                new_version,
                json_dumps(validation),
                now,
                draft_id,
                expected_version,
            ),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "Concurrent draft edit detected. Reload and try again."
            )

        _record_version(
            db,
            draft_id,
            new_version,
            row["invoice_number"],
            row["customer_name"],
            recipient,
            subject_text,
            content,
            tone,
            row["recommended_action"],
            actor,
            reason,
        )

    _audit(
        actor,
        "email_draft_version_created",
        draft_id,
        row["invoice_number"],
        validation,
        {
            "version": new_version,
            "change_reason": reason,
        },
    )

    return get_email_draft(actor, draft_id)


def list_email_draft_versions(
    actor: dict[str, Any],
    draft_id: Any,
) -> list[dict[str, Any]]:
    draft = get_email_draft(actor, draft_id)
    draft_id = _parse_positive_int(draft_id, "Draft ID")
    current_version = int(draft["version"])

    initialize_email_drafts()

    with _connect() as db:
        exists = db.execute(
            """
            SELECT 1
            FROM email_draft_versions
            WHERE draft_id = ? AND version = ?
            """,
            (draft_id, current_version),
        ).fetchone()

        if not exists:
            creator = {
                "id": draft.get("created_by_user_id"),
                "username": draft.get("created_by_username", "system"),
                "role": draft.get("created_by_role", "admin"),
            }

            with db:
                _record_version(
                    db,
                    draft_id,
                    current_version,
                    draft["invoice_number"],
                    draft["customer_name"],
                    draft["recipient_email"],
                    draft["subject"],
                    draft["body"],
                    draft["tone"],
                    draft.get("recommended_action", ""),
                    creator,
                    "Version history backfilled during migration",
                )

        rows = db.execute(
            """
            SELECT
                id,
                draft_id,
                version,
                invoice_number,
                customer_name,
                recipient_email,
                subject,
                body,
                tone,
                recommended_action,
                content_hash,
                changed_by_username,
                changed_by_role,
                change_reason,
                created_at
            FROM email_draft_versions
            WHERE draft_id = ?
            ORDER BY version DESC
            """,
            (draft_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def sync_email_draft_approval_status(
    actor: dict[str, Any],
    approval_request_id: Any | None = None,
) -> int:
    """Synchronize draft status from the authoritative approval record."""
    actor = _actor(actor, "view_approvals")

    if actor["role"] not in {"admin", "reviewer"}:
        raise PermissionError(
            "Only reviewers or administrators can synchronize approval status."
        )

    initialize_email_drafts()

    if approval_request_id is not None:
        approval_request_id = _parse_positive_int(
            approval_request_id,
            "Approval request ID",
        )

    status_map = {
        "Pending": "Pending Approval",
        "Approved": "Approved",
        "Rejected": "Rejected",
    }

    protected_statuses = {
        "Sent",
        "Sending",
        "Delivery Unknown",
        "Send Failed",
    }

    changed = 0

    with _connect() as db:
        if approval_request_id is None:
            rows = db.execute(
                """
                SELECT *
                FROM email_drafts
                WHERE approval_request_id IS NOT NULL
                  AND status IN (
                      'Pending Approval', 'Approved', 'Rejected'
                  )
                """
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT *
                FROM email_drafts
                WHERE approval_request_id = ?
                """,
                (approval_request_id,),
            ).fetchall()

        for draft in rows:
            approval = db.execute(
                """
                SELECT status, requester_user_id, rationale
                FROM approval_requests
                WHERE id = ?
                """,
                (int(draft["approval_request_id"]),),
            ).fetchone()

            if approval is None:
                continue

            if approval["status"] not in status_map:
                continue

            target = status_map[approval["status"]]

            if draft["status"] in protected_statuses:
                continue

            if target == "Approved":
                rationale = approval["rationale"] or ""

                version_match = re.search(
                    r"\[EMAIL_DRAFT_VERSION:(\d+)\]",
                    rationale,
                )
                hash_match = re.search(
                    r"\[EMAIL_DRAFT_HASH:([0-9a-f]{64})\]",
                    rationale,
                )

                if not version_match or not hash_match:
                    target = "Approval Stale"
                else:
                    approved_version = int(version_match.group(1))
                    approved_hash = hash_match.group(1)
                    current_hash = _content_hash(
                        draft["invoice_number"],
                        draft["customer_name"],
                        draft["recipient_email"],
                        draft["subject"],
                        draft["body"],
                        draft["tone"],
                        draft["recommended_action"],
                    )

                    snapshot = db.execute(
                        """
                        SELECT content_hash
                        FROM email_draft_versions
                        WHERE draft_id = ? AND version = ?
                        """,
                        (int(draft["id"]), int(draft["version"])),
                    ).fetchone()

                    is_current = (
                        approved_version == int(draft["version"])
                        and approved_hash == current_hash
                        and snapshot is not None
                        and snapshot["content_hash"] == current_hash
                    )

                    if not is_current:
                        target = "Approval Stale"

            if draft["status"] == target:
                continue

            if target == "Approved":
                db.execute(
                    """
                    UPDATE email_drafts
                    SET
                        status = ?,
                        approved_version = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        target,
                        approved_version,
                        _now(),
                        int(draft["id"]),
                    ),
                )
            else:
                db.execute(
                    """
                    UPDATE email_drafts
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (target, _now(), int(draft["id"])),
                )

            changed += 1

    if changed:
        _audit(
            actor,
            "email_draft_approval_status_synchronized",
            0,
            "multiple",
            extra={"changed_count": changed},
        )

    return changed


def recover_email_draft_submissions(actor: dict[str, Any]) -> dict[str, int]:
    """Reconcile stuck Submitting rows and synchronize approval states."""
    actor = _actor(actor, "view_approvals")

    if actor["role"] not in {"admin", "reviewer"}:
        raise PermissionError(
            "Only reviewers or administrators can run draft recovery."
        )

    initialize_email_drafts()

    recovered = 0
    reset = 0

    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM email_drafts WHERE status = 'Submitting'"
        ).fetchall()

    for row in rows:
        draft_id = int(row["id"])
        version = int(row["version"] or 1)

        content_hash = _content_hash(
            row["invoice_number"],
            row["customer_name"],
            row["recipient_email"],
            row["subject"],
            row["body"],
            row["tone"],
            row["recommended_action"],
        )

        linked = _find_linked_approval_id(
            draft_id,
            version,
            content_hash,
        )

        if linked:
            with _connect() as db, db:
                db.execute(
                    """
                    UPDATE email_drafts
                    SET
                        status = 'Pending Approval',
                        approval_request_id = ?,
                        submitted_at = COALESCE(submitted_at, ?),
                        updated_at = ?
                    WHERE id = ? AND status = 'Submitting'
                    """,
                    (linked, _now(), _now(), draft_id),
                )

            recovered += 1
            continue

        updated_at = _parse_timestamp(row["updated_at"])
        age = (
            (datetime.now(timezone.utc) - updated_at).total_seconds()  # noqa: UP017
            if updated_at is not None
            else 0
        )

        if age > 300:
            with _connect() as db, db:
                db.execute(
                    """
                    UPDATE email_drafts
                    SET status = 'Draft', updated_at = ?
                    WHERE id = ? AND status = 'Submitting'
                    """,
                    (_now(), draft_id),
                )

            reset += 1

    synced = sync_email_draft_approval_status(actor)

    _audit(
        actor,
        "email_draft_recovery_run",
        0,
        "multiple",
        extra={
            "recovered": recovered,
            "reset": reset,
            "synced": synced,
        },
    )

    return {
        "recovered": recovered,
        "reset": reset,
        "synced": synced,
    }