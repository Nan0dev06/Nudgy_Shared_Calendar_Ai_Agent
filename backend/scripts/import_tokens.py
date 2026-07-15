"""One-off: import any backend/.tokens/*.json into the SQLite DB as users.

Bridges the Phase 1 CLI tokens into the Phase 2 database so you don't have to
re-run OAuth. Safe to run repeatedly (upserts by email).

    python backend/scripts/import_tokens.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # -> backend/

from app.auth.google import list_connected_accounts
from app.db.repo import upsert_user_token
from app.db.session import SessionLocal, init_db


def main() -> None:
    init_db()
    token_files = list_connected_accounts()
    if not token_files:
        print("No token files in backend/.tokens/. Run connect_account.py first.")
        return
    session = SessionLocal()
    try:
        for path in token_files:
            email = path.stem
            user = upsert_user_token(session, email, path.read_text())
            print(f"Imported {email} (user id {user.id})")
    finally:
        session.close()


if __name__ == "__main__":
    main()
