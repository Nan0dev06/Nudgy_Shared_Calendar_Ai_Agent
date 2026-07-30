"""Prove the app can reach its database, and create/upgrade the schema.

    python backend/scripts/check_db.py

Reads DATABASE_URL from .env exactly like the app does, so this answers "will
the deploy come up?" without deploying. With DATABASE_URL unset it checks the
local SQLite file instead, which is a useful no-op sanity run.

Run it once after pasting a Neon connection string into .env: it connects,
applies create_all + the in-place column/index migrations, and prints a row
count per table. Safe to re-run — every step is idempotent, and nothing here
deletes or overwrites data.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text  # noqa: E402

from app.core.config import DATABASE_URL  # noqa: E402
from app.db.session import engine, init_db  # noqa: E402


def describe(url) -> str:
    """A URL you can paste into a chat: everything but the password."""
    if url.password:
        url = url.set(password="***")
    return url.render_as_string(hide_password=False)


def main() -> int:
    print(f"backend : {'Postgres' if DATABASE_URL else 'SQLite (no DATABASE_URL set)'}")
    print(f"url     : {describe(engine.url)}")

    probe = "SELECT sqlite_version()" if engine.dialect.name == "sqlite" else "SELECT version()"
    try:
        with engine.connect() as conn:
            version = conn.execute(text(probe)).scalar()
    except Exception as exc:
        print(f"\nFAILED to connect: {type(exc).__name__}")
        print(f"  {exc}")
        print("\nCheck: the URL is the full connection string, the host is "
              "reachable, and (Neon/Supabase) the project is not deleted.")
        return 1
    print(f"server  : {str(version).split(' on ')[0]}")

    print("\napplying schema (create_all + late columns/indexes)...")
    init_db()

    insp = inspect(engine)
    tables = sorted(insp.get_table_names())
    print(f"tables  : {len(tables)}")
    with engine.connect() as conn:
        for name in tables:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            print(f"  {name:<22} {count:>6} rows")
    print("\nOK - the app can read and write this database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
