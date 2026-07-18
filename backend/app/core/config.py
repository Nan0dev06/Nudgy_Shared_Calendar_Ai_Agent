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
# render.yaml generates one automatically for the blueprint deploy.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

# The dev fallback is published in this file, so anyone could forge a session
# cookie for any user with it. That is only tolerable on localhost: refuse to
# boot with it anywhere reachable, rather than quietly serving a known key.
if SECRET_KEY == "dev-secret-change-me" and DATABASE_URL:
    raise RuntimeError(
        "SECRET_KEY is still the published dev default, but DATABASE_URL is set "
        "(this looks like a real deployment). Session cookies would be forgeable "
        "by anyone. Set SECRET_KEY to a random secret before starting."
    )

GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
)

# --- LLM provider -----------------------------------------------------------
# Which model backend Nudgy talks to. Default is Groq (free tier, fast).
#   groq      -> free cloud, needs GROQ_API_KEY   (recommended for the demo)
#   gemini    -> free cloud, needs GEMINI_API_KEY (huge token limits, no card)
#   ollama    -> free local, no key, run `ollama serve` first
#   openai    -> needs LLM_API_KEY
# All speak the OpenAI API (Gemini via its OpenAI-compatible endpoint), so
# they share one code path.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

# base_url + default model per provider
_OPENAI_COMPAT = {
    "groq":   {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "model": "gemini-2.0-flash"},
    "ollama": {"base_url": "http://localhost:11434/v1",      "model": "llama3.1"},
    "openai": {"base_url": "https://api.openai.com/v1",      "model": "gpt-4o-mini"},
}

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

_cfg = _OPENAI_COMPAT.get(LLM_PROVIDER, _OPENAI_COMPAT["groq"])
LLM_MODEL = os.getenv("NUDGY_MODEL", _cfg["model"])
LLM_BASE_URL = os.getenv("LLM_BASE_URL", _cfg["base_url"])
# ollama ignores the key; the cloud providers need a real one. Pick the key
# that matches the ACTIVE provider — otherwise a leftover key for another
# provider (e.g. GEMINI_API_KEY still set while running on groq) would be sent
# to the wrong service and fail auth. An explicit LLM_API_KEY overrides.
_PROVIDER_KEY = {"groq": GROQ_API_KEY, "gemini": GEMINI_API_KEY}
LLM_API_KEY = os.getenv("LLM_API_KEY") or _PROVIDER_KEY.get(LLM_PROVIDER, "") or "ollama"

# Big-intake guard: if an estimated request would exceed this many input
# tokens, the agent asks the user to narrow the request instead of firing a
# call that the model would reject. 0 disables the check. The default leaves
# generous headroom under Gemini's free tier while still catching runaway
# conversations; lower it (e.g. 10000) when running on Groq's tighter tier.
LLM_MAX_INPUT_TOKENS = int(os.getenv("LLM_MAX_INPUT_TOKENS", "100000"))

# Both scopes requested up front so test accounts consent once and we never
# have to re-run OAuth when Phase 3 starts writing events.
#   calendar.readonly -> freebusy queries + reading locations on own events
#   calendar.events   -> creating the group event after the host locks in a time
OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

# Where Phase 1 CLI scripts store per-account OAuth tokens (gitignored).
# Phase 2 moves these into SQLite.
TOKENS_DIR = ROOT_DIR / "backend" / ".tokens"
