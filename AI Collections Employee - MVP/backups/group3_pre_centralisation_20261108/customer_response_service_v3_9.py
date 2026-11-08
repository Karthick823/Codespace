"""V3.9 persistent customer-response inbox with mandatory human disposition.

AI classification is advisory only. This module never sends email, changes
invoices, updates ERP records, or performs an action recommended by a model.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database_config import DEFAULT_DB_PATH

DB_PATH = DEFAULT_DB_PATH

CATEGORIES = (
    "Payment promised",
    "Payment completed",
    "Invoice dispute",
    "Missing invoice",
    "Request for supporting documents",
    "Payment date requested",
    "No clear response",
)

SENTIMENTS = (
    "Positive",
    "Neutral",
    "Negative",
    "Mixed",
    "Unclear",
)

DISPOSITIONS = (
    "Needs review",
    "Follow-up required",
    "Dispute escalation",
    "Payment to reconcile",
    "Documents to send",
    "Resolved",
    "Not actionable",
)

SOURCES = {
    "Manual entry",
    "Email import",
    "Other",
}

TERMINAL_DISPOSITIONS = {
    "Resolved",
    "Not actionable",
}

MAX_INVOICE_LENGTH = 120
MAX_CUSTOMER_LENGTH = 200
MAX_MESSAGE_LENGTH = 12000
MAX_RECOMMENDED_ACTION_LENGTH = 1000
MAX_REVIEWER_NOTES_LENGTH = 1500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DB_PATH), timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 20000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _authorize(
    actor: dict[str, Any] | None,
    permission: str,
) -> dict[str, Any]:
    from secure_workflow_service import require_permission

    return require_permission(actor, permission)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid integer.") from exc

    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer.")

    return parsed


def _parse_received_at(value: Any, fallback: str) -> str:
    if value in (None, ""):
        return fallback

    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Received timestamp must be a valid ISO-8601 timestamp."
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # noqa: UP017

    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017


def _validate_length(
    value: str,
    maximum: int,
    field_name: str,
) -> str:
    if len(value) > maximum:
        raise ValueError(
            f"{field_name} must be {maximum:,} characters or fewer."
        )

    return value


def _audit(
    actor: dict[str, Any],
    event: str,
    response_id: int | str,
    invoice: str,
    extra: dict[str, Any] | None = None,
) -> None:
    from audit_service import log_event

    details: dict[str, Any] = {
        "invoice_number": invoice,
    }

    if extra:
        # Prevent caller-supplied audit metadata from overriding the
        # authoritative invoice number.
        details.update(
            {key: value for key, value in extra.items() if key != "invoice_number"}
        )

    log_event(
        actor["username"],
        actor["role"],
        event,
        "customer_response",
        str(response_id),
        details,
    )


def initialize_response_inbox() -> None:
    

    with closing(_connect()) as db, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                response_uuid TEXT NOT NULL UNIQUE,
                invoice_number TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'Manual entry',
                received_at TEXT NOT NULL,
                message_text TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'No clear response',
                sentiment TEXT NOT NULL DEFAULT 'Unclear',
                recommended_action TEXT NOT NULL DEFAULT '',
                requires_human_review INTEGER NOT NULL DEFAULT 1,
                disposition TEXT NOT NULL DEFAULT 'Needs review',
                reviewer_notes TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                UNIQUE(invoice_number, response_uuid)
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_responses_status
            ON customer_responses(disposition, received_at DESC)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_customer_responses_invoice
            ON customer_responses(invoice_number, received_at DESC)
            """
        )


def save_customer_response(
    actor: dict[str, Any],
    *,
    invoice_number: Any,
    customer_name: Any,
    message_text: Any,
    source: str = "Manual entry",
    received_at: Any = None,
) -> dict[str, Any]:
    actor = _authorize(actor, "run_workflow")
    initialize_response_inbox()

    invoice = _validate_length(
        _clean_text(invoice_number),
        MAX_INVOICE_LENGTH,
        "Invoice number",
    )
    customer = _validate_length(
        _clean_text(customer_name),
        MAX_CUSTOMER_LENGTH,
        "Customer name",
    )
    message = _validate_length(
        _clean_text(message_text),
        MAX_MESSAGE_LENGTH,
        "Message",
    )

    if not invoice:
        raise ValueError(
            "Invoice number is required "
            f"(maximum {MAX_INVOICE_LENGTH} characters)."
        )

    if not customer:
        raise ValueError(
            "Customer name is required "
            f"(maximum {MAX_CUSTOMER_LENGTH} characters)."
        )

    if not message:
        raise ValueError(
            "Message must contain 1-"
            f"{MAX_MESSAGE_LENGTH:,} characters."
        )

    if source not in SOURCES:
        raise ValueError("Unsupported response source.")

    now = _now()
    received = _parse_received_at(received_at, now)
    response_uuid = str(uuid.uuid4())

    with closing(_connect()) as db, db:
        cursor = db.execute(
            """
            INSERT INTO customer_responses (
                response_uuid,
                invoice_number,
                customer_name,
                source,
                received_at,
                message_text,
                created_by,
                created_at,
                updated_by,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                response_uuid,
                invoice,
                customer,
                source,
                received,
                message,
                actor["username"],
                now,
                actor["username"],
                now,
            ),
        )

        response_id = int(cursor.lastrowid)

    _audit(
        actor,
        "customer_response_created",
        response_id,
        invoice,
        {"source": source},
    )

    return get_customer_response(actor, response_id)


def update_classification(
    actor: dict[str, Any],
    response_id: Any,
    *,
    expected_version: Any,
    category: str,
    sentiment: str,
    recommended_action: Any = "",
    disposition: str = "Needs review",
    reviewer_notes: Any = "",
) -> dict[str, Any]:
    actor = _authorize(actor, "run_workflow")
    initialize_response_inbox()

    response_id = _parse_positive_int(response_id, "Response ID")
    expected_version = _parse_positive_int(
        expected_version,
        "Expected version",
    )

    if category not in CATEGORIES:
        raise ValueError("Unsupported response category.")

    if sentiment not in SENTIMENTS:
        raise ValueError("Unsupported sentiment.")

    if disposition not in DISPOSITIONS:
        raise ValueError("Unsupported disposition.")

    action = _validate_length(
        _clean_text(recommended_action),
        MAX_RECOMMENDED_ACTION_LENGTH,
        "Recommended action",
    )
    notes = _validate_length(
        _clean_text(reviewer_notes),
        MAX_REVIEWER_NOTES_LENGTH,
        "Reviewer notes",
    )

    if disposition in TERMINAL_DISPOSITIONS and not notes:
        raise ValueError(
            f"Reviewer notes are required when disposition is '{disposition}'."
        )

    now = _now()

    with closing(_connect()) as db, db:
        row = db.execute(
            "SELECT invoice_number FROM customer_responses WHERE id = ?",
            (response_id,),
        ).fetchone()

        if row is None:
            raise ValueError("Customer response not found.")

        invoice_number = row["invoice_number"]

        cursor = db.execute(
            """
            UPDATE customer_responses
            SET
                category = ?,
                sentiment = ?,
                recommended_action = ?,
                disposition = ?,
                reviewer_notes = ?,
                updated_by = ?,
                updated_at = ?,
                version = version + 1
            WHERE id = ?
              AND version = ?
            """,
            (
                category,
                sentiment,
                action,
                disposition,
                notes,
                actor["username"],
                now,
                response_id,
                expected_version,
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "Response changed by another user. "
                "Refresh before editing."
            )

    _audit(
        actor,
        "customer_response_reviewed",
        response_id,
        invoice_number,
        {
            "category": category,
            "sentiment": sentiment,
            "disposition": disposition,
        },
    )

    return get_customer_response(actor, response_id)


def list_customer_responses(
    actor: dict[str, Any],
    disposition: str = "All",
    limit: Any = 500,
) -> list[dict[str, Any]]:
    _authorize(actor, "view_workflows")
    initialize_response_inbox()

    limit = _parse_positive_int(limit, "Limit")
    limit = min(limit, 1000)

    allowed = {"All", *DISPOSITIONS}

    if disposition not in allowed:
        raise ValueError("Unsupported disposition filter.")

    query = "SELECT * FROM customer_responses"
    params: tuple[Any, ...] = ()

    if disposition != "All":
        query += " WHERE disposition = ?"
        params = (disposition,)

    query += " ORDER BY received_at DESC, id DESC LIMIT ?"
    params = (*params, limit)

    with closing(_connect()) as db:
        rows = db.execute(query, params).fetchall()

    return [dict(row) for row in rows]


def get_customer_response(
    actor: dict[str, Any],
    response_id: Any,
) -> dict[str, Any]:
    _authorize(actor, "view_workflows")
    initialize_response_inbox()

    response_id = _parse_positive_int(response_id, "Response ID")

    with closing(_connect()) as db:
        row = db.execute(
            "SELECT * FROM customer_responses WHERE id = ?",
            (response_id,),
        ).fetchall()

    if not row:
        raise ValueError("Customer response not found.")

    return dict(row[0])