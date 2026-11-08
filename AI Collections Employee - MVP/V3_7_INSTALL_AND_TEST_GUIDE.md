# AI Collections Employee — V3.7 Email Tools (Draft-Only)

## Included
- `app_v3_7.py`: V3.6 app with database-backed email draft editing, validation, saved-draft library, and approval submission.
- `email_draft_service.py`: SQLite persistence, validation, access checks, audit events, and human approval integration.
- `test_v3_7_email_drafts.py`: automated draft-only tests.

## Explicit safety boundary
This version has **no email provider connection and no send-email tool**. It cannot send email. Submitting a draft creates an approval request only; approval does not send the message.

Validation checks syntax and required fields. It does not verify that a mailbox exists, that the recipient is authorized for the customer, or that invoice facts are correct. A human must verify those details.

## 1. Back up
Stop Streamlit with `Ctrl+C`, then run PowerShell:

```powershell
cd "C:\Business\AI Collections Employee\Codespace\AI Collections Employee - MVP"
New-Item -ItemType Directory -Force -Path ".\backups\v3_7"
Copy-Item ".\app_v3_6.py" ".\backups\v3_7\app_v3_6_backup.py"
Copy-Item ".\collection_workflow.db" ".\backups\v3_7\collection_workflow_pre_v3_7.db"
```

Keep the original V3.6 files and database.

## 2. Copy the files
Copy these files to the project root:
- `app_v3_7.py`
- `email_draft_service.py`
- `test_v3_7_email_drafts.py`

Keep the existing V3.6/V3.5 and supporting modules in the same directory.

## 3. Activate the environment

```powershell
.\.venv\Scripts\Activate.ps1
```

## 4. Compile

```powershell
python -m py_compile app_v3_7.py email_draft_service.py test_v3_7_email_drafts.py
```

Expected: no output.

## 5. Run V3.7 tests

```powershell
python -m unittest -v test_v3_7_email_drafts.py
```

Expected: 11 tests pass.

## 6. Run existing regression suites

```powershell
python -m unittest -v test_v3_6_reliability.py
python -m unittest -v test_v3_5_secure_workflow.py
python -m unittest -v test_v3_2_regression.py
```

Expected: 9 V3.6 reliability tests, 8 V3.5 security tests, and 19 V3.2 regression tests pass.

## 7. Launch V3.7

```powershell
python -m streamlit run app_v3_7.py
```

Use an overdue invoice with a customer email address in your test dataset.

## 8. Manual test checklist
1. Sign in as a collector or admin.
2. Generate an AI payment reminder.
3. Edit the subject and body.
4. Click **Validate draft**. Try a malformed email address and confirm validation rejects it.
5. Enter one valid test recipient and save the draft to the database.
6. Refresh/restart the app and confirm the saved draft still appears.
7. Submit the saved draft for human approval.
8. Confirm the approval queue contains “Review collection email draft #…”.
9. Confirm the draft library shows `Pending Approval` and an approval ID.
10. Try submitting the same draft again; it must not create a second approval.
11. Sign in as a reviewer and inspect the approval. Review the draft separately in the draft library.
12. Verify there is no send button, no email provider credential, and no email is sent.

## What is not included yet
- Email sending, mailbox connectivity, inbound reply ingestion.
- Mailbox existence or customer-recipient authorization verification.
- Editing an already saved draft in-place/version history; saving edits currently creates a new draft record.
- Automatic status synchronization when the related approval is approved/rejected. V3.8 or a follow-up patch should reconcile the draft status with the approval decision.
- Production deployment hardening or tenant isolation.

## Security / operational notes
- Draft body and recipient are persisted in the local SQLite database. Treat the database as confidential customer data.
- Draft access is role/creator scoped in this local prototype. Do not expose this app to untrusted external users.
- Human approval is required; approval does not authorize or trigger sending.
