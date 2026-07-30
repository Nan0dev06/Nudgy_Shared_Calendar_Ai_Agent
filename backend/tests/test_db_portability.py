"""The schema has only ever run against SQLite locally; production is Postgres.

These tests close the gap without needing a Postgres server: SQLAlchemy can
render every CREATE TABLE against the real Postgres dialect offline, which
catches a column type that has no Postgres spelling, and the in-place migration
lists can be checked against the models they are supposed to mirror.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_mock_engine

from app.db.models import Base
from app.db.session import (
    _LATE_COLUMNS,
    _LATE_INDEXES,
    _LATE_UNIQUE_INDEXES,
    normalize_db_url,
)


def _render_ddl(dialect_url: str) -> list[str]:
    """Every DDL statement create_all would emit for a dialect, as text."""
    statements: list[str] = []

    def collect(sql, *args, **kwargs):
        statements.append(str(sql.compile(dialect=engine.dialect)))

    engine = create_mock_engine(dialect_url, collect)
    Base.metadata.create_all(engine, checkfirst=False)
    return statements


def test_schema_renders_on_postgres():
    """Nothing in the models is SQLite-only."""
    ddl = "\n".join(_render_ddl("postgresql://"))
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table} " in ddl, f"{table} missing from Postgres DDL"
    # DateTime(timezone=True) must survive as a real timestamptz — this is what
    # keeps the app's aware-UTC datetimes from silently losing their offset.
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "DATETIME" not in ddl  # SQLite's spelling


def test_late_columns_match_the_models():
    """A late column that no longer exists on its model would ALTER a table into
    a shape the ORM does not know about, and the mismatch would only show on a
    fresh Postgres deploy."""
    for table, column, ddl in _LATE_COLUMNS:
        assert table in Base.metadata.tables, f"unknown table {table!r}"
        assert column in Base.metadata.tables[table].c, f"{table}.{column} not on the model"
        assert ddl.strip(), f"{table}.{column} has empty DDL"


@pytest.mark.parametrize("name,table,column", _LATE_INDEXES + _LATE_UNIQUE_INDEXES)
def test_late_indexes_reference_real_columns(name, table, column):
    assert table in Base.metadata.tables, f"{name}: unknown table {table!r}"
    assert column in Base.metadata.tables[table].c, f"{name}: no column {column!r}"


class TestNormalizeDbUrl:
    def test_legacy_scheme_is_upgraded(self):
        url = normalize_db_url("postgres://u:p@host/db")
        assert url.startswith("postgresql://")

    def test_tls_is_required_when_unspecified(self):
        """libpq's default (`prefer`) silently accepts an unencrypted
        connection; the app's calendar tokens must not ride on that."""
        assert "sslmode=require" in normalize_db_url("postgresql://u:p@host/db")

    def test_an_explicit_sslmode_is_left_alone(self):
        url = normalize_db_url("postgresql://u:p@host/db?sslmode=verify-full")
        assert "sslmode=verify-full" in url
        assert "require" not in url

    def test_other_query_params_survive(self):
        url = normalize_db_url("postgresql://u:p@host/db?channel_binding=require")
        assert "channel_binding=require" in url
        assert "sslmode=require" in url

    def test_sqlite_urls_are_untouched(self):
        assert normalize_db_url("sqlite:///nudgy.db") == "sqlite:///nudgy.db"

    def test_surrounding_whitespace_from_a_paste_is_trimmed(self):
        assert normalize_db_url("  postgres://u:p@host/db\n").startswith("postgresql://")
