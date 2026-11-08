# AI Collections Employee V2 — Code Study Guide

## Files and responsibilities

- `app.py`: Streamlit user interface, invoice processing, dashboard metrics, reminder workflow, and activity-history UI.
- `llm_service.py`: Gemini API call and Pydantic validation for the structured reminder. Keep API logic here rather than inside UI code.
- `sample_invoices.csv`: synthetic/demo invoice data used when no upload is supplied.
- `collection_activity.db`: SQLite database created at runtime; stores activity events locally.
- `.venv`: isolated Python environment for this project.

## How the application runs

1. **Imports and configuration** — imports Python and third-party packages; sets required CSV/XLSX columns and paths relative to the app file.
2. **SQLite helpers** — `init_activity_db()` creates the activity table if needed; `record_activity()` inserts an event; `load_activity()` reads recent events.
3. **Invoice processing** — `process_invoices()` normalizes column names, parses dates and money values, rejects invalid/duplicate invoice rows, and calculates days overdue, status, aging bucket, and priority.
4. **File loading** — user can upload CSV/XLSX or click to load the bundled sample CSV.
5. **Dashboard** — derives totals and renders tables/charts using Pandas, Plotly, and Streamlit.
6. **Dispute gate** — invoices with outstanding balance, overdue days, and `Review` priority are shown separately. Only overdue invoices without that review flag are offered for reminder generation.
7. **AI reminder** — the app calls `generate_payment_reminder(...)` from `llm_service.py`; the returned dictionary is stored in Streamlit session state.
8. **Review and editing** — subject and body are editable; the recommendation and review warning are shown.
9. **Activity log** — generation, saving, and review actions are inserted into SQLite. Activity history can be exported to CSV.

## Important functions

### `process_invoices(data)`
Input: a Pandas DataFrame loaded from CSV/XLSX.
Output: a validated DataFrame with derived columns including `days_overdue`, `invoice_status`, `aging_bucket`, and `priority`.

Why it matters: the invoice data and business rules are the source of truth. An LLM should not calculate balances or decide whether an invoice is paid.

### `init_activity_db()`
Creates `collection_activity` if it does not already exist. The table stores timestamps, invoice/customer identifiers, event type/status, draft content, tone, recommendation, follow-up date, and notes.

### `record_activity(...)`
Writes one event. For example, generating a draft and saving a draft are separate events so the history shows what happened.

### `load_activity()`
Reads the latest 500 rows. This cap is for prototype usability; a production system would need retention, pagination, backup, authorization, and stronger audit controls.

### `generate_payment_reminder(...)`
Imported from `llm_service.py`. It sends invoice facts and tone to Gemini and validates the response using Pydantic. The UI should treat the result as a draft, not as a verified financial decision.

## Important concepts to understand

- **Streamlit reruns:** Streamlit executes the script again after many interactions. `st.session_state` preserves in-progress draft fields between reruns.
- **SQLite persistence:** the database survives app restarts because records are stored on disk, unlike ordinary Python variables.
- **Deterministic rules:** normal Python code decides invoice status and whether a dispute blocks reminders.
- **Structured output:** the LLM returns named fields (`subject`, `body`, `tone`, `recommended_action`, `requires_human_review`) instead of unstructured prose.
- **Human-in-the-loop:** a person must review content before it is used. The prototype's “mark reviewed” action records a user action but is not authenticated approval.
- **No email sending:** neither generating, saving, nor marking reviewed sends an email.

## Run it

From PowerShell in the project root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m streamlit run src/ai_collections_employee_mvp/app.py
```

The `app.py`, `llm_service.py`, and `sample_invoices.csv` files should be in the same package directory shown in the command.

## Suggested learning order

1. Read imports and configuration.
2. Understand `process_invoices()` and test it with a tiny DataFrame.
3. Read the SQLite helper functions.
4. Follow the UI from file upload through KPI metrics.
5. Follow the selected-invoice flow into `generate_payment_reminder()`.
6. Trace how `st.session_state` preserves the generated and edited draft.
7. Inspect how each button writes a separate event to SQLite.
8. Make one small change at a time and rerun the app.

## Prototype limitations before real customer use

- Local SQLite is not multi-user storage and is not a compliant audit system by itself.
- No login, roles, tenant isolation, encryption-at-rest strategy, or access audit is implemented here.
- No email is sent and no accounting/ERP records are updated.
- AI output can still contain incorrect wording; review every draft.
- Use synthetic data until privacy, vendor terms, and security controls are reviewed.
