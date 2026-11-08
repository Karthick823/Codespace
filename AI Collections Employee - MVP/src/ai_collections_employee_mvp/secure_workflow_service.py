"""V3.5 service-layer authorization for approvals and workflow access."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from approval_service import initialize as initialize_approvals
from approval_service import list_requests as _list_requests
from audit_service import log_event

DB_PATH = Path(__file__).resolve().parent / "collection_workflow.db"

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "view_approvals",
        "create_approval",
        "decide_approval",
        "run_workflow",
        "view_workflows",
    },
    "reviewer": {
        "view_approvals",
        "decide_approval",
        "run_workflow",
        "view_workflows",
    },
    "collector": {
        "view_approvals",
        "create_approval",
        "run_workflow",
        "view_workflows",
    },
    "viewer": {
        "view_approvals",
        "view_workflows",
    },
}

_SCHEMA_INITIALIZED = False


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DB_PATH), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017


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


def require_permission(actor: Any, permission: str) -> dict[str, Any]:
    if not isinstance(actor, dict):
        raise PermissionError("An authenticated user is required.")

    username = actor.get("username")
    role = actor.get("role")

    if not username or role not in ROLE_PERMISSIONS:
        raise PermissionError("An authenticated user is required.")

    if permission not in ROLE_PERMISSIONS[role]:
        raise PermissionError(
            f"Role '{role}' is not permitted to perform this action."
        )

    return actor


def initialize_secure_workflows() -> None:
    global _SCHEMA_INITIALIZED

    if _SCHEMA_INITIALIZED:
        return

    initialize_approvals()

    with closing(_connect()) as db, db:
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(approval_requests)")
        }

        additions = {
            "workflow_id": "TEXT",
            "task_id": "INTEGER",
            "requester_user_id": "INTEGER",
            "requester_role": "TEXT",
            "reviewer_user_id": "INTEGER",
            "reviewer_role": "TEXT",
            "decision_request_id": "TEXT",
            "version": "INTEGER NOT NULL DEFAULT 0",
        }

        for column, column_type in additions.items():
            if column not in columns:
                db.execute(
                    f"ALTER TABLE approval_requests ADD COLUMN {column} {column_type}"
                )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_approval_workflow_id
            ON approval_requests(workflow_id)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_approval_task_id
            ON approval_requests(task_id)
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS secure_workflow_links (
                workflow_id TEXT PRIMARY KEY,
                invoice_number TEXT NOT NULL,
                task_id INTEGER,
                approval_id INTEGER,
                created_by_user_id INTEGER,
                created_by_username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    _SCHEMA_INITIALIZED = True


def create_approval_request(
    actor: dict[str, Any],
    invoice_number: Any,
    proposed_action: Any,
    rationale: Any,
    workflow_id: Any = None,
    task_id: Any = None,
) -> int:
    actor = require_permission(actor, "create_approval")

    invoice = _clean_text(invoice_number)
    action = _clean_text(proposed_action)
    reason = _clean_text(rationale)
    workflow_id = _clean_text(workflow_id) or None

    if not invoice or not action or not reason:
        raise ValueError(
            "Invoice, proposed action, and rationale are required."
        )

    if len(reason) > 3000:
        raise ValueError("Rationale must be 3,000 characters or fewer.")

    if task_id is not None:
        task_id = _parse_positive_int(task_id, "Task ID")

    initialize_secure_workflows()

    request_token = str(uuid.uuid4())
    now = _now()

    with closing(_connect()) as db, db:
        cursor = db.execute(
            """
            INSERT INTO approval_requests (
                invoice_number,
                proposed_action,
                rationale,
                requested_by,
                status,
                created_at,
                execution_status,
                workflow_id,
                task_id,
                requester_user_id,
                requester_role,
                decision_request_id
            )
            VALUES (?, ?, ?, ?, 'Pending', ?, 'Not executed', ?, ?, ?, ?, ?)
            """,
            (
                invoice,
                action,
                reason,
                actor["username"],
                now,
                workflow_id,
                task_id,
                actor.get("id"),
                actor["role"],
                request_token,
            ),
        )

        approval_id = int(cursor.lastrowid)

        if workflow_id:
            db.execute(
                """
                INSERT INTO secure_workflow_links (
                    workflow_id,
                    invoice_number,
                    task_id,
                    approval_id,
                    created_by_user_id,
                    created_by_username,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id) DO UPDATE SET
                    task_id = excluded.task_id,
                    approval_id = excluded.approval_id,
                    updated_at = excluded.updated_at
                """,
                (
                    workflow_id,
                    invoice,
                    task_id,
                    approval_id,
                    actor.get("id"),
                    actor["username"],
                    now,
                    now,
                ),
            )

    log_event(
        actor["username"],
        actor["role"],
        "approval_request_created",
        "approval_request",
        str(approval_id),
        {
            "invoice_number": invoice,
            "action": action,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "request_id": request_token,
        },
    )

    return approval_id


def list_authorized_approvals(
    actor: dict[str, Any],
    status: str = "All",
) -> list[dict[str, Any]]:
    actor = require_permission(actor, "view_approvals")

    rows = _list_requests(status)

    if actor["role"] in {"admin", "reviewer", "collector"}:
        return [dict(row) for row in rows]

    redacted_rows: list[dict[str, Any]] = []

    for row in rows:
        redacted_row = dict(row)
        redacted_row.pop("rationale", None)
        redacted_row.pop("reviewer_comments", None)
        redacted_rows.append(redacted_row)

    return redacted_rows


def decide_approval_request(
    actor: dict[str, Any],
    request_id: Any,
    decision: str,
    comments: Any = "",
) -> bool:
    actor = require_permission(actor, "decide_approval")

    request_id = _parse_positive_int(request_id, "Request ID")

    if decision not in {"Approved", "Rejected"}:
        raise ValueError("Decision must be Approved or Rejected.")

    comments = _clean_text(comments)

    if len(comments) > 1500:
        raise ValueError("Review comments must be 1,500 characters or fewer.")

    initialize_secure_workflows()

    with closing(_connect()) as db, db:
        row = db.execute(
            "SELECT * FROM approval_requests WHERE id = ?",
            (request_id,),
        ).fetchone()

        if row is None:
            raise ValueError("Approval request not found.")

        if row["status"] != "Pending":
            raise ValueError("Only pending requests can be decided.")

        requester_user_id = row["requester_user_id"]
        actor_id = actor.get("id")

        is_same_user = (
            requester_user_id is not None
            and actor_id is not None
            and int(requester_user_id) == int(actor_id)
        ) or (
            row["requested_by"]
            and row["requested_by"] == actor.get("username")
        )

        if is_same_user:
            raise PermissionError(
                "Separation of duties: the requester cannot decide their own request."
            )

        current_version = int(row["version"] or 0)

        cursor = db.execute(
            """
            UPDATE approval_requests
            SET
                status = ?,
                reviewer = ?,
                reviewer_comments = ?,
                decided_at = ?,
                reviewer_user_id = ?,
                reviewer_role = ?,
                version = version + 1
            WHERE id = ?
              AND status = 'Pending'
              AND version = ?
            """,
            (
                decision,
                actor["username"],
                comments,
                _now(),
                actor.get("id"),
                actor["role"],
                request_id,
                current_version,
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                "The request was changed by another reviewer. Refresh and try again."
            )

    log_event(
        actor["username"],
        actor["role"],
        "approval_decision",
        "approval_request",
        str(request_id),
        {
            "decision": decision,
            "requester": row["requested_by"],
            "workflow_id": row["workflow_id"],
            "task_id": row["task_id"],
        },
    )

    return True


def register_workflow_link(
    actor: dict[str, Any],
    workflow_id: Any,
    invoice_number: Any,
    task_id: Any = None,
    approval_id: Any = None,
) -> None:
    actor = require_permission(actor, "run_workflow")

    workflow_id = _clean_text(workflow_id)
    invoice_number = _clean_text(invoice_number)

    if not workflow_id or not invoice_number:
        raise ValueError("Workflow ID and invoice number are required.")

    if task_id is not None:
        task_id = _parse_positive_int(task_id, "Task ID")

    if approval_id is not None:
        approval_id = _parse_positive_int(approval_id, "Approval ID")

    initialize_secure_workflows()
    now = _now()

    with closing(_connect()) as db, db:
        db.execute(
            """
            INSERT INTO secure_workflow_links (
                workflow_id,
                invoice_number,
                task_id,
                approval_id,
                created_by_user_id,
                created_by_username,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_id) DO UPDATE SET
                task_id = COALESCE(
                    excluded.task_id,
                    secure_workflow_links.task_id
                ),
                approval_id = COALESCE(
                    excluded.approval_id,
                    secure_workflow_links.approval_id
                ),
                updated_at = excluded.updated_at
            """,
            (
                workflow_id,
                invoice_number,
                task_id,
                approval_id,
                actor.get("id"),
                actor["username"],
                now,
                now,
            ),
        )

    log_event(
        actor["username"],
        actor["role"],
        "workflow_link_updated",
        "workflow",
        workflow_id,
        {
            "invoice_number": invoice_number,
            "task_id": task_id,
            "approval_id": approval_id,
        },
    )


def list_workflow_links(
    actor: dict[str, Any],
    limit: Any = 200,
) -> list[dict[str, Any]]:
    require_permission(actor, "view_workflows")

    limit = _parse_positive_int(limit, "Limit")
    limit = min(limit, 1000)

    with closing(_connect()) as db:
        rows = db.execute(
            """
            SELECT *
            FROM secure_workflow_links
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]