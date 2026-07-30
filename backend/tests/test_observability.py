"""Error reports must not carry the credentials the URLs are full of.

Nudgy puts bearer tokens in paths (`/share/<token>/…`), OAuth codes and reset
tokens in query strings, and passwords in request bodies. An unscrubbed Sentry
event is a copy of the request, so each of those is checked here.
"""
from __future__ import annotations

from app.core.observability import REDACTED, init_sentry, scrub_event, scrub_url


class TestScrubUrl:
    def test_query_string_is_dropped(self):
        url = scrub_url("https://nudgy.app/auth/verify?token=secret-value")
        assert "secret-value" not in url
        assert url.startswith("https://nudgy.app/auth/verify")

    def test_oauth_code_is_dropped(self):
        url = scrub_url("https://nudgy.app/auth/google/callback?code=4/abc&state=xyz")
        assert "4/abc" not in url and "xyz" not in url

    def test_share_token_in_the_path_is_redacted(self):
        url = scrub_url("https://nudgy.app/share/tok3n/vote")
        assert "tok3n" not in url
        assert url == f"https://nudgy.app/share/{REDACTED}/vote"

    def test_the_rest_of_the_path_survives(self):
        """The route is the whole debugging value of the URL — keep it."""
        assert scrub_url("https://nudgy.app/plans/12/lock-in") == (
            "https://nudgy.app/plans/12/lock-in"
        )

    def test_fragment_is_dropped(self):
        assert "#tok" not in scrub_url("https://nudgy.app/?x=1#tok")

    def test_none_and_empty_pass_through(self):
        assert scrub_url(None) is None
        assert scrub_url("") == ""


class TestScrubEvent:
    def _event(self):
        return {
            "request": {
                "url": "https://nudgy.app/share/tok3n/vote?token=leak",
                "query_string": "token=leak",
                "cookies": {"nudgy_session": "signed-session"},
                "headers": {"Cookie": "nudgy_session=signed-session",
                            "Authorization": "Bearer abc",
                            "User-Agent": "Firefox"},
                "data": {"password": "hunter2"},
            }
        }

    def test_credentials_are_gone(self):
        event = scrub_event(self._event())
        blob = repr(event)
        for secret in ("tok3n", "leak", "signed-session", "hunter2", "Bearer abc"):
            assert secret not in blob, f"{secret!r} leaked into the event"

    def test_cookies_and_body_are_removed_entirely(self):
        request = scrub_event(self._event())["request"]
        assert "cookies" not in request
        assert "data" not in request
        assert request["query_string"] == ""

    def test_harmless_headers_survive(self):
        headers = scrub_event(self._event())["request"]["headers"]
        assert headers["User-Agent"] == "Firefox"
        assert headers["Cookie"] == REDACTED

    def test_breadcrumb_urls_are_scrubbed(self):
        event = {"breadcrumbs": {"values": [
            {"data": {"url": "https://api.example.com/x?key=secret"}},
            {"message": "no data key at all"},
        ]}}
        out = scrub_event(event)
        assert "secret" not in repr(out)

    def test_an_event_without_a_request_is_fine(self):
        """Errors from the background ticker have no request at all."""
        assert scrub_event({"level": "error"}) == {"level": "error"}


class TestInit:
    def test_disabled_without_a_dsn(self, monkeypatch):
        monkeypatch.setattr("app.core.observability.SENTRY_DSN", "")
        assert init_sentry() is False

    def test_a_missing_sdk_does_not_crash_the_app(self, monkeypatch):
        monkeypatch.setattr("app.core.observability.SENTRY_DSN", "https://k@example.com/1")
        import builtins
        real_import = builtins.__import__

        def no_sentry(name, *args, **kwargs):
            if name == "sentry_sdk":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_sentry)
        assert init_sentry() is False
