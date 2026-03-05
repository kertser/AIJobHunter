"""Database migrations — placeholder for future Alembic integration."""

from __future__ import annotations


def run_migrations() -> None:
    """Run pending database migrations.

    In v1 we rely on ``repo.init_db`` to create tables via
    ``Base.metadata.create_all``.  This stub will be replaced with Alembic
    once the schema stabilises.
    """
    raise NotImplementedError("Alembic migrations not yet configured")

