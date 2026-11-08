# AI Collections Employee — V3.2 Installation & Test Guide

## Objective
V3.2 introduces an offline deterministic regression suite and a Streamlit evaluation panel. It checks collection-policy outputs and invoice retrieval contracts using synthetic test data. It does not call Gemini, write workflow/task/approval records, send email, or update accounting systems.

## Files
- `app_v3_2.py` — V3.1 app plus the V3.2 evaluation panel.
- `evaluation_service.py` — reusable offline evaluation runner with CSV/JSON-friendly results.
- `test_v3_2_regression.py` — standard-library `unittest` tests.

Keep `app_v3_1.py`, `workflow_orchestrator.py`, `collection_agent.py`, `collection_rules.py`, `follow_up_service.py`, `approval_service.py`, `llm_service.py`, `response_classification_service.py`, and the existing SQLite database unchanged.

## Safe install
1. Stop Streamlit with `Ctrl+C`.
2. Back up `app_v3_1.py` and `collection_workflow.db` while the app is stopped.
3. Put the three V3.2 files in the same project folder as the existing Python modules.
4. Activate the existing virtual environment:

```powershell
cd "C:\Business\AI Collections Employee\Codespace\AI Collections Employee - MVP"
.\.venv\Scripts\Activate.ps1
```

5. Compile:

```powershell
python -m py_compile app_v3_2.py evaluation_service.py test_v3_2_regression.py
```

6. Run the unit tests:

```powershell
python -m unittest -v test_v3_2_regression.py
```

Expected: 19 tests pass. If any test fails, stop and inspect the traceback before using the UI.

7. Launch the app:

```powershell
python -m streamlit run app_v3_2.py
```

8. Open **V3.2 — Agent Evaluation & Regression Testing** and click **Run V3.2 regression suite**. Expected: 18 offline cases pass (14 business-rule cases + 4 invoice-retrieval cases). Download CSV/JSON reports if useful.

## Coverage
- Overdue, undisputed positive balance: reminder draft is eligible, but human review remains required.
- Recorded or classified dispute: standard reminder is blocked.
- Reported payment: payment verification, never automatic settlement.
- Missing invoice, supporting documents, and payment promise: correct review paths.
- Due today/future, zero balance, rule precedence.
- Invalid inputs: blank invoice, negative balance, non-numeric amounts.
- Invoice lookup: unique, missing, duplicate, and blank invoice identifiers.

## Important limitations
- The current suite tests deterministic rules and invoice retrieval; it does not measure live Gemini classification accuracy or end-to-end workflow reliability.
- A `Payment date requested` or `No clear response` category falls through to invoice-based rules; an overdue undisputed invoice may therefore be reminder-draft eligible. Human review is still mandatory. Review whether that policy is appropriate for your business before pilot use.
- Passing tests is not a production-readiness certification. Next improvements should add mocked orchestration failure/recovery tests, classification evaluation with a labelled dataset, idempotency/concurrency tests, and authenticated reviewer permissions.
- Use synthetic or authorized data only. Never place API keys in source files or share them in chat.
