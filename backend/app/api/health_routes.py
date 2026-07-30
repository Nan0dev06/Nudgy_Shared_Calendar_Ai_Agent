"""Liveness + readiness probes.

Two endpoints on purpose, because they cost different things:

- `GET /healthz` answers from the process alone. This is the one an uptime
  pinger hits every few minutes to keep the free web service from sleeping —
  it must NOT touch the database, or the ping alone would hold serverless
  Postgres (Neon) awake around the clock and eat the free compute-hour budget.
- `GET /readyz` actually round-trips the database. Use it after a deploy or a
  DATABASE_URL change to prove the app can reach its data; 503 when it can't.

Both are unauthenticated and deliberately say nothing about the internals: a
failed readiness check reports that the database is unreachable, never the
driver error (which quotes the connection string, host included).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz", include_in_schema=False)
def healthz():
    """Process is up and serving. No I/O."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
def readyz(session: Session = Depends(get_session)):
    """Up AND the database answers."""
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        log.exception("Readiness check failed: database unreachable")
        return JSONResponse(
            status_code=503, content={"status": "error", "database": "unreachable"}
        )
    return {"status": "ok", "database": "ok"}
