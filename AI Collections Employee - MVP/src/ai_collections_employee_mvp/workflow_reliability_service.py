"""V3.6 reliability primitives for the AI Collections Employee.

SQLite-backed idempotency, execution leases, validated workflow state changes,
and reliability-event logging. No external actions are executed here.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "collection_workflow.db"

ALLOWED_TRANSITIONS = {
    "Queued": {"Running", "Failed", "Cancelled"},
    "Running": {"Completed", "Failed", "Queued"},
    "Failed": {"Running", "Cancelled"},
    "Completed": set(),
    "Cancelled": set(),
}

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _connect():
    db = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def initialize_reliability() -> None:
    with _connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS workflow_idempotency (
            idempotency_key TEXT PRIMARY KEY,
            request_fingerprint TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS workflow_execution_leases (
            workflow_id TEXT PRIMARY KEY,
            lease_token TEXT NOT NULL,
            owner TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at_epoch REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS workflow_reliability_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            workflow_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT,
            request_id TEXT,
            details_json TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_reliability_events_workflow ON workflow_reliability_events(workflow_id, id DESC)")

def request_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def resolve_idempotency_key(key: str, payload: dict[str, Any], workflow_id: str) -> tuple[str, bool]:
    """Return (canonical workflow id, created_new_mapping).

    Reusing a key for an identical request returns the original workflow ID.
    Reusing it for a different request raises ValueError.
    """
    initialize_reliability()
    key = str(key or "").strip()
    if not key:
        raise ValueError("An idempotency key is required.")
    if len(key) > 200:
        raise ValueError("Idempotency key must be 200 characters or fewer.")
    fingerprint = request_fingerprint(payload)
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT request_fingerprint, workflow_id FROM workflow_idempotency WHERE idempotency_key=?", (key,)).fetchone()
        if row:
            if row["request_fingerprint"] != fingerprint:
                db.rollback()
                raise ValueError("This idempotency key was already used for a different request.")
            db.commit()
            return str(row["workflow_id"]), False
        db.execute("INSERT INTO workflow_idempotency(idempotency_key, request_fingerprint, workflow_id, created_at) VALUES(?,?,?,?)",
                   (key, fingerprint, workflow_id, _now()))
        db.commit()
    return workflow_id, True

def acquire_execution_lease(workflow_id: str, owner: str, lease_seconds: int = 900) -> str:
    """Acquire a lease; reject overlapping execution while a live lease exists."""
    initialize_reliability()
    token = str(uuid.uuid4())
    now = time.time()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT lease_token, expires_at_epoch FROM workflow_execution_leases WHERE workflow_id=?", (workflow_id,)).fetchone()
        if row and float(row["expires_at_epoch"]) > now:
            db.rollback()
            raise RuntimeError("This workflow is already being processed. Wait for it to finish or for its lease to expire.")
        db.execute("""INSERT INTO workflow_execution_leases(workflow_id, lease_token, owner, acquired_at, expires_at_epoch)
                      VALUES(?,?,?,?,?) ON CONFLICT(workflow_id) DO UPDATE SET
                      lease_token=excluded.lease_token, owner=excluded.owner,
                      acquired_at=excluded.acquired_at, expires_at_epoch=excluded.expires_at_epoch""",
                   (workflow_id, token, str(owner)[:80], _now(), now + max(30, int(lease_seconds))))
        db.commit()
    return token

def release_execution_lease(workflow_id: str, lease_token: str) -> bool:
    initialize_reliability()
    with _connect() as db:
        cur = db.execute("DELETE FROM workflow_execution_leases WHERE workflow_id=? AND lease_token=?", (workflow_id, lease_token))
        return cur.rowcount == 1

def log_reliability_event(workflow_id: str, event_type: str, actor: str | None = None,
                          request_id: str | None = None, details: dict | None = None) -> int:
    initialize_reliability()
    safe = details if isinstance(details, dict) else {}
    forbidden = {"password", "api_key", "token", "customer_message", "message_body", "authorization"}
    safe = {str(k)[:80]: v for k, v in safe.items() if str(k).lower() not in forbidden}
    encoded = json.dumps(safe, ensure_ascii=False, default=str)[:4000]
    with _connect() as db:
        cur = db.execute("""INSERT INTO workflow_reliability_events
            (occurred_at, workflow_id, event_type, actor, request_id, details_json)
            VALUES(?,?,?,?,?,?)""",
            (_now(), str(workflow_id)[:200], str(event_type)[:100], str(actor or "")[:80],
             str(request_id or "")[:100], encoded))
        return int(cur.lastrowid)

def list_reliability_events(workflow_id: str, limit: int = 200) -> list[dict]:
    initialize_reliability()
    limit = max(1, min(int(limit), 1000))
    with _connect() as db:
        rows = db.execute("SELECT * FROM workflow_reliability_events WHERE workflow_id=? ORDER BY id DESC LIMIT ?",
                          (workflow_id, limit)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json"))
        except Exception:
            item["details"] = {}
        out.append(item)
    return out

def validate_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid workflow state transition: {current} -> {target}")

def transition_workflow(workflow_id: str, current: str, target: str, actor: str | None = None,
                        request_id: str | None = None) -> None:
    validate_transition(current, target)
    initialize_reliability()
    # Compare-and-set transition protects against stale concurrent updates.
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            cur = db.execute("UPDATE workflow_runs SET status=?, updated_at=? WHERE workflow_id=? AND status=?",
                             (target, _now(), workflow_id, current))
            if cur.rowcount != 1:
                db.rollback()
                raise RuntimeError("Workflow state changed concurrently; reload the workflow before retrying.")
            db.commit()
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
    log_reliability_event(workflow_id, "state_transition", actor, request_id,
                          {"from": current, "to": target})

@contextmanager
def execution_lease(workflow_id: str, owner: str, lease_seconds: int = 900):
    token = acquire_execution_lease(workflow_id, owner, lease_seconds)
    try:
        yield token
    finally:
        release_execution_lease(workflow_id, token)
