"""SMTP email backend + the enumeration-safe send wrapper.

No real network: smtplib.SMTP / SMTP_SSL are replaced with a fake that records
the call order, so we can assert the security-relevant invariants (TLS upgrade
happens before AUTH, header-injection is rejected, credentials never leak into
errors) without a server.
"""
import smtplib

import pytest

from app import mailer
from app.mailer.base import Email
from app.mailer.console import ConsoleEmailSender
from app.mailer.smtp import EmailSendError, SmtpEmailSender


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL. Records the sequence of calls on a
    shared list so tests can assert ordering (e.g. starttls before login)."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, *, context=None, timeout=None):
        self.host = host
        self.port = port
        self.context = context
        self.timeout = timeout
        self.calls: list[str] = []
        self.sent: list = []
        FakeSMTP.instances.append(self)

    # context-manager protocol (`with smtplib.SMTP(...) as s:`)
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")
        self.context = context

    def login(self, user, password):
        self.calls.append("login")
        self.user = user
        self.password = password

    def send_message(self, msg):
        self.calls.append("send_message")
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeSMTP.instances = []
    yield
    FakeSMTP.instances = []


def _sender(**over):
    kw = dict(host="smtp.example.com", port=587, username="bot@example.com",
              password="app-secret", from_addr="bot@example.com")
    kw.update(over)
    return SmtpEmailSender(**kw)


def test_starttls_path_upgrades_tls_before_auth(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    _sender().send(Email(to="a@x.com", subject="Hi", text="body"))

    conn = FakeSMTP.instances[-1]
    # AUTH must never precede the TLS upgrade — credentials would cross plaintext.
    assert conn.calls.index("starttls") < conn.calls.index("login")
    assert conn.calls[-1] == "send_message"
    assert conn.context is not None  # verified SSL context passed to starttls


def test_port_465_uses_implicit_tls(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    _sender(port=465).send(Email(to="a@x.com", subject="Hi", text="body"))

    conn = FakeSMTP.instances[-1]
    assert "starttls" not in conn.calls          # implicit TLS, no upgrade step
    assert conn.context is not None              # but still a verified context
    assert conn.calls == ["login", "send_message"]


def test_message_carries_headers_and_body(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    _sender().send(Email(to="a@x.com", subject="Verify", text="click here"))

    msg = FakeSMTP.instances[-1].sent[-1]
    assert msg["To"] == "a@x.com"
    assert msg["From"] == "bot@example.com"
    assert msg["Subject"] == "Verify"
    assert "click here" in msg.get_content()


def test_header_injection_is_rejected(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    # A recipient smuggling a second header (BCC exfiltration) must be refused
    # before anything is sent.
    with pytest.raises(ValueError):
        _sender().send(Email(to="a@x.com\nBcc: evil@x.com", subject="Hi", text="b"))
    assert FakeSMTP.instances == []  # never connected


def test_send_failure_raises_without_leaking_password(monkeypatch):
    def boom(*a, **k):
        raise smtplib.SMTPAuthenticationError(535, b"bad creds")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    with pytest.raises(EmailSendError) as ei:
        _sender().send(Email(to="a@x.com", subject="Hi", text="b"))
    assert "app-secret" not in str(ei.value)


def test_constructor_rejects_missing_credentials():
    with pytest.raises(ValueError):
        SmtpEmailSender(host="", port=587, username="u", password="p", from_addr="u")
    with pytest.raises(ValueError):
        SmtpEmailSender(host="h", port=587, username="u", password="", from_addr="u")


# --- facade: send_email_safe + env selection --------------------------------

class BoomSender:
    def send(self, email):
        raise RuntimeError("smtp down")


def test_send_email_safe_swallows_failure():
    prev = mailer.get_email_sender()
    mailer.set_email_sender(BoomSender())
    try:
        # Must not raise: enumeration-safe flows return an identical response
        # whether or not delivery worked.
        assert mailer.send_email_safe("a@x.com", "Hi", "body") is False
    finally:
        mailer.set_email_sender(prev)


def test_send_email_safe_reports_success():
    prev = mailer.get_email_sender()
    mailer.set_email_sender(ConsoleEmailSender())
    try:
        assert mailer.send_email_safe("a@x.com", "Hi", "body") is True
    finally:
        mailer.set_email_sender(prev)


def test_env_selection_defaults_to_console(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config, "SMTP_HOST", "")
    assert isinstance(mailer.email_sender_from_env(), ConsoleEmailSender)


def test_env_selection_picks_smtp_when_configured(monkeypatch):
    from app.core import config
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_USER", "bot@example.com")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_FROM", "bot@example.com")
    assert isinstance(mailer.email_sender_from_env(), SmtpEmailSender)
