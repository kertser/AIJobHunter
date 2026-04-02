"""Database migrations — lightweight ALTER TABLE helpers.

In v1 we rely on ``repo.init_db`` to create tables via
``Base.metadata.create_all``.  These helpers patch up columns that were
added to ORM models *after* the table already existed on disk.

Each migration is idempotent: it checks ``PRAGMA table_info`` before
issuing ``ALTER TABLE``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger("job_hunter.db.migrations")


def _table_columns(engine: Engine, table: str) -> set[str]:
    """Return the set of column names currently present in *table*."""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})")
        return {row[1] for row in rows}


def _add_column_if_missing(
    engine: Engine,
    table: str,
    column: str,
    col_type: str = "CHAR(32)",
    *,
    nullable: bool = True,
) -> bool:
    """Add *column* to *table* when it doesn't exist yet.  Returns True if added."""
    existing = _table_columns(engine, table)
    if column in existing:
        return False
    null_clause = "" if nullable else " NOT NULL DEFAULT ''"
    ddl = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{null_clause}"
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)
    logger.info("migration: added column %s.%s (%s)", table, column, col_type)
    return True


def _create_index_if_missing(engine: Engine, index_name: str, table: str, column: str) -> bool:
    """Create an index if it doesn't already exist.  Returns True if created."""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            (index_name,),
        )
        if rows.fetchone() is not None:
            return False
    ddl = f"CREATE INDEX {index_name} ON {table} ({column})"
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)
    logger.info("migration: created index %s on %s(%s)", index_name, table, column)
    return True


# ---------------------------------------------------------------------------
# Public API — called from init_db()
# ---------------------------------------------------------------------------

def run_migrations(engine: Engine) -> None:
    """Apply all pending schema migrations (idempotent)."""
    changes = 0

    # ── v2: add user_id to jobs, scores, application_attempts ──
    for table, ix_name in [
        ("jobs", "ix_jobs_user_id"),
        ("scores", "ix_scores_user_id"),
        ("application_attempts", "ix_application_attempts_user_id"),
    ]:
        if _add_column_if_missing(engine, table, "user_id", "CHAR(32)"):
            changes += 1
        if _create_index_if_missing(engine, ix_name, table, "user_id"):
            changes += 1


    # ── v3: add per-user settings columns to users ──
    _users_columns: list[tuple[str, str]] = [
        ("openai_api_key", "TEXT"),
        ("llm_provider", "VARCHAR(50)"),
        ("local_llm_url", "VARCHAR(500)"),
        ("local_llm_model", "VARCHAR(200)"),
        ("llm_temperature", "FLOAT"),
        ("llm_max_tokens", "INTEGER"),
        ("mock", "BOOLEAN"),
        ("dry_run", "BOOLEAN"),
        ("headless", "BOOLEAN"),
        ("slowmo_ms", "INTEGER"),
        ("email_provider", "VARCHAR(50)"),
        ("resend_api_key", "TEXT"),
        ("smtp_host", "VARCHAR(255)"),
        ("smtp_port", "INTEGER"),
        ("smtp_user", "VARCHAR(255)"),
        ("smtp_password", "TEXT"),
        ("smtp_use_tls", "BOOLEAN"),
        ("notification_email", "VARCHAR(320)"),
        ("notifications_enabled", "BOOLEAN"),
    ]
    for col_name, col_type in _users_columns:
        if _add_column_if_missing(engine, "users", col_name, col_type):
            changes += 1

    # ── v4: add description_formatted flag to jobs ──
    if _add_column_if_missing(engine, "jobs", "description_formatted", "BOOLEAN"):
        changes += 1

    if changes:
        logger.info("migration: applied %d change(s)", changes)
    else:
        logger.debug("migration: schema is up-to-date")

