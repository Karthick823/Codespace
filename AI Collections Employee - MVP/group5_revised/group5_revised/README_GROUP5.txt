Group 5 — db_operations.py centralisation

Changes:
- DB_PATH now imports DEFAULT_DB_PATH from database_config.
- BACKUP_DIR now comes from get_backup_directory() in database_config.
- SQLite online backup API and integrity verification are preserved.
- Module-level DB_PATH and BACKUP_DIR remain available for test overrides.
- No database is migrated, copied, deleted, or modified by importing this module.

Install only db_operations.py into src\ai_collections_employee_mvp after taking a backup.
Keep EMAIL_SENDING_ENABLED=false.
