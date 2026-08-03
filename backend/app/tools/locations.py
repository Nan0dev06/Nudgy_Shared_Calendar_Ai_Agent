"""Location-anchored venue suggestion (Feature: 'where should we go?').

Two ways to anchor the search: on an area the user named (then no calendar is
read at all — see `near` below), or, by default, on where the group already is:

Pipeline:
  1. For each connected member, read the LOCATION field of their own events
     adjacent to the candidate slot (within +/- N hours).
     PRIVACY: the Google request is fields-restricted to location/start/end —
     event titles and descriptions are never requested, so they never enter
     this system even transiently. This is the documented single exception
     to "freebusy only": locations users explicitly typed, nothing else.
  2. Geocode those location strings (OpenStreetMap Nominatim — free, no key).
  3. Anchor = centroid of the geocoded points.
  4. Find real venues near the anchor (OpenStreetMap Overpass — free, no key).
     Venues come ONLY from this API call; if it returns nothing, we say so.
     No venue is ever invented.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

if TYPE_CHECKING:
    from app.calendars.base import CalendarProvider

log = logging.getLogger("nudgy.agent")

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass is free and public, so it 504s under load. Retrying the main
# endpoint beats failing over to a mirror: the well-known mirrors are either
# unreliable (kumi.systems timed out) or REGIONAL (overpass.osm.ch serves
# Switzerland only — it answers 200 with zero results for Beirut, which is
# worse than an error because it looks like a real "no venues here").
OVERPASS_ATTEMPTS = 3
# Nominatim's usage policy requires a User-Agent that identifies the app AND
# gives a way to contact whoever runs it; generic ones get blocked. A repo URL
# counts. Overridable so a fork does not impersonate this deployment.
HTTP_HEADERS = {"User-Agent": os.getenv(
    "NUDGY_USER_AGENT",
    "Nudgy/1.0 (+https://github.com/Nan0dev06/Nudgy_Shared_Calendar_Ai_Agent)",
)}

# What "kind of place" means as an OpenStreetMap tag.
#
# The value is a (tag key, tag value) pair rather than a bare string because OSM
# does not file everything under `amenity`: a park is `leisure`, and assuming
# otherwise is why parks silently returned nothing. Keys here are also the enum
# the agent chooses from — agent/tools.py derives it from this dict so the two
# cannot drift apart.
VENUE_KINDS: dict[str, tuple[str, str]] = {
    "cafe": ("amenity", "cafe"),
    "restaurant": ("amenity", "restaurant"),
    "bar": ("amenity", "bar"),
    "pub": ("amenity", "pub"),
    "fast_food": ("amenity", "fast_food"),
    "ice_cream": ("amenity", "ice_cream"),
    "cinema": ("amenity", "cinema"),
    "park": ("leisure", "park"),
    "bowling_alley": ("leisure", "bowling_alley"),
}
DEFAULT_VENUE_KIND = "cafe"

# Two different jobs, so two sets — see `_dedupe_key`.
#
# Articles and particles are NEVER distinctive; "The Coffee House" and "Coffee
# House" are one place, so these are dropped outright.
_ARTICLES = {"the", "de", "du", "la", "le", "el", "al", "and", "of"}
# Category words name what a place IS, not which one it is. Dropped when
# deciding what is distinctive, but kept as a last-resort key: a venue called
# nothing but category words ("The Coffee Shop") must not collapse into every
# other one ("The Coffee House").
_CATEGORY_WORDS = {
    "cafe", "coffee", "restaurant", "bar", "pub", "bistro", "lounge", "shop",
    "house",
}


def get_adjacent_event_locations(
    creds: Credentials, slot_start: datetime, slot_end: datetime, window_hours: int = 2
) -> list[str]:
    """Location strings from the member's own events within +/- window_hours
    of the slot. fields= restricts the response to location/start/end ONLY —
    Google never sends us titles/descriptions/attendees at all."""
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    time_min = (slot_start - timedelta(hours=window_hours)).astimezone(timezone.utc)
    time_max = (slot_end + timedelta(hours=window_hours)).astimezone(timezone.utc)
    resp = service.events().list(
        calendarId="primary",
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        singleEvents=True,
        maxResults=10,
        fields="items(location,start,end)",  # <- the privacy boundary
    ).execute()
    return [ev["location"].strip() for ev in resp.get("items", []) if ev.get("location")]


def geocode(location: str) -> tuple[float, float] | None:
    """location string -> (lat, lon) via Nominatim; None if not found."""
    try:
        r = httpx.get(
            f"{NOMINATIM_URL}/search",
            params={"q": location, "format": "json", "limit": 1},
            headers=HTTP_HEADERS, timeout=10,
        )
        r.raise_for_status()
        hits = r.json()
        if not hits:
            return None
        return float(hits[0]["lat"]), float(hits[0]["lon"])
    except Exception as exc:
        log.warning("[venues] geocode failed for %r: %s", location, exc)
        return None


def area_name(lat: float, lon: float) -> str:
    """Reverse-geocode the anchor to a human area name ('Hamra, Beirut')."""
    try:
        r = httpx.get(
            f"{NOMINATIM_URL}/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 14},
            headers=HTTP_HEADERS, timeout=10,
        )
        r.raise_for_status()
        addr = r.json().get("address", {})
        parts = [addr.get(k) for k in ("suburb", "neighbourhood", "quarter", "city", "town")]
        parts = [p for p in parts if p]
        return ", ".join(parts[:2]) if parts else "the computed midpoint"
    except Exception:
        return "the computed midpoint"


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Mean of lat/lon points. Fine at city scale (we're not crossing poles)."""
    if not points:
        raise ValueError("centroid of no points")
    return (sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points))


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> int:
    """Haversine distance in meters."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return int(2 * 6_371_000 * math.asin(math.sqrt(h)))


# --------------------------------------------------------------- venue cache
#
# Overpass is free, public, keyless and shared with the whole internet — it 429s
# and 504s under load, and it did so 3 times out of 3 during the verification
# run that produced these fixes. The retry helps; not asking at all helps more.
#
# A group planning an evening searches the SAME area repeatedly: once per agent
# step that calls suggest_venues, again when they ask for a different kind, again
# when someone re-opens the plan. Cafes near a point do not change in an hour, so
# a short TTL turns that burst into one request.
#
# Deliberately NOT the freebusy cache: that one is about a person's private
# availability with a 90s TTL and a booking-time invalidation rule. This is
# public map data with no correctness deadline. Same shape, different lifetime.
VENUE_CACHE_TTL_SECONDS = float(os.getenv("VENUE_CACHE_TTL_SECONDS", "900"))
# ~11 m at Beirut's latitude. Anchors are computed centroids, so two searches for
# "the same place" differ in the 5th decimal; rounding makes them one key.
_CACHE_COORD_DP = 4

_venue_cache: dict[tuple, tuple[float, list[dict] | None]] = {}
_venue_cache_lock = threading.Lock()


def _cache_key(lat: float, lon: float, kind: str, radius_m: int, limit: int) -> tuple:
    return (round(lat, _CACHE_COORD_DP), round(lon, _CACHE_COORD_DP),
            kind, radius_m, limit)


def clear_venue_cache() -> None:
    """Drop everything. For tests, and for a manual 'try that again'."""
    with _venue_cache_lock:
        _venue_cache.clear()


def _display_name(tags: dict) -> str | None:
    """The name to show a user, English first.

    OSM's `name` is the LOCAL-language name, so a Beirut search came back as
    'ستاربكس' and 'كافي دو براغ' — correct data, unreadable in an English UI, and
    the model has to reason over it too. `name:en` is tagged on roughly 4 in 5
    Beirut cafes; where it is missing the local name is still better than
    dropping a real venue.
    """
    tags = tags or {}
    for key in ("name:en", "int_name", "name"):
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return None


def _dedupe_key(name: str) -> frozenset[str] | str:
    """A key that collapses the same place spelled two ways.

    OSM lets one shop exist as two nodes, and Hamra returns exactly that:
    'Caribou Coffee' and 'Café Caribou' are one Caribou; 'Café Younès' and
    'Café Younes' differ by an accent. Casefolding alone catches neither.

    So: strip accents, drop articles outright, drop the words that name the
    CATEGORY rather than the place, and compare what is left as a SET — word
    order stops mattering and 'caribou' meets 'caribou'.

    When nothing distinctive survives ('The Coffee House') the remaining words
    become the key instead, so it still matches 'Coffee House' but not 'The
    Coffee Shop'. Cross-SCRIPT duplicates only collapse when OSM tagged
    `name:en` on both; no amount of normalising matches two alphabets.
    """
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(ch)
    ).casefold()
    words = {w for w in "".join(
        ch if ch.isalnum() else " " for ch in stripped
    ).split() if w} - _ARTICLES
    distinctive = words - _CATEGORY_WORDS
    return frozenset(distinctive) if distinctive else " ".join(sorted(words))


# Two venues sharing a distinctive name this close together are one venue mapped
# twice, not neighbours. Wide enough for a shop tagged once at the door and once
# at the building centroid; tight enough that two branches of a chain on
# different streets both survive.
#
# MEASURED 2026-08-03, and deliberately left just outside: Hamra returns
# 'Caribou Coffee' (33.89570, 35.48119) and 'Café Caribou' (33.89530, 35.48435)
# **295 m apart**, so both survive. They may well be one shop mapped twice by
# two people — the two different Arabic spellings suggest it — but they may also
# be two real branches on a dense street, and nothing in the data settles it.
# Raising this to ~400 would collapse that pair at the cost of merging genuine
# neighbouring branches elsewhere. Showing a duplicate wastes a slot; hiding a
# real venue loses an option. The second is worse, so this stays conservative.
DUPLICATE_RADIUS_M = 250


def search_venues_near(
    lat: float, lon: float, kind: str = DEFAULT_VENUE_KIND,
    radius_m: int = 1500, limit: int = 5,
) -> list[dict] | None:
    """REAL venues near a point from OpenStreetMap (Overpass).

    Returns [] when the area genuinely has no such venue, and None when the
    SEARCH ITSELF failed (Overpass is a free public API and 504s under load).
    Those two are very different and must never be conflated: reporting a
    timeout as "no cafes here" tells the user something false about a real
    place. Only named places are returned, and nothing is ever invented.

    Queries `nwr`, not `node`: plenty of venues are mapped as a building outline
    rather than a point, and parks and cinemas nearly always are. `out center`
    hands back a representative coordinate for those, so one parser covers all
    three element types.
    """
    kind = kind if kind in VENUE_KINDS else DEFAULT_VENUE_KIND
    cache_key = _cache_key(lat, lon, kind, radius_m, limit)
    with _venue_cache_lock:
        hit = _venue_cache.get(cache_key)
        if hit is not None and time.monotonic() - hit[0] < VENUE_CACHE_TTL_SECONDS:
            return hit[1]

    tag_key, tag_value = VENUE_KINDS[kind]
    query = (
        f'[out:json][timeout:15];'
        f'nwr(around:{radius_m},{lat},{lon})["{tag_key}"="{tag_value}"]["name"];'
        f'out center {max(limit * 4, 20)};'
    )
    elements = None
    for attempt in range(OVERPASS_ATTEMPTS):
        try:
            r = httpx.post(OVERPASS_URL, data={"data": query},
                           headers=HTTP_HEADERS, timeout=30)
            r.raise_for_status()
            elements = r.json().get("elements", [])
            break
        except Exception as exc:
            log.warning("[venues] overpass attempt %d/%d failed: %s",
                        attempt + 1, OVERPASS_ATTEMPTS, exc)
            if attempt + 1 < OVERPASS_ATTEMPTS:
                time.sleep(2 ** attempt)  # 1s, 2s — it's usually transient load
    if elements is None:
        return None  # searching failed; the caller must NOT say "no venues"

    venues = []
    for el in elements:
        name = _display_name(el.get("tags"))
        if not name:
            continue
        # nodes carry lat/lon directly; ways and relations get it from `center`
        center = el.get("center") or el
        v_lat, v_lon = center.get("lat"), center.get("lon")
        if v_lat is None or v_lon is None:
            continue
        venues.append({
            "name": name,
            "kind": kind,
            "distance_m": distance_m((lat, lon), (v_lat, v_lon)),
            "map_url": f"https://www.openstreetmap.org/?mlat={v_lat}&mlon={v_lon}#map=18/{v_lat}/{v_lon}",
            "_lat": v_lat,
            "_lon": v_lon,
        })
    venues.sort(key=lambda v: v["distance_m"])

    # Collapse the same place mapped twice. Nearest wins, so the survivor is the
    # one whose coordinate is closest to the anchor — and because the list is
    # already sorted, the first of any duplicate pair is that one.
    kept: list[dict] = []
    for v in venues:
        # NOT `key` — that name belongs to the cache key above, and shadowing it
        # here silently stored every result under a frozenset no lookup could
        # match. The cache never hit and grew an entry per call.
        name_key = _dedupe_key(v["name"])
        twin = next(
            (k for k in kept
             if _dedupe_key(k["name"]) == name_key
             and distance_m((k["_lat"], k["_lon"]), (v["_lat"], v["_lon"]))
             <= DUPLICATE_RADIUS_M),
            None,
        )
        if twin is None:
            kept.append(v)
        if len(kept) >= limit:
            break
    for v in kept:
        v.pop("_lat", None)
        v.pop("_lon", None)

    # Only successes are cached. A transient 504 stored for 15 minutes would
    # turn one bad moment into a quarter-hour of "the venue lookup is down" for
    # an area that is actually fine — the opposite of what this cache is for.
    with _venue_cache_lock:
        _venue_cache[cache_key] = (time.monotonic(), kept)
    return kept


def _search_failed(where: str) -> dict:
    """The map service didn't answer. Say THAT — not 'there are no cafes'.

    `where` is carried as DATA (`searched_near`) and never spliced into `note`.
    A note is trusted text the model is meant to follow — see
    agent/loop.TRUSTED_RESULT_KEYS — while `where` is either an area the user
    typed or a name that came back from OpenStreetMap. Interpolating one into
    the other would smuggle untrusted text past the fence, so every note here
    points at a field name instead of inlining its value.
    """
    return {
        "search_failed": True,
        "venues": [],
        "searched_near": where,
        "note": ("The map service (OpenStreetMap) did not respond, so the venues near "
                 "the area named in `searched_near` are UNKNOWN — this is NOT the same "
                 "as there being none. Do not tell the user the area has no places and "
                 "do not ask them for a different area. Say the venue lookup is "
                 "temporarily down, and offer to try again in a moment or to let them "
                 "name the spot themselves."),
    }


def suggest_venues_for_slot(
    members_with_providers: list[tuple[str, "CalendarProvider"]],
    slot_start: datetime,
    slot_end: datetime,
    kind: str = "cafe",
    near: str | None = None,
) -> dict:
    """The full pipeline. Returns a JSON-safe dict the agent can reason over:
    which member locations anchored the search (locations only — never why
    they're there), the anchor area, and REAL venues found near it.

    `near` anchors on an area the user named instead of on the group's own
    locations. Nobody's calendar is read at all in that case — the user told us
    where to look, so there is nothing to infer.
    """
    if near:
        point = geocode(near)
        if point is None:
            return {
                "anchor": None, "venues": [], "requested_area": near,
                "note": ("Could not find the place named in `requested_area` on the "
                         "map. Ask the user to name the area differently, or to add "
                         "the city."),
            }
        anchor_area = area_name(*point)
        venues = search_venues_near(*point, kind=kind)
        if venues is None:
            return _search_failed(near)
        log.info("[venues] user-named anchor %r=%s (%s) -> %d real venue(s)",
                 near, point, anchor_area, len(venues))
        return {
            "anchor_area": anchor_area,
            "requested_area": near,
            "anchored_on": "the area the user asked for (see `requested_area`)",
            "venues": venues,
            "note": (None if venues else
                     f"The venue search near the area in `requested_area` returned no "
                     f"{kind}s — tell the user honestly; do NOT invent a venue."),
        }

    locations_by_member: dict[str, list[str]] = {}
    for email, provider in members_with_providers:
        locs = provider.get_event_locations(slot_start, slot_end)
        if locs:
            locations_by_member[email] = sorted(set(locs))
        log.info("[venues] %s — %d declared location(s) near slot", email, len(locs))

    if not locations_by_member:
        return {
            "anchor": None, "venues": [],
            "note": ("No member has an event with a declared location near this "
                     "slot, so there is nothing to anchor a venue search on. "
                     "Ask the user where the group will roughly be."),
        }

    # geocode each unique location string (cache; be polite to Nominatim)
    coords: dict[str, tuple[float, float]] = {}
    for loc in {l for locs in locations_by_member.values() for l in locs}:
        point = geocode(loc)
        if point:
            coords[loc] = point
        time.sleep(1)  # Nominatim policy: max 1 req/s

    if not coords:
        return {
            "anchor": None, "venues": [],
            "locations_by_member": locations_by_member,
            "note": "Found declared locations but none could be geocoded — say so honestly.",
        }

    anchor = centroid(list(coords.values()))
    anchor_area = area_name(*anchor)
    venues = search_venues_near(*anchor, kind=kind)
    if venues is None:
        return _search_failed(anchor_area) | {"locations_by_member": locations_by_member}
    log.info("[venues] anchor=%s (%s) -> %d real venue(s)", anchor, anchor_area, len(venues))

    return {
        "locations_by_member": locations_by_member,
        "anchor_area": anchor_area,
        "venues": venues,
        "note": (None if venues else
                 f"The venue search near the area in `anchor_area` returned no "
                 f"{kind}s — tell the user honestly; do NOT invent a venue."),
    }
