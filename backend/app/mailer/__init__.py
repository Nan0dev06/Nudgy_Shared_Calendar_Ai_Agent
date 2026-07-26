"""Mailer facade: one process-wide EmailSender the app sends through.

    from app.mailer import send_email
    send_email(to, subject, text)

Defaults to the console (dev) backend. Swap it — for tests, or to plug in a real
free-tier provider — with set_email_sender(...).
"""
from __future__ import annotations

from app.mailer.base import Email, EmailSender
from app.mailer.console import ConsoleEmailSender

_sender: EmailSender = ConsoleEmailSender()


def set_email_sender(sender: EmailSender) -> None:
    global _sender
    _sender = sender


def get_email_sender() -> EmailSender:
    return _sender


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    _sender.send(Email(to=to, subject=subject, text=text, html=html))
