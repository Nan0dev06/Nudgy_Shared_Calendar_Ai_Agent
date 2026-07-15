"""Google OAuth 2.0: run the consent flow, persist tokens, refresh on expiry.

Token lifecycle (the part that usually silently breaks):
- Google access tokens live ~1 hour. The refresh_token (granted because we
  request access_type="offline" + prompt="consent") lets us mint new ones
  without user interaction.
- load_credentials() transparently refreshes an expired token and writes the
  refreshed token back to disk, so a token file connected on Monday still
  works at the Friday demo.
"""
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow
from googleapiclient.discovery import build

from app.core.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    OAUTH_SCOPES,
    TOKENS_DIR,
)

# Must be registered as an authorized redirect URI on the OAuth client.
CLI_REDIRECT_PORT = 8765


def _client_config() -> dict:
    """OAuth client config assembled from .env (never from a committed file)."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise RuntimeError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET missing. "
            "Copy .env.example to .env and fill them in."
        )
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"http://localhost:{CLI_REDIRECT_PORT}/"],
        }
    }


def run_oauth_flow() -> Credentials:
    """Open a browser, let a test account consent, return its credentials.

    Spins up a temporary local HTTP server on CLI_REDIRECT_PORT to catch
    Google's redirect. access_type=offline + prompt=consent forces Google to
    issue a refresh_token every time (otherwise re-consenting returns none).
    """
    flow = InstalledAppFlow.from_client_config(_client_config(), scopes=OAUTH_SCOPES)
    return flow.run_local_server(
        port=CLI_REDIRECT_PORT, access_type="offline", prompt="consent"
    )


def build_web_flow(redirect_uri: str) -> Flow:
    """OAuth flow for the web app: we redirect the user to Google, Google
    redirects back to `redirect_uri` with a code, we exchange it for tokens."""
    flow = Flow.from_client_config(_client_config(), scopes=OAUTH_SCOPES)
    flow.redirect_uri = redirect_uri
    return flow


def get_account_email(creds: Credentials) -> str:
    """The primary calendar's id IS the account email — no extra scope needed."""
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return service.calendars().get(calendarId="primary").execute()["id"]


def token_path(email: str) -> Path:
    return TOKENS_DIR / f"{email}.json"


def save_credentials(email: str, creds: Credentials) -> Path:
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    path = token_path(email)
    path.write_text(creds.to_json())
    return path


def load_credentials(path: Path) -> Credentials:
    """Load a stored token; if expired, refresh it and persist the new token."""
    creds = Credentials.from_authorized_user_file(str(path), OAUTH_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds


def credentials_from_json(token_json: str) -> tuple[Credentials, str | None]:
    """Build Credentials from a stored JSON string (the DB path).

    Returns (creds, refreshed_json). If the token was expired and we refreshed
    it, refreshed_json is the new JSON to persist back to the DB; otherwise it
    is None. This is how OAuth token refresh stays non-silent in the web app:
    the caller writes refreshed_json back whenever it isn't None.
    """
    import json

    creds = Credentials.from_authorized_user_info(json.loads(token_json), OAUTH_SCOPES)
    refreshed_json = None
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        refreshed_json = creds.to_json()
    return creds, refreshed_json


def list_connected_accounts() -> list[Path]:
    """All token files saved by connect_account.py, sorted for stable output."""
    if not TOKENS_DIR.exists():
        return []
    return sorted(TOKENS_DIR.glob("*.json"))
