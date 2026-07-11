"""Optuna storage URL scoping (schema isolation in the shared Postgres)."""

from carcharoth.services.optuna.storage import scoped_storage_url


def test_postgres_url_gets_search_path_option() -> None:
    url = "postgresql+psycopg://user:pw@localhost:5432/carcharoth"
    assert scoped_storage_url(url) == f"{url}?options=-csearch_path%3Doptuna"


def test_postgres_url_with_existing_query_appends() -> None:
    url = "postgresql+psycopg://user:pw@localhost:5432/carcharoth?sslmode=require"
    assert scoped_storage_url(url) == f"{url}&options=-csearch_path%3Doptuna"


def test_already_scoped_url_unchanged() -> None:
    url = "postgresql+psycopg://u:p@h/db?options=-csearch_path%3Doptuna"
    assert scoped_storage_url(url) == url


def test_sqlite_url_passes_through() -> None:
    assert scoped_storage_url("sqlite:///optuna.db") == "sqlite:///optuna.db"
