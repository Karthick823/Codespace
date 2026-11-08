# AI Collections Employee — V3.7.1 + V3.8 Installation and Hardening Guide

## What is included

### V3.7.1 — Email lifecycle hardening
- Synchronizes the saved draft's status with the linked `approval_requests` status (`Pending Approval`, `Approved`, `Rejected`).
- Stores draft content snapshots in `email_draft_versions`, including a SHA-256 content hash, version, editor identity, change reason, and timestamp.
- Uses optimistic locking (`expected_version`) to prevent stale edits overwriting newer changes.
- Makes submitted/approved drafts immutable through the service API. To change content after rejection, create a new version and submit again.
- Adds recovery for drafts stuck in `Submitting`, reconciling an approval record before resetting to `Draft`.
- Captures the approved version in the approval rationale and blocks sending if the approval does not match the current draft version.

### V3.8 — Controlled sending through Microsoft Graph
- Microsoft Graph is the single provider implemented in this milestone.
- Actual sending is disabled by default. It requires `EMAIL_SENDING_ENABLED=true` plus all Graph environment variables.
- Only `admin` or `reviewer` roles can send, and only a draft whose linked approval is still `Approved` and whose approved version equals the current version.
- The operator must type the exact recipient address and check a confirmation box in the UI.
- A unique send attempt is recorded before the provider call. A second attempt for the same draft version is blocked.
- A timeout/network error becomes `Delivery Unknown`; it is not automatically retried because the provider may already have accepted the email.
- Audit events record metadata only, not email body content or secrets.

## Files

Copy these files into your project root:

- `app_v3_7_1.py` — V3.7.1 UI; does not expose sending.
- `app_v3_8.py` — V3.8 UI with controlled send interface.
- `email_draft_service_v3_8.py` — lifecycle, versioning, approval sync, and recovery service.
- `email_provider_service_v3_8.py` — Microsoft Graph provider and guarded send function.
- `test_v3_8_controlled_email.py` — automated tests; all provider sends are mocked.
- `SECURITY_HARDENING_PRE_PRODUCTION.md` — production readiness work that remains outstanding.

Keep all previous V3.2–V3.7 files and support modules. Do not overwrite your database or old versions.

## 1. Back up the current application and database

Stop Streamlit with `Ctrl+C`, then run PowerShell:

```powershell
cd "C:\Business\AI Collections Employee\Codespace\AI Collections Employee - MVP"
New-Item -ItemType Directory -Force -Path ".\backups\v3_8"
Copy-Item ".\app_v3_7.py" ".\backups\v3_8\app_v3_7_backup.py"
Copy-Item ".\email_draft_service.py" ".\backups\v3_8\email_draft_service_v3_7_backup.py"
Copy-Item ".\collection_workflow.db" ".\backups\v3_8\collection_workflow_pre_v3_8.db"
```

Keep the backup outside any public web directory and protect it as confidential data.

## 2. Copy files and compile

Activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Compile:

```powershell
python -m py_compile app_v3_7_1.py app_v3_8.py email_draft_service_v3_8.py email_provider_service_v3_8.py test_v3_8_controlled_email.py
```

Expected: no output and no syntax errors.

## 3. Run tests before launching the UI

```powershell
python -m unittest -v test_v3_8_controlled_email.py
python -m unittest -v test_v3_7_email_drafts.py
python -m unittest -v test_v3_6_reliability.py
python -m unittest -v test_v3_5_secure_workflow.py
python -m unittest -v test_v3_2_regression.py
```

The new suite currently contains 11 tests, including stale-send recovery. It mocks the provider; **it does not send real email**. The older suites may emit `ResourceWarning` from existing SQLite helpers that do not close all connections. Those warnings are not test failures, but they are technical debt to fix before production.

## 4. Run V3.7.1 first (no sending UI)

```powershell
python -m streamlit run app_v3_7_1.py
```

Test:
1. Create a new synthetic-invoice draft.
2. Save it and check the draft ID/version.
3. Edit a Draft or Rejected draft and confirm the version increments.
4. Confirm the version history contains both snapshots.
5. Submit for approval.
6. Approve/reject as a different reviewer account.
7. Confirm draft status follows the approval status after refresh.
8. Restart the app and confirm records persist.

Stop the app before starting V3.8.

## 5. Configure Microsoft Graph — only when ready for a controlled test

The send function uses Microsoft Graph `POST /users/{id-or-UPN}/sendMail`. Microsoft documents the `Mail.Send` permission and `202 Accepted` response for this API. A `202` means accepted for processing, not proof of final delivery.

Official references:
- Microsoft Graph `sendMail`: https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0
- Microsoft identity platform client credentials flow: https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow

A Microsoft 365 administrator must create/configure an app registration, grant admin consent to the required **Application** `Mail.Send` permission, and restrict the app to a dedicated sender mailbox using the organization's supported Exchange mailbox-scoping controls. Do not grant broader permissions than needed. The current prototype uses a client secret; for production prefer a certificate or federated credential and a managed secrets store.

Set the following variables only in a protected environment, never in source code:

- `MS_GRAPH_TENANT_ID`
- `MS_GRAPH_CLIENT_ID`
- `MS_GRAPH_CLIENT_SECRET`
- `MS_GRAPH_SENDER_UPN`
- `EMAIL_SENDING_ENABLED`

For initial setup, keep sending disabled or set it to `false`. In PowerShell, an explicit local test setup (only after the tenant admin has configured the app) can use session-scoped variables:

```powershell
$env:MS_GRAPH_TENANT_ID = "<tenant-id>"
$env:MS_GRAPH_CLIENT_ID = "<application-client-id>"
$env:MS_GRAPH_CLIENT_SECRET = "<secret-from-secure-store>"
$env:MS_GRAPH_SENDER_UPN = "<dedicated-test-mailbox-UPN>"
$env:EMAIL_SENDING_ENABLED = "false"
```

Do not paste secrets into ChatGPT, source files, screenshots, logs, or commit them to Git. Session-scoped variables disappear when the shell closes, but they are not a production secrets-management solution.

## 6. Start V3.8 in safe mode

```powershell
$env:EMAIL_SENDING_ENABLED = "false"
python -m streamlit run app_v3_8.py
```

Check the provider status at the top of the saved draft section. Sending must show disabled. Verify draft creation, versioning, approval synchronization, and recovery still work.

## 7. Controlled live-send test (only after authorization and policy review)

Do not enable sending until the app registration is approved by the mailbox/tenant administrator and you are using a dedicated test mailbox and a recipient you control.

For a controlled test, create a new V3.8 draft and submit it for approval. Drafts/approvals created by old versions may lack the version marker and will be marked stale; create a fresh draft. Have a different reviewer approve it. Check the saved recipient and content. Then set:

```powershell
$env:EMAIL_SENDING_ENABLED = "true"
```

Restart Streamlit in the same PowerShell session. On the approved draft, type the exact recipient address, tick the confirmation checkbox, and send once. Check the provider response, Sent Items, and local send attempt history.

**Do not repeat a send if the status is `Delivery Unknown`.** Investigate Sent Items/provider logs and reconcile manually first. The service deliberately blocks another send attempt for the same draft version, because Graph may have accepted the request before the client timed out.

After testing, immediately turn sending off:

```powershell
$env:EMAIL_SENDING_ENABLED = "false"
```

## Important limitations

- Microsoft Graph is the only provider implemented; Gmail/SMTP are not included.
- A successful `202 Accepted` means Graph accepted the request for processing; it does not prove recipient delivery.
- The send attempt ID is a local idempotency guard, not a provider-guaranteed exactly-once delivery token.
- `email_draft_versions` is append-only through this service API, not tamper-proof against a user with direct database/process access. A content hash ties the approval to the exact draft content; this detects ordinary version/content mismatch but is not a defense against a privileged attacker who can rewrite both the database and application.
- The prototype still uses local authentication and SQLite. Direct database/process access, production deployment hardening, SSO/MFA, secure server-side session management, immutable external audit storage, and migration/restriction of every legacy service entry point remain mandatory before external production use. See `SECURITY_HARDENING_PRE_PRODUCTION.md` for the release gate checklist.
- Recipient syntax validation does not verify customer-mailbox ownership. The human reviewer must verify the recipient against an authoritative customer/invoice system; automated authoritative contact validation is still future work.
- Use synthetic or explicitly authorized data until the full security checklist is complete.
