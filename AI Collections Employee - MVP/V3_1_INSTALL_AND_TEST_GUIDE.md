# V3.1 — Multi-step Workflow Orchestration

## Files
- `app_v3_1.py`: UI for creating, inspecting, and resuming workflows.
- `workflow_orchestrator.py`: persisted workflow engine and step checkpoints.

## What changed
- Every workflow receives a UUID.
- Workflow run and step statuses persist in `collection_workflow.db`.
- Completed steps are reused when resuming a failed workflow.
- Failures are recorded with the step name and message.
- A unique workflow marker is written into task/approval records to help recover if a process stops between a side effect and its checkpoint.
- All financial decisions remain based on deterministic collection rules; customer-response classification is optional.
- No email, ERP, accounting, payment, or invoice status action is executed.

## Install
Place both files beside the existing V3.0 modules in:
`C:\Business\AI Collections Employee\Codespace\AI Collections Employee - MVP`

Back up `app_v3_0.py` and `collection_workflow.db` before testing. Do not delete the database. Activate the existing `.venv`, then run:

```powershell
python -m py_compile app_v3_1.py workflow_orchestrator.py
python -m streamlit run app_v3_1.py
```

## Tests
1. Create a workflow for an overdue invoice without a customer response. Expect all five steps completed, one follow-up task, and one pending approval.
2. Create a workflow for a disputed invoice. Expect `Dispute review` and `reminder_allowed = false`.
3. Provide a customer response reporting payment. Expect payment verification; no invoice status changes.
4. Provide a customer response saying the invoice is missing. Expect invoice resend review.
5. Restart Streamlit and confirm workflow runs and step history remain visible.
6. Resume a completed workflow. It should return the stored result without repeating completed steps.
7. To test recovery, temporarily remove `GEMINI_API_KEY` and submit a workflow with a customer response. It should fail at classification while preserving earlier completed steps. Restore the key and resume; it should continue from classification. Do not use a real customer message for this test.
8. Confirm each completed workflow has a task ID and approval request ID, and the records include the workflow UUID.
9. Confirm no email or external accounting action occurs.

## Important limitations
- Workflow customer response text is stored locally in SQLite to enable resumption. Use synthetic/authorized data only and protect the database.
- Reviewer names are not authenticated and role-based access control is not implemented.
- Local SQLite is appropriate for a single-user prototype, not concurrent production workers.
- Recovery logic helps with common interruptions, but this is not a distributed transaction or production-grade exactly-once execution guarantee.
- If the dataset changes between initial execution and resume, the invoice is retrieved from the currently loaded dataset. Confirm invoice identity before resuming.
