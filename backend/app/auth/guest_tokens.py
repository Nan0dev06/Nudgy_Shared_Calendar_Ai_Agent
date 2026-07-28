"""The cookie that remembers who a share-link voter is.

A guest has no account, so "the same person came back" has to be carried by the
browser. This is a signed cookie holding a plan_id -> guest_id map (a map, not a
single id, so someone who opens two different share links doesn't lose the first
identity to the second).

What it is NOT: a login. It grants nothing except the ability to see and change
your own answers on the specific plans listed inside it, and every read is
re-checked against that plan's live share token. Losing it costs a guest their
vote history, not access to anything.

Signed with SECRET_KEY under its own salt, so a guest cookie can never be
replayed as a session cookie (or vice versa), and forging one means forging a
signature.
"""
from __future__ import annotations

from itsdangerous import BadData, URLSafeTimedSerializer

from app.core.config import SECRET_KEY

GUEST_COOKIE = "nudgy_guest"
# Long enough to outlive any plan's voting window; a guest identity is worthless
# once its plan is settled, so there's nothing to gain from a shorter life.
GUEST_TTL_SECONDS = 60 * 24 * 3600

_signer = URLSafeTimedSerializer(SECRET_KEY, salt="nudgy-guest")


def read_guest_cookie(value: str | None) -> dict[str, int]:
    """plan_id (as a string key) -> guest_id. Empty for missing/forged/expired."""
    if not value:
        return {}
    try:
        data = _signer.loads(value, max_age=GUEST_TTL_SECONDS)
    except BadData:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): int(v) for k, v in data.items() if str(v).isdigit()}


def guest_id_for(value: str | None, plan_id: int) -> int | None:
    return read_guest_cookie(value).get(str(plan_id))


def with_guest(value: str | None, plan_id: int, guest_id: int) -> str:
    """The cookie value to set after somebody joins — the existing map plus this
    plan, so other plans' identities survive."""
    identities = read_guest_cookie(value)
    identities[str(plan_id)] = guest_id
    return _signer.dumps(identities)
