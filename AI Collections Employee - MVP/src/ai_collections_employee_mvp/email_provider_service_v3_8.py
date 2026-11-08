"""V3.8 controlled Microsoft Graph email sending for AI Collections Employee.
No email is sent unless EMAIL_SENDING_ENABLED=true and Graph credentials are
present. Only approved, version-matched drafts can be sent. Ambiguous outcomes
are never automatically retried.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from email_draft_service_v3_8 import (
    _audit,
    _connect,
    _content_hash,
    _now,
    _parse_positive_int,
    _parse_timestamp,
    get_email_draft,
    initialize_email_drafts,
    validate_email_draft,
)

PROVIDER = "microsoft_graph"
REQUIRED_ENVIRONMENT_VARIABLES = (
    "MS_GRAPH_TENANT_ID",
    "MS_GRAPH_CLIENT_ID",
    "MS_GRAPH_CLIENT_SECRET",
    "MS_GRAPH_SENDER_UPN",
)
class ProviderRejected(RuntimeError):
    """Microsoft Graph definitely rejected the send request."""
class DeliveryOutcomeUnknown(RuntimeError):
    """The request may have reached Microsoft Graph; do not retry automatically."""
class ProviderConfigurationError(RuntimeError):
    """The provider could not be reached or authenticated before dispatch."""
def _env(name: str) -> str:
    return str(os.environ.get(name, "")).strip()
def sending_enabled() -> bool:
    return _env("EMAIL_SENDING_ENABLED").casefold() == "true"
def provider_status() -> dict[str, Any]:
    missing = [
        name
        for name in REQUIRED_ENVIRONMENT_VARIABLES
        if not _env(name)
    ]
    enabled = sending_enabled()
    configured = not missing
    if enabled and configured:
        mode = "LIVE SEND ENABLED"
    else:
        mode = "DISABLED / NOT READY"
    return {
        "provider": PROVIDER,
        "enabled": enabled,
        "configured": configured,
        "missing_environment_variables": missing,
        "mode": mode,
    }
def _require_actor(
    actor: dict[str, Any] | None,
    permission: str = "decide_approval",
) -> dict[str, Any]:
    from secure_workflow_service import require_permission
    return require_permission(actor, permission)
def _graph_token(timeout: int = 20) -> str:
    tenant = _env("MS_GRAPH_TENANT_ID")
    client_id = _env("MS_GRAPH_CLIENT_ID")
    client_secret = _env("MS_GRAPH_CLIENT_SECRET")
    if not tenant or not client_id or not client_secret:
        raise ProviderConfigurationError(
            "Microsoft Graph is not configured. "
            "Missing tenant/client credentials."
        )
    token_url = (
        "https://login.microsoftonline.com/"
        + urllib.parse.quote(tenant, safe="")
        + "/oauth2/v2.0/token"
    )
    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        token_url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise ProviderConfigurationError(
            f"Microsoft identity endpoint rejected the token request "
            f"(HTTP {error.code})."
        ) from error
    except (TimeoutError, urllib.error.URLError, ConnectionError) as error:
        raise ProviderConfigurationError(
            "Microsoft identity endpoint could not be reached."
        ) from error
    except (ValueError, json.JSONDecodeError) as error:
        raise ProviderConfigurationError(
            "Microsoft identity endpoint returned an invalid token response."
        ) from error
    token = payload.get("access_token")
    if not token:
        raise ProviderConfigurationError(
            "Microsoft identity endpoint did not return an access token."
        )
    return str(token)
def _graph_send(
    recipient: str,
    subject: str,
    body: str,
    attempt_id: str,
    timeout: int = 30,
) -> dict[str, Any]:
    sender = _env("MS_GRAPH_SENDER_UPN")
    if not sender:
        raise ProviderConfigurationError(
            "MS_GRAPH_SENDER_UPN is not configured."
        )
    token = _graph_token(timeout=min(timeout, 20))
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": recipient,
                    },
                },
            ],
        },
        "saveToSentItems": True,
    }
    data = json.dumps(payload).encode("utf-8")
    endpoint = (
        "https://graph.microsoft.com/v1.0/users/"
        + urllib.parse.quote(sender, safe="")
        + "/sendMail"
    )
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "client-request-id": attempt_id,
            "return-client-request-id": "true",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 202:
                raise ProviderRejected(
                    "Microsoft Graph returned an unexpected HTTP status "
                    f"{response.status}."
                )
            provider_message_id = (
                response.headers.get("request-id")
                or response.headers.get("client-request-id")
            )
            return {
                "status": "Sent",
                "provider_message_id": provider_message_id,
            }
    except urllib.error.HTTPError as error:
        # This is a definite provider rejection. Do not persist or log the
        # response body because it may contain sensitive information.
        raise ProviderRejected(
            f"Microsoft Graph rejected the send request (HTTP {error.code})."
        ) from error
    except (
        TimeoutError,
        urllib.error.URLError,
        ConnectionError,
    ) as error:
        # The request may have reached Graph. Automatic retries could
        # duplicate the message.
        raise DeliveryOutcomeUnknown(
            "The provider outcome is unknown due to a network or timeout "
            "error. Do not retry automatically."
        ) from error
def _ensure_send_attempt_schema() -> None:
    with _connect() as db, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS email_send_attempts (
                attempt_id TEXT PRIMARY KEY,
                draft_id INTEGER NOT NULL,
                draft_version INTEGER NOT NULL,
                approval_request_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                recipient_email TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                provider_message_id TEXT,
                error_message TEXT,
                error_type TEXT,
                error_summary TEXT
            )
            """
        )
        existing_columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(email_send_attempts)"
            ).fetchall()
        }
        additions = {
            "provider_message_id": "TEXT",
            "error_type": "TEXT",
            "error_summary": "TEXT",
        }
        for column_name, column_type in additions.items():
            if column_name not in existing_columns:
                db.execute(
                    f"ALTER TABLE email_send_attempts "
                    f"ADD COLUMN {column_name} {column_type}"
                )
def _mark_attempt(
    attempt_id: str,
    draft_id: int,
    status: str,
    error_type: str | None = None,
    error_summary: str | None = None,
    provider_message_id: str | None = None,
) -> None:
    _ensure_send_attempt_schema()
    now = _now()
    with _connect() as db, db:
        db.execute(
            """
            UPDATE email_send_attempts
            SET
                status = ?,
                completed_at = ?,
                provider_message_id = COALESCE(
                    ?,
                    provider_message_id
                ),
                error_type = ?,
                error_summary = ?
            WHERE attempt_id = ?
            """,
            (
                status,
                now,
                provider_message_id,
                error_type,
                error_summary,
                attempt_id,
            ),
        )
        db.execute(
            """
            UPDATE email_drafts
            SET
                status = ?,
                send_status = ?,
                provider_message_id = COALESCE(
                    ?,
                    provider_message_id
                ),
                updated_at = ?
            WHERE id = ?
              AND last_send_attempt_id = ?
            """,
            (
                status,
                status,
                provider_message_id,
                now,
                draft_id,
                attempt_id,
            ),
        )
def send_approved_email(
    actor: dict[str, Any],
    draft_id: Any,
    *,
    expected_version: Any,
    confirm_recipient: Any,
) -> dict[str, Any]:
    """Send one explicitly confirmed approved draft through Microsoft Graph."""
    actor = _require_actor(actor, "decide_approval")
    draft_id = _parse_positive_int(draft_id, "Draft ID")
    expected_version = _parse_positive_int(
        expected_version,
        "Expected version",
    )
    config = provider_status()
    if not config["enabled"]:
        raise PermissionError(
            "Live sending is disabled. Set EMAIL_SENDING_ENABLED=true only "
            "after completing provider and security setup."
        )
    if not config["configured"]:
        raise RuntimeError(
            "Microsoft Graph is not configured; required configuration is "
            "missing."
        )
    initialize_email_drafts()
    _ensure_send_attempt_schema()
    with _connect() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM email_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Email draft not found.")
        if actor["role"] not in {"admin", "reviewer"}:
            raise PermissionError(
                "Only an authorized reviewer or administrator may send an "
                "approved draft."
            )
        if row["status"] != "Approved":
            raise ValueError(
                "Draft status must be Approved; current status is "
                f"{row['status']}."
            )
        if (
            int(row["version"]) != expected_version
            or row["approved_version"] is None
            or int(row["approved_version"]) != int(row["version"])
        ):
            raise ValueError(
                "The current draft version is not the version approved. "
                "Re-review the latest version."
            )
        saved_recipient = str(row["recipient_email"]).casefold()
        confirmed_recipient = str(confirm_recipient or "").strip().casefold()
        if confirmed_recipient != saved_recipient:
            raise ValueError(
                "Typed recipient confirmation does not match the saved "
                "recipient."
            )
        if not row["approval_request_id"]:
            raise ValueError(
                "An approval request is required before sending."
            )
        approval = db.execute(
            """
            SELECT
                status,
                rationale,
                reviewer_user_id,
                requester_user_id
            FROM approval_requests
            WHERE id = ?
            """,
            (int(row["approval_request_id"]),),
        ).fetchone()
        if approval is None or approval["status"] != "Approved":
            raise ValueError(
                "The linked approval is missing or is not Approved."
            )
        rationale = approval["rationale"] or ""
        version_match = re.search(
            r"\[EMAIL_DRAFT_VERSION:(\d+)\]",
            rationale,
        )
        hash_match = re.search(
            r"\[EMAIL_DRAFT_HASH:([0-9a-f]{64})\]",
            rationale,
        )
        current_hash = _content_hash(
            row["invoice_number"],
            row["customer_name"],
            row["recipient_email"],
            row["subject"],
            row["body"],
            row["tone"],
            row["recommended_action"],
        )
        snapshot = db.execute(
            """
            SELECT content_hash
            FROM email_draft_versions
            WHERE draft_id = ? AND version = ?
            """,
            (draft_id, int(row["version"])),
        ).fetchone()
        approval_matches = (
            version_match is not None
            and int(version_match.group(1)) == int(row["version"])
            and hash_match is not None
            and hash_match.group(1) == current_hash
            and snapshot is not None
            and snapshot["content_hash"] == current_hash
        )
        if not approval_matches:
            raise ValueError(
                "Approval does not match the exact current draft content "
                "and version."
            )
        if (
            approval["requester_user_id"] is not None
            and actor.get("id") is not None
            and int(approval["requester_user_id"]) == int(actor["id"])
        ):
            raise PermissionError(
                "The requester cannot send their own approved draft; "
                "use separation of duties."
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
                "Current draft failed validation: "
                + " ".join(validation["errors"])
            )
        prior = db.execute(
            """
            SELECT status
            FROM email_send_attempts
            WHERE draft_id = ? AND draft_version = ?
            """,
            (draft_id, int(row["version"])),
        ).fetchone()
        if prior is not None:
            raise ValueError(
                "A send attempt already exists for this draft version "
                f"(status: {prior['status']}). Reconcile provider status; "
                "do not retry blindly."
            )
        attempt_id = str(uuid.uuid4())
        now = _now()
        db.execute(
            """
            INSERT INTO email_send_attempts (
                attempt_id,
                draft_id,
                draft_version,
                approval_request_id,
                provider,
                recipient_email,
                status,
                started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'Sending', ?)
            """,
            (
                attempt_id,
                draft_id,
                int(row["version"]),
                int(row["approval_request_id"]),
                PROVIDER,
                row["recipient_email"],
                now,
            ),
        )
        db.execute(
            """
            UPDATE email_drafts
            SET
                status = 'Sending',
                send_status = 'Sending',
                last_send_attempt_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (attempt_id, now, draft_id),
        )
    draft_version = int(row["version"])
    approval_request_id = int(row["approval_request_id"])
    try:
        outcome = _graph_send(
            row["recipient_email"],
            row["subject"],
            row["body"],
            attempt_id,
        )
        status = outcome["status"]
        provider_message_id = outcome.get("provider_message_id")
        _mark_attempt(
            attempt_id,
            draft_id,
            status,
            provider_message_id=provider_message_id,
        )
        _audit(
            actor,
            "email_sent",
            draft_id,
            row["invoice_number"],
            validation,
            {
                "provider": PROVIDER,
                "attempt_id": attempt_id,
                "recipient": row["recipient_email"],
                "draft_version": draft_version,
                "approval_request_id": approval_request_id,
            },
        )
        return {
            "draft_id": draft_id,
            "status": "Sent",
            "provider": PROVIDER,
            "attempt_id": attempt_id,
            "provider_message_id": provider_message_id,
            "recipient": row["recipient_email"],
        }
    except ProviderRejected as error:
        _mark_attempt(
            attempt_id,
            draft_id,
            "Send Failed",
            error_type=type(error).__name__,
            error_summary=str(error)[:500],
        )
        _audit(
            actor,
            "email_send_failed",
            draft_id,
            row["invoice_number"],
            validation,
            {
                "provider": PROVIDER,
                "attempt_id": attempt_id,
                "draft_version": draft_version,
                "error_type": type(error).__name__,
            },
        )
        raise
    except DeliveryOutcomeUnknown as error:
        _mark_attempt(
            attempt_id,
            draft_id,
            "Delivery Unknown",
            error_type=type(error).__name__,
            error_summary=str(error)[:500],
        )
        _audit(
            actor,
            "email_delivery_unknown",
            draft_id,
            row["invoice_number"],
            validation,
            {
                "provider": PROVIDER,
                "attempt_id": attempt_id,
                "draft_version": draft_version,
                "error_type": type(error).__name__,
            },
        )
        raise
    except Exception as error:
        # Any unexpected failure after the send request was dispatched is
        # conservatively treated as an unknown delivery outcome.
        _mark_attempt(
            attempt_id,
            draft_id,
            "Delivery Unknown",
            error_type=type(error).__name__,
            error_summary=str(error)[:500],
        )
        _audit(
            actor,
            "email_delivery_unknown",
            draft_id,
            row["invoice_number"],
            validation,
            {
                "provider": PROVIDER,
                "attempt_id": attempt_id,
                "draft_version": draft_version,
                "error_type": type(error).__name__,
            },
        )
        raise
def list_send_attempts(
    actor: dict[str, Any],
    draft_id: Any,
) -> list[dict[str, Any]]:
    # Enforces draft visibility before returning provider attempt data.
    draft_id = _parse_positive_int(draft_id, "Draft ID")
    get_email_draft(actor, draft_id)
    _ensure_send_attempt_schema()
    with _connect() as db:
        rows = db.execute(
            """
            SELECT
                attempt_id,
                draft_id,
                draft_version,
                approval_request_id,
                provider,
                recipient_email,
                status,
                started_at,
                completed_at,
                provider_message_id,
                error_type,
                error_summary
            FROM email_send_attempts
            WHERE draft_id = ?
            ORDER BY started_at DESC
            """,
            (draft_id,),
        ).fetchall()
    return [dict(row) for row in rows]
def recover_stale_send_attempts(
    actor: dict[str, Any],
    older_than_seconds: Any = 300,
) -> int:
    """Mark abandoned Sending attempts as Delivery Unknown.
    This never retries automatically.
    """
    actor = _require_actor(actor, "decide_approval")
    if actor["role"] not in {"admin", "reviewer"}:
        raise PermissionError(
            "Only reviewers or administrators can reconcile send attempts."
        )
    older_than_seconds = _parse_positive_int(
        older_than_seconds,
        "Stale attempt threshold",
    )
    older_than_seconds = min(max(older_than_seconds, 60), 86400)
    initialize_email_drafts()
    _ensure_send_attempt_schema()
    cutoff = datetime.now(timezone.utc) - timedelta(  # noqa: UP017
        seconds=older_than_seconds
    )
    marked = 0
    with _connect() as db, db:
        rows = db.execute(
            """
            SELECT
                attempt_id,
                draft_id,
                started_at
            FROM email_send_attempts
            WHERE status = 'Sending'
            """
        ).fetchall()
        for row in rows:
            started_at = _parse_timestamp(row["started_at"])
            if started_at is None:
                continue
            # SQLite timestamps may not contain timezone information.
            # Treat naive timestamps as UTC.
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)  # noqa: UP017
            else:
                started_at = started_at.astimezone(timezone.utc)  # noqa: UP017
            if started_at >= cutoff:
                continue
            db.execute(
                """
                UPDATE email_send_attempts
                SET
                    status = 'Delivery Unknown',
                    completed_at = ?,
                    error_type = 'StaleSendingRecovery',
                    error_summary = ?
                WHERE attempt_id = ?
                  AND status = 'Sending'
                """,
                (
                    _now(),
                    "Process stopped or provider outcome is unknown; "  # noqa: ISC004
                    "manual reconciliation required.",
                    row["attempt_id"],
                ),
            )
            db.execute(
                """
                UPDATE email_drafts
                SET
                    status = 'Delivery Unknown',
                    send_status = 'Delivery Unknown',
                    updated_at = ?
                WHERE id = ?
                  AND last_send_attempt_id = ?
                  AND status = 'Sending'
                """,
                (_now(), int(row["draft_id"]), row["attempt_id"]),
            )
            marked += 1
    if marked:
        _audit(
            actor,
            "email_send_recovery_marked_unknown",
            0,
            "multiple",
            extra={
                "attempt_count": marked,
                "policy": "manual_reconciliation_no_auto_retry",
            },
        )
    return marked
