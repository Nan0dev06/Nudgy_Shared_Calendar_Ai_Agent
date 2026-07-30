"""Nudgy backend entrypoint.

Run from the repo root:
    uvicorn app.main:app --reload --app-dir backend

Interactive API docs (for the frontend dev): http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth_routes import router as auth_router
from app.api.chat_routes import router as chat_router
from app.api.event_routes import router as event_router
from app.api.group_routes import router as group_router
from app.api.plan_routes import router as plan_router
from app.api.review_routes import router as review_router
from app.api.share_routes import router as share_router
from app.api.stream_routes import router as stream_router
from app.db.session import init_db
from app.jobs.plan_ticker import start_ticker, stop_ticker
from app.mailer import configure_from_env as configure_mailer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Own the background plan ticker for exactly as long as the app runs —
    started here so a reload/shutdown cancels it instead of leaking a task."""
    ticker = start_ticker()
    try:
        yield
    finally:
        await stop_ticker(ticker)


app = FastAPI(title="Nudgy", description="Agentic group scheduling assistant",
              lifespan=lifespan)
init_db()
configure_mailer()  # SMTP if SMTP_* is set, else the console dev backend

app.include_router(auth_router)
app.include_router(group_router)
app.include_router(event_router)
app.include_router(plan_router)
app.include_router(review_router)
app.include_router(share_router)
app.include_router(stream_router)
app.include_router(chat_router)

# Minimal scaffold frontend (teammate replaces this with the real React app).
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")
