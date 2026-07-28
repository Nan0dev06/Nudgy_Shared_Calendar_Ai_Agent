"""Background work that runs on a clock rather than on a request.

Deliberately in-process (an asyncio task started by main.py's lifespan) instead
of Celery/RQ + a broker: the workload is "scan the open plans once a minute",
the deploy target is one free-tier web service, and a queue would be a second
thing to host, pay for, and keep alive. Every job here follows the same shape —
a pure-ish `run_*` function taking a session and an explicit `now` (so tests
drive it directly with a frozen clock) plus a thin async loop around it.
"""
