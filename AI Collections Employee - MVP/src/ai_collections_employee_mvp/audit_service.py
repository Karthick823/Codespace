"""V3.4 structured audit event service. Do not store secrets or customer message bodies here."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from database_config import DEFAULT_DB_PATH

DB_PATH = DEFAULT_DB_PATH

def _connect():
    conn=sqlite3.connect(str(DB_PATH), timeout=15); conn.row_factory=sqlite3.Row; return conn

def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017

def initialize_audit():
    with _connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
          actor TEXT NOT NULL, actor_role TEXT NOT NULL, event_type TEXT NOT NULL,
          resource_type TEXT NOT NULL, resource_id TEXT NOT NULL, details_json TEXT NOT NULL,
          request_id TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_occurred ON audit_events(occurred_at DESC)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor, occurred_at DESC)")

def log_event(actor, actor_role, event_type, resource_type, resource_id, details=None, request_id=None):
    initialize_audit()
    # Keep event metadata small and avoid storing passwords, tokens, or message bodies.
    safe = details if isinstance(details, dict) else {}
    forbidden = {"password", "api_key", "token", "customer_message", "message_body", "authorization"}
    safe = {str(k)[:80]: v for k,v in safe.items() if str(k).lower() not in forbidden}
    encoded=json.dumps(safe, ensure_ascii=False, default=str)[:4000]
    with _connect() as db:
        cur=db.execute("INSERT INTO audit_events(occurred_at,actor,actor_role,event_type,resource_type,resource_id,details_json,request_id) VALUES(?,?,?,?,?,?,?,?)",
          (_now(), str(actor)[:80], str(actor_role)[:40], str(event_type)[:100], str(resource_type)[:100], str(resource_id)[:200], encoded, str(request_id)[:100] if request_id else None))
        return cur.lastrowid

def list_events(limit=500):
    initialize_audit(); limit=max(1,min(int(limit),5000))
    with _connect() as db:
        rows=db.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        try: item["details_json"] = json.loads(item["details_json"])
        except Exception: pass  # noqa: BLE001, S110
        out.append(item)
    return out