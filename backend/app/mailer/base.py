"""The email-sending seam.

Auth flows (verification, magic-link, password reset) send transactional email
through an EmailSender, never a provider SDK directly — so a real free-tier
provider (Resend, SMTP, …) is a later drop-in, config not a refactor. Named
`mailer` (not `email`) to avoid any confusion with the stdlib `email` package.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Email:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailSender(ABC):
    @abstractmethod
    def send(self, email: Email) -> None:
        ...
