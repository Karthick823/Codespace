# AI Collections Employee — V3.6 Installation & Test Guide

## What V3.6 adds
- SQLite-backed idempotency keys for workflow creation.
- Request fingerprint validation: a key cannot be reused for a different payload.
- Execution leases to prevent simultaneous runs of the same workflow.
- Lease expiry so interrupted workers can eventually be retried.
- State-transition validation and compare-and-set updates.
- Durable reliability events for create/run/recovery/failure/success and idempotent replay.
- The V3.5 app UI is carried forward, with the workflow orchestrator switched to V3.6.

## Important integration note
The `create_workflow()` API in `workflow_orchestrator_v3_6.py` accepts `idempotency_key` and `request_id`. The current Streamlit create form remains backward-compatible and does not yet ask the user for an idempotency key. For a UI/API retry-safe submission, a future UI patch should create and reuse one key for a single logical submission. Do not generate a brand-new key on every retry.

V3.6 adds reliability primitives and wraps workflow execution with a lease. The existing task/approval side-effect recovery logic from V3.5 remains in place. This is still a local prototype; it is not a distributed job queue and must not be exposed to untrusted users.

## 1. Back up first
Stop Streamlit with `Ctrl+C`, then in PowerShell:

```powershell
cd "C:\Business\AI Collections Employee\Codespace\AI Collections Employee - MVP"
New-Item -ItemType Directory -Force -Path ".\backups\v3_6"
Copy-Item ".\app_v3_5.py" ".\backups\v3_6\app_v3_5_backup.py"
Copy-Item ".\workflow_orchestrator_v3_5.py" ".\backups\v3_6\workflow_orchestrator_v3_5_backup.py"
Copy-Item ".\collection_workflow.db" ".\backups\v3_6\collection_workflow_pre_v3_6.db"
```

Keep V3.5 intact.

## 2. Copy these files to the project root
- `app_v3_6.py`
- `workflow_orchestrator_v3_6.py`
- `workflow_reliability_service.py`
- `test_v3_6_reliability.py`

Keep all V3.5 and existing support modules beside them.

## 3. Activate the environment

```powershell
.\.venv\Scripts\Activate.ps1
```

## 4. Compile

```powershell
python -m py_compile app_v3_6.py workflow_orchestrator_v3_6.py workflow_reliability_service.py test_v3_6_reliability.py
```

Expected: no output.

## 5. Run the new reliability tests

```powershell
python -m unittest -v test_v3_6_reliability.py
```

Expected: 8 tests pass.

## 6. Run prior test suites

```powershell
python -m unittest -v test_v3_5_secure_workflow.py
python -m unittest -v test_v3_2_regression.py
```

Expected: 8 V3.5 security tests and 19 V3.2 regression tests pass.

## 7. Launch V3.6

```powershell
python -m streamlit run app_v3_6.py
```

Log in with your existing account and test creating, running, and resuming a workflow. Verify V3.5 approval behavior is unchanged and that a failed workflow can be resumed after fixing its underlying issue.

## 8. Test the idempotency API directly
After the app modules are copied, you can run a short local test from the project root:

```powershell
python -c "from workflow_orchestrator_v3_6 import create_workflow; from datetime import date; a=create_workflow('INV-DEMO-1','admin',follow_up_date=date.today(),idempotency_key='demo-request-001'); b=create_workflow('INV-DEMO-1','admin',follow_up_date=date.today(),idempotency_key='demo-request-001'); print(a, b, 'SAME=', a==b)"
```

This creates a demo workflow in your local database. Use a clearly labelled test invoice and do not use a real invoice for this check.

## Current limitations / honest status
- This does not yet provide a distributed queue, automatic background retries, or a watchdog process.
- Idempotency is available through the Python `create_workflow()` API, but the current UI does not yet persist a client-generated key across browser retries.
- A process crash after a legacy task/approval write is handled using the existing workflow-marker recovery pattern; comprehensive transactional outbox semantics are not implemented.
- Lease expiry is a recovery mechanism, not proof that a stale process has stopped. Keep the app single-instance for now.
- Do not connect live email sending yet. Email tools should be integrated after reliability tests and a controlled draft-only pilot, with human approval, recipient validation, duplicate-send prevention, and a complete send audit.
