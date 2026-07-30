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

# Encrypts OAuth tokens at rest (see core/crypto.py). A urlsafe-base64 32-byte
# Fernet key. Unset -> derived from SECRET_KEY, which is fine for local dev; in
# production set a dedicated key so token secrecy doesn't ride on the cookie key.
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")

GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback"
)

# Public origin of the app, used to build links in transactional email
# (verification, magic-link, password reset). Same origin the frontend is served
# from; override in production to the real domain.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")

# --- Microsoft / Outlook OAuth (Azure app registration) ----------------------
# Optional: unset -> Microsoft sign-in + Outlook calendars are simply unavailable
# (auth/microsoft.py raises a clear error if invoked without them). "common"
# tenant lets BOTH personal (Outlook.com) and work/school accounts sign in.
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")
MS_TENANT = os.getenv("MS_TENANT", "common")
MS_REDIRECT_URI = os.getenv(
    "MS_REDIRECT_URI", "http://localhost:8000/auth/microsoft/callback"
)
# Delegated Graph scopes. offline_access -> refresh_token; Calendars.ReadWrite ->
# read busy time (calendarView) + write events; User.Read -> the account email;
# openid/profile/email -> identity. Space-joined at request time.
MS_SCOPES = [
    "offline_access",
    "openid",
    "profile",
    "email",
    "User.Read",
    "Calendars.ReadWrite",
]

# --- LLM provider -----------------------------------------------------------
# Which model backend Nudgy talks to. Default is Groq (free tier, fast).
#   groq      -> free cloud, needs GROQ_API_KEY   (recommended for the demo)
#   ollama    -> free local, no key, run `ollama serve` first
#   openai    -> needs LLM_API_KEY
# All speak the OpenAI API, so they share one code path.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()

# base_url + default model per provider
_OPENAI_COMPAT = {
    "groq":   {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.1-8b-instant"},
    "ollama": {"base_url": "http://localhost:11434/v1",      "model": "llama3.1"},
    "openai": {"base_url": "https://api.openai.com/v1",      "model": "gpt-4o-mini"},
}

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

_cfg = _OPENAI_COMPAT.get(LLM_PROVIDER, _OPENAI_COMPAT["groq"])
LLM_MODEL = os.getenv("NUDGY_MODEL", _cfg["model"])
LLM_BASE_URL = os.getenv("LLM_BASE_URL", _cfg["base_url"])
# ollama ignores the key; groq/openai need a real one. An explicit LLM_API_KEY
# overrides, else fall back to the provider-specific key.
LLM_API_KEY = os.getenv("LLM_API_KEY") or GROQ_API_KEY or "ollama"

# Short-TTL display cache for member busy ranges (see calendars/cache.py). A
# person's free/busy doesn't change second-to-second, so caching it briefly cuts
# redundant calendar-API calls on group loads / the 5s poll / multi-step agent
# runs, and stays inside free API quotas. 90s sits in the v1-decided 60–120s
# band. This is a DISPLAY cache only — booking always reads live.
FREEBUSY_CACHE_TTL_SECONDS = float(os.getenv("FREEBUSY_CACHE_TTL_SECONDS", "90"))

# --- async plan convergence (jobs/plan_ticker.py) ---------------------------
# How often the background job looks for plans whose deadline has passed or
# whose non-voters need a nudge. A minute is plenty: deadlines are set in hours,
# and every tick is a handful of cheap queries over OPEN plans only.
PLAN_TICK_SECONDS = float(os.getenv("PLAN_TICK_SECONDS", "60"))

# Kill switch for that job. Off in tests (they call run_tick directly with a
# frozen clock) and useful if the app is ever run as several processes, where a
# ticker per process would nudge people once per process.
PLAN_TICKER_ENABLED = os.getenv("PLAN_TICKER_ENABLED", "1").lower() not in (
    "0", "false", "no",
)

# Minimum gap between nudges to the same plan's non-voters. 12h means a plan
# that sits unanswered for a day pings you twice — enough to converge, not
# enough to feel like spam. Short-fused plans get one earlier nudge instead
# (see tools/plan_deadlines.next_reminder_at).
PLAN_REMINDER_INTERVAL_SECONDS = float(
    os.getenv("PLAN_REMINDER_INTERVAL_SECONDS", str(12 * 3600))
)

# --- live updates / SSE (api/stream_routes.py) ------------------------------
# How long a quiet stream waits before sending a heartbeat comment. Idle
# connections are dropped by proxies (and phone radios) after ~60s of silence,
# so this has to stay comfortably under that; it is also how long a client can
# take to notice the server went away.
SSE_HEARTBEAT_SECONDS = float(os.getenv("SSE_HEARTBEAT_SECONDS", "20"))

# Ceiling on concurrent open streams in this process. Each one is cheap (an
# idle coroutine, no DB session held), but a runaway client reconnect-storm
# shouldn't be able to pin the worker. Over the cap the endpoint returns 503 and
# the frontend falls back to polling, which is exactly the old behaviour.
SSE_MAX_CONNECTIONS = int(os.getenv("SSE_MAX_CONNECTIONS", "500"))

# Big-intake guard: if an estimated request would exceed this many input
# tokens, the agent asks the user to narrow the request instead of firing a
# call that the model would reject. 0 disables the check. Sized to catch
# runaway conversations while leaving normal use untouched.
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
