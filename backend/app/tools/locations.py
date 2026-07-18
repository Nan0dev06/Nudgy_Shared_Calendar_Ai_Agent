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
import time
from datetime import datetime, timedelta, timezone

import httpx
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

log = logging.getLogger("nudgy.agent")

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# Overpass is free and public, so it 504s under load. Retrying the main
# endpoint beats failing over to a mirror: the well-known mirrors are either
# unreliable (kumi.systems timed out) or REGIONAL (overpass.osm.ch serves
# Switzerland only — it answers 200 with zero results for Beirut, which is
# worse than an error because it looks like a real "no venues here").
OVERPASS_ATTEMPTS = 3
# Nominatim usage policy requires an identifying User-Agent.
HTTP_HEADERS = {"User-Agent": "nudgy-hackathon-demo/1.0"}

VENUE_KINDS = {"cafe", "restaurant", "bar", "fast_food"}


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


def search_venues_near(
    lat: float, lon: float, kind: str = "cafe", radius_m: int = 1500, limit: int = 5
) -> list[dict] | None:
    """REAL venues near a point from OpenStreetMap (Overpass).

    Returns [] when the area genuinely has no such venue, and None when the
    SEARCH ITSELF failed (Overpass is a free public API and 504s under load).
    Those two are very different and must never be conflated: reporting a
    timeout as "no cafes here" tells the user something false about a real
    place. Only named places are returned, and nothing is ever invented.
    """
    kind = kind if kind in VENUE_KINDS else "cafe"
    query = (
        f'[out:json][timeout:15];'
        f'node(around:{radius_m},{lat},{lon})["amenity"="{kind}"]["name"];'
        f'out body {max(limit * 4, 20)};'
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
        name = el.get("tags", {}).get("name")
        if not name:
            continue
        v_lat, v_lon = el["lat"], el["lon"]
        venues.append({
            "name": name,
            "kind": kind,
            "distance_m": distance_m((lat, lon), (v_lat, v_lon)),
            "map_url": f"https://www.openstreetmap.org/?mlat={v_lat}&mlon={v_lon}#map=18/{v_lat}/{v_lon}",
        })
    venues.sort(key=lambda v: v["distance_m"])
    return venues[:limit]


def _search_failed(where: str) -> dict:
    """The map service didn't answer. Say THAT — not 'there are no cafes'."""
    return {
        "search_failed": True,
        "venues": [],
        "note": (f"The map service (OpenStreetMap) did not respond, so the venues near "
                 f"{where} are UNKNOWN — this is NOT the same as there being none. Do "
                 f"not tell the user the area has no places and do not ask them for a "
                 f"different area. Say the venue lookup is temporarily down, and offer "
                 f"to try again in a moment or to let them name the spot themselves."),
    }


def suggest_venues_for_slot(
    members_with_creds: list[tuple[str, Credentials]],
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
                "anchor": None, "venues": [],
                "note": (f"Could not find a place called {near!r} on the map. Ask the "
                         "user to name the area differently, or to add the city."),
            }
        anchor_area = area_name(*point)
        venues = search_venues_near(*point, kind=kind)
        if venues is None:
            return _search_failed(near)
        log.info("[venues] user-named anchor %r=%s (%s) -> %d real venue(s)",
                 near, point, anchor_area, len(venues))
        return {
            "anchor_area": anchor_area,
            "anchored_on": f"the area the user asked for ({near})",
            "venues": venues,
            "note": (None if venues else
                     f"The venue search near {near} returned no {kind}s — tell the "
                     "user honestly; do NOT invent a venue."),
        }

    locations_by_member: dict[str, list[str]] = {}
    for email, creds in members_with_creds:
        locs = get_adjacent_event_locations(creds, slot_start, slot_end)
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
                 f"The venue search near {anchor_area} returned no {kind}s — "
                 "tell the user honestly; do NOT invent a venue."),
    }
