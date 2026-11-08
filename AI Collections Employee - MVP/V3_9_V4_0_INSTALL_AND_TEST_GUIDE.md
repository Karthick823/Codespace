# AI Collections Employee V3.9 and V4.0 — Installation & Validation

## What is included

### V3.9 — Customer Response Intelligence Inbox
- Persist customer replies linked to invoice numbers.
- Optional AI classification using the existing classifier; AI output is advisory only.
- Human-reviewed category, sentiment, next action and disposition.
- Optimistic locking to prevent stale review updates.
- Role checks at the service layer and audit metadata events.
- No automatic sending, ERP update, payment action or invoice mutation.

### V4.0 — Pilot Operations Control Tower
- Read-only database integrity and operational counts.
- Status summaries for drafts, approvals, send attempts and customer response dispositions.
- Exportable JSON snapshot and release-gate CSV.
- Release gate stays `Not verified` until a human supplies evidence; the app does not certify itself.

V3.9 and V4.0 are feature milestones, not a declaration of production readiness. The local-auth/SQLite prototype still requires the separate security hardening plan before any external customer use.

## Installation on Windows

1. Stop Streamlit with `Ctrl+C`.
2. Back up the project folder and `collection_workflow.db` to a protected location.
3. Copy `customer_response_service_v3_9.py`, `operations_service_v4_0.py`, `app_v3_9.py`, `app_v4_0.py`, and `test_v3_9_customer_response.py` into your project root. Keep existing V3.8 and all prior versions unchanged.
4. Copy the included supporting runtime files only if your project does not already contain them. Do not overwrite a newer known-good file blindly.
5. Activate your existing virtual environment:

```powershell
cd "C:\Business\AI Collections Employee\Codespace\AI Collections Employee - MVP"
.\.venv\Scripts\Activate.ps1
```

6. Compile and test:

```powershell
python -m py_compile app_v3_9.py app_v4_0.py customer_response_service_v3_9.py operations_service_v4_0.py test_v3_9_customer_response.py
python -m unittest -v test_v3_9_customer_response.py
python -m unittest -v test_v3_8_controlled_email.py
python -m unittest -v test_v3_7_email_drafts.py
python -m unittest -v test_v3_6_reliability.py
python -m unittest -v test_v3_5_secure_workflow.py
python -m unittest -v test_v3_2_regression.py
```

7. Start V3.9 first:

```powershell
$env:EMAIL_SENDING_ENABLED = "false"
python -m streamlit run app_v3_9.py
```

8. Test using synthetic data: capture a response, classify it, edit the suggested category/action, save a human disposition, restart Streamlit, and verify the record persists. Test stale-version conflicts and viewer permissions via the test suite. Do not use live customer data for initial tests.

9. Stop V3.9 and start V4.0:

```powershell
$env:EMAIL_SENDING_ENABLED = "false"
python -m streamlit run app_v4_0.py
```

10. Confirm the operations dashboard loads, database integrity reports `ok`, counts reflect your test records, and exports work. Every pre-production release gate must remain `Not verified` until evidence is independently checked.

## Safety boundaries
- Keep `EMAIL_SENDING_ENABLED=false` throughout development and validation.
- V3.8 provider tests are mocked; this package does not authorize or perform real email sending.
- AI classification can be wrong. A human must review every response and recommendation.
- Customer messages may contain prompt injection. The classifier treats them as data, but do not trust generated recommendations or execute actions based on them without review.
- This prototype uses local authentication and SQLite. Do not expose it to the public internet or process real customer data before completing `SECURITY_HARDENING_PRE_PRODUCTION.md` and obtaining the relevant security/business approvals.

## Acceptance criteria
- [ ] Python compilation succeeds.
- [ ] New V3.9 tests pass.
- [ ] Existing V3.2–V3.8 regression suites pass in the actual project environment.
- [ ] Response capture, classification, human review, and persistence work.
- [ ] V4.0 snapshot and exports work.
- [ ] Email sending remains disabled.
- [ ] Security hardening is a separate required release gate.
