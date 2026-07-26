"""Test email backend: capture messages in an in-memory outbox instead of
sending. Tests swap this in via app.mailer.set_email_sender and assert on the
links/subjects that landed in `.outbox`."""
from __future__ import annotations

from app.mailer.base import Email, EmailSender


class MemoryEmailSender(EmailSender):
    def __init__(self):
        self.outbox: list[Email] = []

    def send(self, email: Email) -> None:
        self.outbox.append(email)

    @property
    def last(self) -> Email | None:
        return self.outbox[-1] if self.outbox else None
