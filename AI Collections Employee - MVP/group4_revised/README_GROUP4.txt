GROUP 4 - DATABASE CENTRALISATION

File to replace:
  src\ai_collections_employee_mvp\email_draft_service_v3_8.py

Change:
  - Replaced the module-local collection_workflow.db path with
    `from database_config import DEFAULT_DB_PATH` and `DB_PATH = DEFAULT_DB_PATH`.
  - Preserved the existing _connect() function and module-level DB_PATH override.

Provider service:
  email_provider_service_v3_8.py is intentionally unchanged.
  It imports and uses _connect() from email_draft_service_v3_8.py, so its
  database connections automatically use the centralised path after the
  draft service is updated. No second DB_PATH should be introduced there.

Safety:
  - This change does not enable email sending.
  - Keep EMAIL_SENDING_ENABLED=false.
  - Do not run database migration as part of Group 4.
