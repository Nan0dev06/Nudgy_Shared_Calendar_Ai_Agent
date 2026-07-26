"""Dev email backend: log the message instead of sending it.

The default backend for local dev and private beta before a real provider is
wired. It prints the full body to the server log at INFO, so magic-links and
verification links are copy-pasteable straight from the uvicorn console — no
inbox, no external account.
"""
from __future__ import annotations

import logging

from app.mailer.base import Email, EmailSender

log = logging.getLogger("nudgy.mailer")


class ConsoleEmailSender(EmailSender):
    def send(self, email: Email) -> None:
        log.info(
            "\n----- EMAIL (dev backend, not actually sent) -----\n"
            "To: %s\nSubject: %s\n\n%s\n"
            "--------------------------------------------------",
            email.to, email.subject, email.text,
        )
