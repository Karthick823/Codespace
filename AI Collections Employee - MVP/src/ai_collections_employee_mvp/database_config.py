"""Central storage configuration for AI Collections Employee."""

from pathlib import Path

SECURE_ROOT = Path(
    r"C:\ProgramData\AICollectionsEmployee"
)

DATA_DIR = SECURE_ROOT / "data"
BACKUP_DIR = SECURE_ROOT / "backups"
AUDIT_DIR = SECURE_ROOT / "audit"

DATABASE_FILENAME = "collection_workflow.db"


def get_database_path() -> Path:
    """Return the protected application database path.

    No fallback to the source directory is permitted.
    Directory creation or permission errors are allowed to surface.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / DATABASE_FILENAME


def get_backup_directory() -> Path:
    """Return the protected database backup directory."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR


def get_audit_directory() -> Path:
    """Return the protected audit directory."""
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    return AUDIT_DIR


DEFAULT_DB_PATH = get_database_path()
