"""Mailer facade: one process-wide EmailSender the app sends through.

    from app.mailer import send_email
    send_email(to, subject, text)

Defaults to the console (dev) backend. `configure_from_env()` (called at app
startup) swaps in the SMTP backend when SMTP_* is set; tests swap in the memory
backend via set_email_sender(...).
"""
from __future__ import annotations

import logging

from app.mailer.base import Email, EmailSender
from app.mailer.console import ConsoleEmailSender

log = logging.getLogger("nudgy.mailer")

_sender: EmailSender = ConsoleEmailSender()


def set_email_sender(sender: EmailSender) -> None:
    global _sender
    _sender = sender


def get_email_sender() -> EmailSender:
    return _sender


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send, propagating any backend failure. Use when the caller must know the
    send succeeded (or is fine with a 500 on failure)."""
    _sender.send(Email(to=to, subject=subject, text=text, html=html))


def send_email_safe(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send, swallowing (and logging) any backend failure; returns whether it
    sent. For enumeration-safe auth flows (register / magic-link / reset-request)
    that MUST return an identical response whether or not mail actually went out —
    a transient SMTP hiccup must not 500, and must not let response shape/timing
    reveal whether the address exists. Real failures are logged for the operator.
    """
    try:
        _sender.send(Email(to=to, subject=subject, text=text, html=html))
        return True
    except Exception:
        # Deliberately broad: no delivery failure should surface to the user or
        # break enumeration-safety. The stack (incl. the real SMTP cause) is
        # logged; the link/body is not, so nothing sensitive lands in the log.
        log.exception("email send failed (subject=%r) — swallowed to preserve "
                      "enumeration-safety", subject)
        return False


def email_sender_from_env() -> EmailSender:
    """Pick the backend from config: SMTP when host+user+password are all set,
    else the console dev backend. Import config lazily so importing the mailer
    (e.g. in tests) doesn't force the whole config/env to load."""
    from app.core import config

    if config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD:
        from app.mailer.smtp import SmtpEmailSender

        return SmtpEmailSender(
            host=config.SMTP_HOST,
            port=config.SMTP_PORT,
            username=config.SMTP_USER,
            password=config.SMTP_PASSWORD,
            from_addr=config.SMTP_FROM,
        )
    return ConsoleEmailSender()


def configure_from_env() -> None:
    """Install the env-selected backend as the process sender. Called once at
    app startup; logs which backend is live (never any credentials)."""
    sender = email_sender_from_env()
    set_email_sender(sender)
    log.info("mailer backend: %s", type(sender).__name__)
