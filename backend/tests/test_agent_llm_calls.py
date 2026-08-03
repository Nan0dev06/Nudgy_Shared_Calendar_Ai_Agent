"""What `_create_with_retry` does when the model misbehaves.

Three behaviours worth pinning, all of which are invisible until a beta tester
hits them:

1. **Temperature is sent.** These APIs default to 1.0, tuned for open-ended
   writing. Nudgy is a tool-calling agent working a fixed rulebook, and the
   `tool_use_failed` retry below exists precisely because high-temperature
   Llama emits malformed function calls.
2. **A rate limit falls back to a smaller model, once.** The free tiers meter a
   DAILY budget per model, shared by every beta tester on one API key, so a
   main model that has run dry says nothing about the smaller one's remaining
   quota. Before this, a rate-limited user waited through 4+8+16+30s of sleep
   and then got an error.
3. **A malformed-tool-call failure does NOT fall back.** That is not a quota
   problem, and a smaller model would produce more of them, not fewer.
"""
import httpx
import pytest
from openai import BadRequestError, RateLimitError

from app.agent import loop as agent_loop


def _http_error(status: int, message: str):
    """Build a real openai SDK error — they carry an httpx response."""
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, request=request)
    cls = RateLimitError if status == 429 else BadRequestError
    return cls(message, response=response, body=None)


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)   # each item: an Exception to raise, or a result
        self.calls = []              # (model, temperature) per attempt

    def create(self, **kwargs):
        self.calls.append((kwargs.get("model"), kwargs.get("temperature")))
        outcome = self.script.pop(0) if self.script else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeClient:
    def __init__(self, script):
        self.chat = type("chat", (), {"completions": _FakeCompletions(script)})()

    @property
    def calls(self):
        return self.chat.completions.calls


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """The retry path sleeps up to 58s in total. Tests must not."""
    monkeypatch.setattr(agent_loop.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def quiet_tools(monkeypatch):
    """`_openai_tools()` reads the real schemas; nothing here needs them."""
    monkeypatch.setattr(agent_loop, "_openai_tools", lambda: [])


def test_temperature_is_sent_on_every_call(monkeypatch):
    monkeypatch.setattr(agent_loop, "LLM_TEMPERATURE", 0.2)
    monkeypatch.setattr(agent_loop, "LLM_MODEL", "main-model")
    client = _FakeClient(["ok"])

    agent_loop._create_with_retry(client, [{"role": "user", "content": "hi"}])

    assert client.calls == [("main-model", 0.2)]


def test_rate_limit_falls_back_to_the_smaller_model_once(monkeypatch):
    monkeypatch.setattr(agent_loop, "LLM_MODEL", "big-model")
    monkeypatch.setattr(agent_loop, "LLM_FALLBACK_MODEL", "small-model")
    monkeypatch.setattr(agent_loop, "LLM_TEMPERATURE", 0.2)
    # every attempt on the main model is rate-limited
    client = _FakeClient([_http_error(429, "rate limit") for _ in range(5)])

    result = agent_loop._create_with_retry(client, [], attempts=5)

    assert result == "ok"
    models = [m for m, _t in client.calls]
    assert models == ["big-model"] * 5 + ["small-model"], \
        "the fallback must run exactly once, and only after the retries"


def test_malformed_tool_calls_do_not_fall_back(monkeypatch):
    monkeypatch.setattr(agent_loop, "LLM_MODEL", "big-model")
    monkeypatch.setattr(agent_loop, "LLM_FALLBACK_MODEL", "small-model")
    client = _FakeClient([_http_error(400, "tool_use_failed") for _ in range(3)])

    with pytest.raises(BadRequestError):
        agent_loop._create_with_retry(client, [], attempts=3)

    assert {m for m, _t in client.calls} == {"big-model"}, \
        "a bad sample is not a quota problem — a weaker model makes it worse"


def test_no_fallback_when_it_is_the_same_model(monkeypatch):
    """The default fallback IS the default model. Retrying it is pure latency."""
    monkeypatch.setattr(agent_loop, "LLM_MODEL", "llama-3.1-8b-instant")
    monkeypatch.setattr(agent_loop, "LLM_FALLBACK_MODEL", "llama-3.1-8b-instant")
    client = _FakeClient([_http_error(429, "rate limit") for _ in range(2)])

    with pytest.raises(RateLimitError):
        agent_loop._create_with_retry(client, [], attempts=2)

    assert len(client.calls) == 2


def test_fallback_failing_reraises_the_original_rate_limit(monkeypatch):
    """The user should hear 'rate limited', not whatever the fallback said."""
    monkeypatch.setattr(agent_loop, "LLM_MODEL", "big-model")
    monkeypatch.setattr(agent_loop, "LLM_FALLBACK_MODEL", "small-model")
    client = _FakeClient([_http_error(429, "rate limit"),
                          _http_error(429, "fallback also limited")])

    with pytest.raises(RateLimitError):
        agent_loop._create_with_retry(client, [], attempts=1)


def test_a_non_retryable_bad_request_propagates_immediately(monkeypatch):
    monkeypatch.setattr(agent_loop, "LLM_MODEL", "big-model")
    monkeypatch.setattr(agent_loop, "LLM_FALLBACK_MODEL", "small-model")
    client = _FakeClient([_http_error(400, "context_length_exceeded")])

    with pytest.raises(BadRequestError):
        agent_loop._create_with_retry(client, [], attempts=5)

    assert len(client.calls) == 1, "only tool_use_failed is worth another sample"


def test_usage_logging_survives_a_response_without_usage(monkeypatch, caplog):
    """Ollama and some proxies omit `usage`; that must not break a turn."""
    monkeypatch.setattr(agent_loop, "LLM_MODEL", "m")
    client = _FakeClient(["ok"])  # a plain string has no .usage

    assert agent_loop._create_with_retry(client, []) == "ok"
