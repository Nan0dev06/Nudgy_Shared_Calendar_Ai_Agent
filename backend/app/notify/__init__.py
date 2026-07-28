"""Outbound notifications about plans (as opposed to auth mail).

Everything goes through `app.mailer`, so the dev console backend shows these
locally and a real provider is still a one-line swap. Kept out of the route and
job modules so the wording of what a member receives lives in one file.

Not here yet, deliberately: in-app and push channels, and the per-user
Settings > Notifications preferences that will gate all three. When those land,
the send functions below are where the preference check belongs — every caller
already funnels through them.
"""
from app.notify.plans import (
    notify_plan_auto_booked, notify_plan_expired, notify_vote_reminder,
)

__all__ = [
    "notify_plan_auto_booked",
    "notify_plan_expired",
    "notify_vote_reminder",
]
