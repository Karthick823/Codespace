"""SQLite-backed follow-up tasks and collection activity history.

This module stores task metadata only. It does not send emails or update accounting systems.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Optional

from database_config import DEFAULT_DB_PATH

DB_PATH = DEFAULT_DB_PATH

TASK_STATUSES = ("Open", "Awaiting approval", "In progress", "Completed", "Cancelled")
TASK_TYPES = (
    "Prepare payment reminder",
    "Verify reported payment",
    "Review invoice dispute",
    "Resend invoice",
    "Provide supporting documents",
    "Review payment promise",
    "Contact customer",
    "Other",
)


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    """Create the local database tables if they do not already exist."""
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS follow_up_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                task_type TEXT NOT NULL,
                description TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                requires_human_review INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                invoice_number TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                notes TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(task_id) REFERENCES follow_up_tasks(id)
            )
            """
        )


def create_task(
    invoice_number: str,
    customer_name: str,
    task_type: str,
    description: str,
    due_date: date,
    status: str = "Open",
    requires_human_review: bool = True,
) -> int:
    """Create a follow-up task and an associated activity-history record."""
    invoice_number = str(invoice_number).strip()
    customer_name = str(customer_name).strip()
    description = str(description).strip()
    if not invoice_number or not customer_name:
        raise ValueError("Invoice number and customer name are required.")
    if task_type not in TASK_TYPES:
        raise ValueError("Select a supported task type.")
    if status not in TASK_STATUSES:
        raise ValueError("Select a supported task status.")
    if not description:
        raise ValueError("Task description cannot be empty.")
    if not isinstance(due_date, date):
        raise ValueError("A valid follow-up date is required.")  # noqa: TRY004

    now = datetime.now().isoformat(timespec="seconds")
    initialize_database()
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO follow_up_tasks
            (invoice_number, customer_name, task_type, description, due_date,
             status, requires_human_review, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_number, customer_name, task_type, description,
                due_date.isoformat(), status, int(bool(requires_human_review)),
                now, now,
            ),
        )
        task_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO collection_activity
            (task_id, invoice_number, activity_type, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, invoice_number, "Task created", description, now),
        )
        return task_id


def list_tasks(status: Optional[str] = None):
    """Return tasks newest first, optionally filtered by status."""
    initialize_database()
    query = "SELECT * FROM follow_up_tasks"
    params = ()
    if status and status != "All":
        if status not in TASK_STATUSES:
            raise ValueError("Unsupported task status filter.")
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY due_date ASC, id DESC"
    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def update_task_status(task_id: int, new_status: str, notes: str = "") -> None:
    """Update task status and record the change in activity history."""
    if new_status not in TASK_STATUSES:
        raise ValueError("Select a supported task status.")
    notes = str(notes).strip() or f"Task status changed to {new_status}."
    now = datetime.now().isoformat(timespec="seconds")
    initialize_database()
    with _connect() as connection:
        row = connection.execute(
            "SELECT invoice_number FROM follow_up_tasks WHERE id = ?", (int(task_id),)
        ).fetchone()
        if row is None:
            raise ValueError("Task not found.")
        connection.execute(
            "UPDATE follow_up_tasks SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, int(task_id)),
        )
        connection.execute(
            """
            INSERT INTO collection_activity
            (task_id, invoice_number, activity_type, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(task_id), row["invoice_number"], "Task status updated", notes, now),
        )


def list_activity(limit: int = 200):
    """Return recent collection activity records."""
    initialize_database()
    limit = max(1, min(int(limit), 1000))
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, task_id, invoice_number, activity_type, notes, created_at
            FROM collection_activity
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]