# V3.4 — Production Foundations: Installation & Test Guide

## What is included
- `app_v3_4.py`: V3.3 application with local login, UI role checks, audit trail, user administration and database health/backup controls.
- `auth_service.py`: PBKDF2 password hashing, account management, failed-login lockout, roles.
- `audit_service.py`: structured audit events.
- `db_operations.py`: SQLite integrity check and online backup.
- `manage_users.py`: secure interactive CLI for creating the initial admin and other users.

## Important scope
This is a local prototype hardening milestone, not production certification. Authentication is stored in SQLite and the Streamlit session. Do not expose it directly to the public internet or use real customer data until identity/session security, TLS, service-level authorization, privacy, retention, monitoring and independent security review are implemented.

## Install
1. Stop Streamlit with `Ctrl+C`.
2. Back up `app_v3_3.py` and `collection_workflow.db` while the app is stopped.
3. Copy all four `.py` files into the same project directory as the existing services and database.
4. Activate `.venv`:
   ```powershell
   cd "C:\Business\AI Collections Employee\Codespace\AI Collections Employee - MVP"
   .\.venv\Scripts\Activate.ps1
   ```
5. Create the first administrator. This prompts for the password without displaying it:
   ```powershell
   python manage_users.py bootstrap-admin
   ```
6. Compile:
   ```powershell
   python -m py_compile app_v3_4.py auth_service.py audit_service.py db_operations.py manage_users.py
   ```
7. Run existing regressions:
   ```powershell
   python -m unittest -v test_v3_2_regression.py
   ```
8. Launch:
   ```powershell
   python -m streamlit run app_v3_4.py
   ```

## Role model
- `admin`: user administration, audit view/export, database health and backups, approval decisions.
- `reviewer`: approval decisions.
- `collector`: standard workflow use and request submission; cannot approve.
- `viewer`: view the application; cannot approve or administer users.

The role checks are currently implemented in the Streamlit interface. Before a multi-user or hosted deployment, enforce permissions at every service/API boundary and use enterprise identity/SSO.

## Tests
A. Bootstrap admin; confirm password is not echoed.
B. Sign in as admin; confirm V3.4 section, user management, audit, database controls.
C. Create a reviewer and collector using the V3.4 admin panel; test sign-in separately (sign out first).
D. As collector, verify the approval queue is visible but decision controls are blocked.
E. As reviewer, approve/reject a pending synthetic request; confirm audit event appears for admin.
F. Enter wrong password five times; verify account lockout behavior, then wait 15 minutes or reset through administrator support procedure (prototype has no unlock UI; do not brute-force).
G. Run database integrity check; expected `ok`.
H. Create backup; confirm a timestamped `.db` appears under `backups/database` and integrity check passes.
I. Restart app; users, approval records, and audit events persist.
J. Re-run `python -m unittest -v test_v3_2_regression.py`.

## Recovery
Do not replace the live database with a backup while Streamlit is running. For a restore test, stop the app, copy a backup to a separate test folder first, and verify its integrity before any replacement.

## Production blockers
- Streamlit session user state is not a substitute for hardened server-side session management.
- Local login lacks password reset/change flow and multi-factor authentication.
- Approval records themselves still use legacy schema and are not fully bound to authenticated identity at the database/service layer.
- No CSRF/rate limiting/centralized secrets management/HTTPS deployment configuration is included.
- Audit log is application-level and not tamper-proof; restrict filesystem/database access and consider centralized immutable logging in production.
- Retention and deletion policy, incident response, monitoring, and disaster recovery need operational implementation.
