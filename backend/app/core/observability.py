"""Error tracking (Sentry), off by default.

Nudgy is about to meet real testers, and a 500 that only exists in a log file
on a sleeping free-tier host is a 500 nobody fixes. This wires Sentry the same
way the mailer is wired: one env var (SENTRY_DSN) decides whether the real
backend or a no-op is used, and no caller changes either way.

The interesting part is `scrub_event`. Nudgy's URLs carry credentials —
`/share/<token>/…` is a bearer link, the OAuth callback carries an
authorization `code`, and the SPA gets password-reset tokens as `?token=`. An
error report is a copy of a request, so shipping one unmodified would post
those to a third party. Everything below is therefore stripped BEFORE an event
leaves the process, and the scrubber is a plain function so it can be tested
without the SDK installed at all.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.core.config import (
    SENTRY_DSN,
    SENTRY_ENVIRONMENT,
    SENTRY_TRACES_SAMPLE_RATE,
)

log = logging.getLogger(__name__)

# Request headers that are pure credential.
_DROP_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization"}

# Path prefixes whose NEXT segment is a secret (see api/share_routes.py).
_SECRET_PATH_PREFIXES = ("/share/",)

REDACTED = "[redacted]"


def scrub_url(url: str | None) -> str | None:
    """Drop the query string and any in-path bearer token from a URL.

    The query string goes wholesale rather than key-by-key: `token`, `code`,
    `state` and `share` all appear in this app, an allowlist would need editing
    every time a route is added, and the debugging value of a query string is
    small next to the cost of leaking one.
    """
    if not url:
        return url
    parts = urlsplit(url)
    path = parts.path
    for prefix in _SECRET_PATH_PREFIXES:
        if path.startswith(prefix):
            rest = path[len(prefix):].split("/", 1)
            rest[0] = REDACTED
            path = prefix + "/".join(rest)
            break
    query = REDACTED if parts.query else ""
    return urlunsplit(parts._replace(path=path, query=query, fragment=""))


def scrub_event(event: dict[str, Any], hint: Any = None) -> dict[str, Any]:
    """Sentry `before_send`: strip credentials and request bodies from an event.

    Request bodies are dropped entirely — /auth/login and /auth/register post
    plaintext passwords, and no error report needs them.
    """
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("data", None)
        request["query_string"] = ""
        if request.get("url"):
            request["url"] = scrub_url(request["url"])
        headers = request.get("headers")
        if isinstance(headers, dict):
            for name in [k for k in headers if k.lower() in _DROP_HEADERS]:
                headers[name] = REDACTED

    # Breadcrumbs record the outbound calls we make (Google, Groq, Graph); their
    # URLs get the same treatment so a token in a query string can't ride along.
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    if isinstance(values, list):
        for crumb in values:
            data = crumb.get("data") if isinstance(crumb, dict) else None
            if isinstance(data, dict) and data.get("url"):
                data["url"] = scrub_url(data["url"])

    return event


def init_sentry() -> bool:
    """Turn on error tracking if a DSN is configured. Returns whether it ran.

    Never raises: a broken observability setup must not stop the app from
    serving. A DSN set without the package installed is logged loudly, because
    that combination means someone thinks errors are being tracked and they
    are not.
    """
    if not SENTRY_DSN:
        log.info("Sentry disabled (no SENTRY_DSN)")
        return False
    try:
        import sentry_sdk
    except ImportError:
        log.error(
            "SENTRY_DSN is set but sentry-sdk is not installed — errors are NOT "
            "being tracked. Run: pip install -r requirements.txt"
        )
        return False
    try:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            # Nudgy holds calendar access for other people; default-off PII means
            # no IP addresses, no cookies, no request bodies attached to events.
            send_default_pii=False,
            before_send=scrub_event,
        )
    except Exception:                                  # pragma: no cover - defensive
        log.exception("Sentry init failed — continuing without error tracking")
        return False
    log.info("Sentry enabled (environment=%s)", SENTRY_ENVIRONMENT)
    return True
