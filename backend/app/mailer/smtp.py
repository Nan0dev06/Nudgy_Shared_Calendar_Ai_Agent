"""Real transactional-email backend: send over SMTP.

The first non-dev EmailSender. For private beta this points at Gmail's SMTP
(an app password, no domain needed); any SMTP host works the same way. Plugged
in via config, not a code change — `email_sender_from_env()` picks this over the
console backend the moment SMTP_* is set.

Security posture (these are deliberate, not incidental):
- **TLS is mandatory.** Port 465 -> implicit TLS (SMTP_SSL); anything else ->
  STARTTLS upgrade before AUTH. Either way the SSL context is
  `ssl.create_default_context()`, which verifies the server cert and hostname —
  credentials never cross a plaintext or unverified connection.
- **Header-injection safe.** We build the message with the stdlib
  `EmailMessage`; its header setters reject embedded newlines, so a hostile
  display value can't smuggle extra headers (BCC exfiltration, spoofed From).
  The recipient address is the only user-controlled field and it goes through
  that same guard.
- **No secrets in logs.** The password is never logged; message bodies (which
  carry single-use verify/reset links = bearer credentials) are never logged
  here. Only subject + recipient at INFO, for operational visibility.
- **Bounded.** A per-connection timeout keeps a hung SMTP server from pinning
  the request thread.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.mailer.base import Email, EmailSender

log = logging.getLogger("nudgy.mailer")


class EmailSendError(RuntimeError):
    """Raised when a message could not be handed to the SMTP server. Carries a
    clean, credential-free reason; the underlying cause is chained for logs."""


class SmtpEmailSender(EmailSender):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        timeout: float = 15.0,
    ):
        if not host or not username or not password:
            # A misconfigured SMTP sender would silently swallow every auth
            # email; fail loudly at construction instead of at 2am.
            raise ValueError("SmtpEmailSender needs host, username, and password.")
        self.host = host
        self.port = port
        self.username = username
        self._password = password
        self.from_addr = from_addr or username
        self.timeout = timeout
        # Port 465 is the classic implicit-TLS submission port; everything else
        # (587, 25) starts plaintext and we upgrade with STARTTLS before AUTH.
        self.use_ssl = port == 465

    def _build(self, email: Email) -> EmailMessage:
        msg = EmailMessage()
        # EmailMessage rejects newlines in header values -> header-injection safe.
        msg["From"] = self.from_addr
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg.set_content(email.text)
        if email.html:
            msg.add_alternative(email.html, subtype="html")
        return msg

    def send(self, email: Email) -> None:
        msg = self._build(email)
        context = ssl.create_default_context()
        try:
            if self.use_ssl:
                with smtplib.SMTP_SSL(
                    self.host, self.port, context=context, timeout=self.timeout
                ) as smtp:
                    smtp.login(self.username, self._password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                    smtp.login(self.username, self._password)
                    smtp.send_message(msg)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # Chain the real cause for the server log; hand callers a message
            # that never contains the password or the connection internals.
            raise EmailSendError(
                f"Could not send email to {email.to!r} via {self.host}:{self.port}."
            ) from exc
        log.info("sent %r to %s", email.subject, email.to)
