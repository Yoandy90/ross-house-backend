"""
Phase 1 — Global pytest guard: tests must NEVER touch a production database.

This conftest imports BEFORE any test module, so:
  1. ENVIRONMENT is forced to "test".
  2. MONGO_URL is forced to localhost (legacy tests that _load_env() with
     os.environ.setdefault can no longer override it with the Atlas URL).
  3. assert_not_production_database() aborts the run if anything still looks
     like a production/hosted database.
"""
import os

PROD_DB_MARKERS = ("mongodb.net", "mongodb+srv", "railway", "atlas")

os.environ["ENVIRONMENT"] = "test"
_current = os.environ.get("MONGO_URL", "")
if not _current or any(m in _current for m in PROD_DB_MARKERS):
    os.environ["MONGO_URL"] = "mongodb://localhost:27017"
if not os.environ.get("DB_NAME") or os.environ.get("DB_NAME") in ("rossapp", "ross_house", "production"):
    os.environ["DB_NAME"] = "rhr_test_db"


def assert_not_production_database(url: str | None = None,
                                   db_name: str | None = None) -> None:
    """Abort loudly if the configured DB looks like production.
    Never prints the connection string itself."""
    url = url if url is not None else os.environ.get("MONGO_URL", "")
    db_name = db_name if db_name is not None else os.environ.get("DB_NAME", "")
    if any(m in (url or "") for m in PROD_DB_MARKERS):
        raise RuntimeError(
            "SAFETY ABORT: MONGO_URL apunta a una base de datos hosteada/"
            "producción. Los tests solo pueden correr contra localhost.")
    if db_name in ("rossapp", "ross_house", "production"):
        raise RuntimeError(
            f"SAFETY ABORT: DB_NAME '{db_name}' está marcado como producción.")


assert_not_production_database()
