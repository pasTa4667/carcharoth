"""Optuna storage preparation: an own schema inside the shared Postgres.

Optuna's RDBStorage manages its schema with its own Alembic, using the
default ``alembic_version`` table — the same name carcharoth's migrations
use. Sharing one schema therefore breaks both sides, so Optuna gets a
dedicated ``optuna`` schema, selected via the connection's search_path.
The returned URL is self-contained: the same string works for
optuna-dashboard.
"""

from sqlalchemy import create_engine, text

OPTUNA_SCHEMA = "optuna"


def scoped_storage_url(url: str) -> str:
    """PostgreSQL URLs scoped to the optuna schema; other backends (e.g.
    sqlite) and already-scoped URLs pass through unchanged."""
    if not url.startswith("postgresql") or "search_path" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options=-csearch_path%3D{OPTUNA_SCHEMA}"


def prepare_storage_url(url: str) -> str:
    """Ensure the optuna schema exists, then return the scoped URL."""
    scoped = scoped_storage_url(url)
    if scoped != url:
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {OPTUNA_SCHEMA}"))
        finally:
            engine.dispose()
    return scoped
