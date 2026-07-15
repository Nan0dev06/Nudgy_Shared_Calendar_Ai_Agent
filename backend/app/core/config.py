"""Central config: loads .env from the repo root and exposes settings.

Every other module reads settings from here — nothing else touches
os.environ directly, so there is exactly one place to debug env issues.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# repo root = three levels up from backend/app/core/config.py
ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Database: Postgres when DATABASE_URL is set (Render injects it), else a local
# SQLite file. Set on the server so connected accounts survive restarts — the
# free tier wipes the local filesystem, so SQLite would lose data there.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Signs the session cookie. Any random string; regenerating it just logs
# everyone out. Set SECRET_KEY in .env for stable sessions across restarts.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
)

# --- LLM provider -----------------------------------------------------------
# Which model backend Orbi talks to. Default is Groq (free tier, fast).
#   groq      -> free cloud, needs GROQ_API_KEY   (recommended for the demo)
#   ollama    -> free local, no key, run `ollama serve` first
#   openai    -> needs LLM_API_KEY
# All three speak the OpenAI API, so they share one code path.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

# base_url + default model per provider
_OPENAI_COMPAT = {
    "groq":   {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    "ollama": {"base_url": "http://localhost:11434/v1",      "model": "llama3.1"},
    "openai": {"base_url": "https://api.openai.com/v1",      "model": "gpt-4o-mini"},
}

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

_cfg = _OPENAI_COMPAT.get(LLM_PROVIDER, _OPENAI_COMPAT["groq"])
LLM_MODEL = os.getenv("ORBI_MODEL", _cfg["model"])
LLM_BASE_URL = os.getenv("LLM_BASE_URL", _cfg["base_url"])
# ollama ignores the key; groq/openai need a real one
LLM_API_KEY = os.getenv("LLM_API_KEY") or GROQ_API_KEY or "ollama"

# Both scopes requested up front so test accounts consent once and we never
# have to re-run OAuth when Phase 3 starts writing events.
#   calendar.readonly -> freebusy queries + reading locations on own events
#   calendar.events   -> creating the group event after an approved poll
OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

# Where Phase 1 CLI scripts store per-account OAuth tokens (gitignored).
# Phase 2 moves these into SQLite.
TOKENS_DIR = ROOT_DIR / "backend" / ".tokens"

# Shared secret for the Google Calendar Add-on (backend/addon/). The Add-on
# runs server-side inside Apps Script (never in a user's browser), so a
# static shared secret in a header is enough to keep /addon/chat from being
# called by randoms — it is NOT a substitute for real per-user auth, which is
# why the endpoint still requires the caller to already be a connected Orbi
# user looked up by email.
ADDON_SHARED_SECRET = os.getenv("ADDON_SHARED_SECRET", "")
