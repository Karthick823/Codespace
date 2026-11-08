"""V3.4 approval request storage and workflow service."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from database_config import DEFAULT_DB_PATH

DB_PATH = DEFAULT_DB_PATH

STATUSES = ("Pending", "Approved", "Rejected")

MAX_INVOICE_LENGTH = 120
MAX_ACTION_LENGTH = 200
MAX_RATIONALE_LENGTH = 3000
MAX_REVIEWER_LENGTH = 80
MAX_COMMENTS_LENGTH = 1500

_INITIALIZED = False


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DB_PATH), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_request_id(value: Any) -> int:
    try:
        request_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Request ID must be a valid integer.") from exc

    if request_id < 1:
        raise ValueError("Request ID must be a positive integer.")

    return request_id


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


def initialize() -> None:
    global _INITIALIZED

    if _INITIALIZED:
        return

    with closing(connect()) as db, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                proposed_action TEXT NOT NULL,
                rationale TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Pending',
                reviewer TEXT,
                reviewer_comments TEXT,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                execution_status TEXT NOT NULL DEFAULT 'Not executed',
                version INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_approval_requests_status
            ON approval_requests(status, created_at DESC, id DESC)
            """
        )

    _INITIALIZED = True


def create_request(
    invoice_number: Any,
    proposed_action: Any,
    rationale: Any,
    requested_by: Any,
) -> int:
    initialize()

    invoice_number = _validate_length(
        _clean_text(invoice_number),
        MAX_INVOICE_LENGTH,
        "Invoice number",
    )
    proposed_action = _validate_length(
        _clean_text(proposed_action),
        MAX_ACTION_LENGTH,
        "Proposed action",
    )
    rationale = _validate_length(
        _clean_text(rationale),
        MAX_RATIONALE_LENGTH,
        "Rationale",
    )
    requested_by = _validate_length(
        _clean_text(requested_by),
        MAX_REVIEWER_LENGTH,
        "Requester",
    )

    if not all(
        (invoice_number, proposed_action, rationale, requested_by)
    ):
        raise ValueError(
            "Invoice, action, rationale, and requester are required."
        )

    with closing(connect()) as db, db:
        cursor = db.execute(
            """
            INSERT INTO approval_requests (
                invoice_number,
                proposed_action,
                rationale,
                requested_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                invoice_number,
                proposed_action,
                rationale,
                requested_by,
                _now(),
            ),
        )

        request_id = cursor.lastrowid

    if request_id is None:
        raise RuntimeError("Approval request creation failed.")

    return int(request_id)


def list_requests(status: str = "All") -> list[dict[str, Any]]:
    initialize()

    query = "SELECT * FROM approval_requests"
    arguments: tuple[Any, ...] = ()

    if status != "All":
        if status not in STATUSES:
            raise ValueError("Unsupported approval status.")

        query += " WHERE status = ?"
        arguments = (status,)

    query += " ORDER BY created_at DESC, id DESC"

    with closing(connect()) as db:
        rows = db.execute(query, arguments).fetchall()

    return [dict(row) for row in rows]


def decide_request(
    request_id: Any,
    decision: str,
    reviewer: Any,
    comments: Any = "",
) -> bool:
    initialize()

    request_id = _parse_request_id(request_id)

    if decision not in STATUSES[1:]:
        raise ValueError("Decision must be Approved or Rejected.")

    reviewer = _validate_length(
        _clean_text(reviewer),
        MAX_REVIEWER_LENGTH,
        "Reviewer",
    )
    comments = _validate_length(
        _clean_text(comments),
        MAX_COMMENTS_LENGTH,
        "Review comments",
    )

    if not reviewer:
        raise ValueError("Reviewer is required.")

    with closing(connect()) as db, db:
        row = db.execute(
            """
            SELECT status, version
            FROM approval_requests
            WHERE id = ?
            """,
            (request_id,),
        ).fetchone()

        if row is None:
            raise ValueError("Approval request not found.")

        if row["status"] != "Pending":
            raise ValueError("Only pending requests can be decided.")

        current_version = int(row["version"] or 0)

        cursor = db.execute(
            """
            UPDATE approval_requests
            SET
                status = ?,
                reviewer = ?,
                reviewer_comments = ?,
                decided_at = ?,
                version = version + 1
            WHERE id = ?
              AND status = 'Pending'
              AND version = ?
            """,
            (
                decision,
                reviewer,
                comments,
                _now(),
                request_id,
                current_version,
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "The request was changed by another reviewer. "
                "Refresh and try again."
            )

    return True