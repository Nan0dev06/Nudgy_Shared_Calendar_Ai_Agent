"""Calendar-provider factory: pick the implementation for a CalendarAccount.

    provider = provider_for_account(session, account)
    busy = provider.get_busy(now, end)

Callers depend only on the CalendarProvider interface — never on Google or
Microsoft directly — so adding a provider is one new module + one branch here.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.calendars.base import CalendarProvider, CreatedEvent, Interval
from app.calendars.google import GoogleCalendarProvider
from app.db.models import CalendarAccount

__all__ = [
    "CalendarProvider", "CreatedEvent", "Interval", "provider_for_account",
]


def provider_for_account(session: Session, account: CalendarAccount) -> CalendarProvider:
    """The live provider for this connected calendar, credentials resolved."""
    if account.provider == "google":
        return GoogleCalendarProvider.from_account(session, account)
    if account.provider == "microsoft":
        raise NotImplementedError(
            "Microsoft/Outlook calendars land in Phase 1 Branch 4 "
            "(needs an Azure app registration)."
        )
    raise ValueError(f"Unknown calendar provider: {account.provider!r}")
