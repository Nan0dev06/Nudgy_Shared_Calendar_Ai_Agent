"""Orbi backend entrypoint.

Run from the repo root:
    uvicorn app.main:app --reload --app-dir backend

Interactive API docs (for the frontend dev): http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.auth_routes import router as auth_router
from app.api.chat_routes import router as chat_router
from app.api.event_routes import router as event_router
from app.api.group_routes import router as group_router
from app.api.poll_routes import router as poll_router
from app.db.session import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="Orbi", description="Agentic group scheduling assistant")
init_db()

app.include_router(auth_router)
app.include_router(group_router)
app.include_router(poll_router)
app.include_router(event_router)
app.include_router(chat_router)

# Minimal scaffold frontend (teammate replaces this with the real React app).
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")
