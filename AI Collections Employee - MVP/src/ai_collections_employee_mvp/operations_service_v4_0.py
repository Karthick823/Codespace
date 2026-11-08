"""V4.0 read-only operational metrics and release-gate reporting."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
DB_PATH=Path(__file__).resolve().parent/"collection_workflow.db"

def _connect():
 c=sqlite3.connect(str(DB_PATH),timeout=15); c.row_factory=sqlite3.Row; c.execute("PRAGMA busy_timeout=15000"); return c

def _table_exists(db,name): return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None

def get_operations_snapshot(actor):
 from secure_workflow_service import require_permission
 require_permission(actor,"view_workflows")
 result={"generated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),"tables":{},"health":{}}
 with _connect() as db:
  result["health"]["integrity_check"]=db.execute("PRAGMA integrity_check").fetchone()[0]
  for table in ("email_drafts","email_send_attempts","approval_requests","workflow_runs","follow_up_tasks","customer_responses","app_users"):
   if _table_exists(db,table):
    result["tables"][table]=int(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
  if _table_exists(db,"email_drafts"):
   result["email_drafts_by_status"]={r[0]:r[1] for r in db.execute("SELECT status,COUNT(*) FROM email_drafts GROUP BY status")}
  if _table_exists(db,"email_send_attempts"):
   result["send_attempts_by_status"]={r[0]:r[1] for r in db.execute("SELECT status,COUNT(*) FROM email_send_attempts GROUP BY status")}
  if _table_exists(db,"approval_requests"):
   result["approvals_by_status"]={r[0]:r[1] for r in db.execute("SELECT status,COUNT(*) FROM approval_requests GROUP BY status")}
  if _table_exists(db,"customer_responses"):
   result["responses_by_disposition"]={r[0]:r[1] for r in db.execute("SELECT disposition,COUNT(*) FROM customer_responses GROUP BY disposition")}
 return result

def export_release_gate():
 """Static checklist only; no item is auto-certified by a healthy database."""
 return [
  {"control":"Synthetic-data-only validation","required_evidence":"Documented test dataset and no unauthorized customer data","status":"Not verified"},
  {"control":"Identity and MFA","required_evidence":"Approved SSO configuration and negative role tests","status":"Not verified"},
  {"control":"Database/process isolation","required_evidence":"Least-privilege runtime identity and verified ACLs","status":"Not verified"},
  {"control":"Immutable external audit","required_evidence":"WORM/append-only store and tamper detection test","status":"Not verified"},
  {"control":"Email sender scope","required_evidence":"Dedicated mailbox, tenant admin approval and negative scope test","status":"Not verified"},
  {"control":"Delivery Unknown reconciliation","required_evidence":"Documented provider reconciliation drill; no blind retries","status":"Not verified"},
  {"control":"Backup restoration","required_evidence":"Successful restore drill with documented recovery point","status":"Not verified"},
  {"control":"Security/privacy review","required_evidence":"Approved threat model, privacy review and security sign-off","status":"Not verified"},
 ]
