# V3.5 — Secure Workflow and Audit Integration

## Deliverables
- `app_v3_5.py`: V3.4-based UI with secured approval operations and workflow links.
- `secure_workflow_service.py`: service-layer permission checks, separation-of-duties checks, approval metadata migration, workflow links.
- `workflow_orchestrator_v3_5.py`: orchestration path updated to require an authenticated actor and use the secure approval service.
- `test_v3_5_secure_workflow.py`: isolated authorization tests using a temporary SQLite database.

## Install safely
1. Stop Streamlit with Ctrl+C.
2. Back up `app_v3_4.py`, `workflow_orchestrator.py`, and `collection_workflow.db` while the app is stopped.
3. Place all four Python files in the same project directory as existing services.
4. Activate the virtual environment: `\.venv\Scripts\Activate.ps1`
5. Compile: `python -m py_compile app_v3_5.py secure_workflow_service.py workflow_orchestrator_v3_5.py test_v3_5_secure_workflow.py`
6. Run new tests: `python -m unittest -v test_v3_5_secure_workflow.py`
7. Run existing regression tests: `python -m unittest -v test_v3_2_regression.py`
8. Start: `python -m streamlit run app_v3_5.py`

## Test checklist
- Collector can create an approval request but cannot decide one.
- Reviewer can decide a pending request created by a different user.
- Requester cannot approve or reject their own request.
- Viewer cannot create approval requests.
- A second decision against a non-pending request is rejected.
- Workflow/task/approval linkage is displayed and exportable.
- Audit events are recorded for secured approval creation and decisions.
- Existing workflow, approval, user, and audit records remain after restart.
- Email sending, accounting updates, and payment settlement remain disabled.

## Database behavior
`secure_workflow_service.py` performs additive SQLite migrations: it adds nullable linkage and authenticated actor columns to `approval_requests` and creates `secure_workflow_links`. It does not drop tables or delete existing rows. Back up the database before first launch. Old approval rows may have no user IDs or workflow links; this is expected for pre-V3.5 records.

## Security caveats
This is still a local prototype. V3.5 strengthens the app's normal service path, but it is not a security certification. The legacy `approval_service.py` remains callable by older modules and direct local code; do not expose the app to untrusted users until every legacy call path is migrated or restricted. Local process/database access, Streamlit session security, SSO/MFA, HTTPS, secrets management, immutable audit storage, and deployment hardening remain future work. No email, payment, or ERP actions are enabled.
