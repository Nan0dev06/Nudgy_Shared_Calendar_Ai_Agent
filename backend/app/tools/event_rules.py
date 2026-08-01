"""Who may change a group event or task — the single place that rule lives.

Pure functions over (event, user_id), like `plan_rules.py`: the route has
already established that the caller is a member of the event's group, and
these decide what membership actually entitles them to.

Three rules, and the reasoning behind each:

**Personal events belong to their owner.** A member's personal event is their
own thing — groupmates see only busy time, and if it's anonymous they don't
even see the title. Before this module any member could `DELETE /events/{id}`
on one, which meant deleting something they were never allowed to read.

**Only the creator edits a shared event, and a material edit resets
attendance** (docs/poll-edit-redesign.md §3). This replaces the earlier design
where every change went to a group vote. That was rejected for two reasons: it
is a second vote engine beside the poll engine, and *majority is the wrong rule
for calendars* — if 4 of 6 vote to move 5pm to 7pm, the 2 who can't make 7pm
are handed an event they never agreed to.

What survives from the original decision is the part that mattered: nothing
reaches a person's calendar without that person's own consent. A material edit
(title, start, end, location) sets every attendee back to `needs_reconfirm`
rather than carrying their yes across to something they never agreed to. Title
is material on purpose — a meeting renamed from "quarter goals" to "week
analysis" is exactly as material as a moved time.

Anyone who is not the creator can still *propose* a change; it arrives as a
suggestion the creator applies or drops, which keeps "any member can propose"
without creating a second authority over other people's time.

**Deleting a shared event is creator-only for now** — tightened from "any
member", which was a hole. Whether cancelling should itself need a group vote
is still open (see docs/v1-decisions.md).

Ticking a shared TASK done stays open to every member on purpose: marking
work finished is collaborative, and it doesn't change what the thing is or
when it happens. On a personal task it's the owner's alone, like everything
else about a personal event.

Group types (a workplace group where the leader changes the time directly,
rather than the group voting) are deliberately [LATER]. Every rule here is
the strict *subset* of that future — it grants nothing a leader model would
have to take away — so adding group types means widening these functions,
never rewriting the call sites.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    """Allowed or not, plus the sentence the user should see if not.

    The reason is part of the rule, not decoration: "you can't edit this"
    without "changes to a shared event go to the group" reads like a bug.
    """
    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Decision(True)

_NOT_YOURS = (
    "This is {owner}'s personal event — only they can change or remove it."
)
_CREATOR_ONLY_EDIT = (
    "Only whoever created this event can change it. Suggest the change to them "
    "and they can apply it."
)
_CREATOR_ONLY_DELETE = (
    "Only whoever created this event can remove it."
)

# Changing one of these changes what people agreed to come to, so everyone's
# attendance goes back to `needs_reconfirm`. Everything else (category, whether
# a personal event is anonymous) is bookkeeping and applies straight away.
#
# `title` is in here deliberately: renaming "quarter goals" to "week analysis"
# changes what you are attending as surely as moving it does.
MATERIAL_FIELDS = frozenset({"title", "start_iso", "end_iso", "location"})


def is_material(changed_fields) -> bool:
    """Does this edit invalidate the yes people already gave?"""
    return bool(MATERIAL_FIELDS & set(changed_fields))


def _owner_label(creator_email: str | None) -> str:
    return creator_email or "another member"


def can_edit(event, user_id: int, creator_email: str | None = None) -> Decision:
    """May this user change the event's fields (title, time, place, …)?

    Creator-only, for personal and shared alike — the difference is only in the
    sentence you get back. What protects the other attendees is not a permission
    check but `needs_reconfirm`: the creator can change the event, and everyone
    who had said yes is asked again rather than dragged along.

    Accepted cost, decided in the redesign: a creator can effectively push
    someone off an event by editing it. That is "the plan changed and you
    couldn't make it" — a real outcome, not a bug.
    """
    if event.created_by != user_id:
        if event.personal:
            return Decision(False, _NOT_YOURS.format(owner=_owner_label(creator_email)))
        return Decision(False, _CREATOR_ONLY_EDIT)
    return ALLOWED


def can_delete(event, user_id: int, creator_email: str | None = None) -> Decision:
    """May this user remove the event entirely?"""
    if event.personal and event.created_by != user_id:
        return Decision(False, _NOT_YOURS.format(owner=_owner_label(creator_email)))
    if event.created_by != user_id:
        return Decision(False, _CREATOR_ONLY_DELETE)
    return ALLOWED


def can_toggle_done(event, user_id: int, creator_email: str | None = None) -> Decision:
    """May this user tick it off? Shared work is finished collaboratively."""
    if event.personal and event.created_by != user_id:
        return Decision(False, _NOT_YOURS.format(owner=_owner_label(creator_email)))
    return ALLOWED
