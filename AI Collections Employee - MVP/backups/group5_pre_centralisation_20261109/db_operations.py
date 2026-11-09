"""V3.4 SQLite maintenance helpers. Backups use SQLite's online backup API."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH=Path(__file__).resolve().parent / "collection_workflow.db"
BACKUP_DIR=Path(__file__).resolve().parent / "backups" / "database"

def get_database_info():
    exists=DB_PATH.exists()
    return {"database_path":str(DB_PATH),"exists":exists,"size_bytes":DB_PATH.stat().st_size if exists else 0,
            "backup_directory":str(BACKUP_DIR)}

def check_database_integrity():
    if not DB_PATH.exists(): return {"ok":False,"result":"Database file does not exist."}
    with sqlite3.connect(str(DB_PATH), timeout=15) as db:
        result=[r[0] for r in db.execute("PRAGMA integrity_check").fetchall()]
    return {"ok":result == ["ok"],"result":result}

def create_backup():
    if not DB_PATH.exists(): raise FileNotFoundError(f"Database not found: {DB_PATH}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
    target=BACKUP_DIR / f"collection_workflow_{stamp}.db"
    with sqlite3.connect(str(DB_PATH), timeout=15) as source, sqlite3.connect(str(target), timeout=15) as dest:
        source.backup(dest)
        result=[r[0] for r in dest.execute("PRAGMA integrity_check").fetchall()]
        if result != ["ok"]:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"Backup integrity check failed: {result}")
    return target
