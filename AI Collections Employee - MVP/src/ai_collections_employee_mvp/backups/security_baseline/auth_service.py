"""
V3.4 local authentication and role service.

Uses PBKDF2-HMAC-SHA256 password hashes and constant-time verification.
For local prototype only; production deployments should use enterprise
SSO/identity provider and server-side session controls.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "collection_workflow.db"

ROLES = ("admin", "reviewer", "collector", "viewer")
ITERATIONS = 310_000
DUMMY_SALT = b"collection-workflow-local-auth-dummy-salt"
MAX_PASSWORD_LENGTH = 256
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,80}")


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(str(DB_PATH), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_locked(row: sqlite3.Row) -> bool:
    locked_until = _parse_timestamp(row["locked_until"])

    if locked_until is None:
        return False

    return locked_until > datetime.now(timezone.utc)  # noqa: UP017


def initialize_auth() -> None:
    with closing(_connect()) as db, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (
                    role IN ('admin', 'reviewer', 'collector', 'viewer')
                ),
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_app_users_active
            ON app_users(is_active, username)
            """
        )


def _hash_password(
    password: Any,
    salt: bytes | None = None,
) -> tuple[str, str]:
    if not isinstance(password, str):
        raise ValueError("Password must be text.")  # noqa: TRY004

    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters.")

    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must not exceed {MAX_PASSWORD_LENGTH} characters."
        )

    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        ITERATIONS,
    )

    return salt.hex(), digest.hex()


def create_user(
    username: Any,
    password: Any,
    role: Any,
    display_name: Any = None,
    created_by: Any = "bootstrap",
) -> int:
    initialize_auth()

    username = str(username or "").strip()

    if not _USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "Username must be 3-80 characters: letters, numbers, dot, "
            "underscore, hyphen."
        )

    if role not in ROLES:
        raise ValueError("Unsupported role.")

    display_name = str(display_name or username).strip()[:120]

    if not display_name:
        raise ValueError("Display name is required.")

    created_by = str(created_by or "bootstrap").strip()[:80] or "bootstrap"

    salt, digest = _hash_password(password)
    now = _now()

    try:
        with closing(_connect()) as db, db:
            cursor = db.execute(
                """
                INSERT INTO app_users (
                    username,
                    display_name,
                    role,
                    password_salt,
                    password_hash,
                    is_active,
                    created_at,
                    created_by,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    username,
                    display_name,
                    role,
                    salt,
                    digest,
                    now,
                    created_by,
                    now,
                ),
            )

            user_id = cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError("A user with this username already exists.") from exc

    if user_id is None:
        raise RuntimeError("User creation failed.")

    return int(user_id)


def list_users() -> list[dict[str, Any]]:
    initialize_auth()

    with closing(_connect()) as db:
        rows = db.execute(
            """
            SELECT
                id,
                username,
                display_name,
                role,
                is_active,
                created_at,
                created_by,
                updated_at,
                failed_attempts,
                locked_until
            FROM app_users
            ORDER BY id
            """
        ).fetchall()

    return [dict(row) for row in rows]


def set_user_active(
    username: Any,
    active: Any,
    changed_by: Any = "admin",
) -> None:
    initialize_auth()

    username = str(username or "").strip()

    if not username:
        raise ValueError("Username is required.")

    changed_by = str(changed_by or "admin").strip()[:80] or "admin"
    active_flag = 1 if active else 0
    now = _now()

    with closing(_connect()) as db, db:
        cursor = db.execute(
            """
            UPDATE app_users
            SET
                is_active = ?,
                failed_attempts = 0,
                locked_until = NULL,
                updated_at = ?
            WHERE username = ? COLLATE NOCASE
            """,
            (active_flag, now, username),
        )

        if cursor.rowcount != 1:
            raise ValueError("User not found.")


def authenticate(username: Any, password: Any) -> dict[str, Any] | None:
    initialize_auth()

    username = str(username or "").strip()[:80]
    password_text = str(password or "")

    # Prevent excessive PBKDF2 work from oversized submitted passwords.
    if len(password_text) > MAX_PASSWORD_LENGTH:
        return None

    with closing(_connect()) as db, db:
        row = db.execute(
            "SELECT * FROM app_users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()

        if row is None:
            # Perform equivalent work to reduce username-enumeration timing
            # differences.
            hashlib.pbkdf2_hmac(
                "sha256",
                password_text.encode("utf-8"),
                DUMMY_SALT,
                ITERATIONS,
            )
            return None

        if not row["is_active"] or _is_locked(row):
            return None

        try:
            salt = bytes.fromhex(row["password_salt"])
        except ValueError:
            return None

        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password_text.encode("utf-8"),
            salt,
            ITERATIONS,
        ).hex()

        if not hmac.compare_digest(candidate, row["password_hash"]):
            failures = int(row["failed_attempts"] or 0) + 1
            locked_until = None

            if failures >= MAX_FAILED_ATTEMPTS:
                locked_until = (
                    datetime.now(timezone.utc) + LOCKOUT_DURATION  # noqa: UP017
                ).isoformat(timespec="seconds")

            db.execute(
                """
                UPDATE app_users
                SET
                    failed_attempts = ?,
                    locked_until = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (failures, locked_until, _now(), row["id"]),
            )

            return None

        db.execute(
            """
            UPDATE app_users
            SET
                failed_attempts = 0,
                locked_until = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (_now(), row["id"]),
        )

        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
        }


def current_user() -> dict[str, Any] | None:
    import streamlit as st

    user = st.session_state.get("v34_user")

    if not isinstance(user, dict) or user.get("role") not in ROLES:
        return None

    return user


def logout() -> None:
    import streamlit as st

    st.session_state.pop("v34_user", None)