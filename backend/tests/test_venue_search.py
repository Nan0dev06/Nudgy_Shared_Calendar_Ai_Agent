"""Venue search: what OpenStreetMap gives back, and what we do to it.

Written after running the real pipeline against Beirut on 2026-08-03, which is
where every fixture below comes from. Three things were wrong and none of them
were covered by a test, because there was no test file at all:

1. Names came back in the LOCAL language — 'ستاربكس', 'كافي دو براغ' — because
   OSM's `name` tag is local-language. Real data, unreadable in an English UI.
   31 of 40 Hamra cafes carry `name:en`.
2. The same shop appeared twice: 'Caribou Coffee' and 'Café Caribou' are one
   Caribou; 'Café Younès' and 'Café Younes' differ by an accent. A user reading
   a five-item list saw two of them wasted.
3. The query asked for `node` only, so anything mapped as a building outline was
   invisible — which is most parks and cinemas, and some cafes.
"""
import pytest

from app.agent.tools import TOOL_SCHEMAS
from app.tools import locations
from app.tools.locations import (
    DEFAULT_VENUE_KIND, VENUE_KINDS, _dedupe_key, _display_name,
    search_venues_near,
)

HAMRA = (33.8967449, 35.4829649)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _overpass(monkeypatch, elements, capture=None):
    """Stand in for the Overpass POST, optionally capturing the query sent."""
    def fake_post(url, data=None, headers=None, timeout=None):
        if capture is not None:
            capture.append(data["data"])
        return _FakeResponse({"elements": elements})
    monkeypatch.setattr(locations.httpx, "post", fake_post)


def _node(name, lat, lon, name_en=None):
    tags = {"name": name}
    if name_en:
        tags["name:en"] = name_en
    return {"type": "node", "lat": lat, "lon": lon, "tags": tags}


# --------------------------------------------------------------- display name

@pytest.mark.parametrize("tags, expected", [
    ({"name": "ستاربكس", "name:en": "Starbucks"}, "Starbucks"),
    ({"name": "سيتي كافه"}, "سيتي كافه"),          # no name:en -> keep the real one
    ({"name": "x", "int_name": "Ex"}, "Ex"),        # int_name beats bare name
    ({"name": "  Padded  "}, "Padded"),
    ({}, None),
    (None, None),
])
def test_display_name_prefers_english_but_never_drops_a_venue(tags, expected):
    assert _display_name(tags) == expected


# -------------------------------------------------------------------- dedupe

@pytest.mark.parametrize("a, b", [
    ("Caribou Coffee", "Café Caribou"),   # word order + a generic word
    ("Café Younès", "Café Younes"),       # one accent apart
    ("The Coffee House", "Coffee House"), # leading article
    ("St. Honoré", "St Honore"),          # punctuation + accent
])
def test_the_same_place_spelled_two_ways_collapses(a, b):
    assert _dedupe_key(a) == _dedupe_key(b)


@pytest.mark.parametrize("a, b", [
    ("Urbanista", "Latte Art"),
    ("Café Younes", "Café Caribou"),
    ("The Coffee Shop", "The Coffee House"),  # generic-only: must NOT merge
])
def test_different_places_stay_separate(a, b):
    assert _dedupe_key(a) != _dedupe_key(b)


def test_duplicates_are_dropped_and_the_nearest_survives(monkeypatch):
    _overpass(monkeypatch, [
        _node("كاريبو كفي", 33.8990, 35.4830, name_en="Café Caribou"),
        _node("كاريبو كوفي", 33.8968, 35.4830, name_en="Caribou Coffee"),
        _node("Urbanista", 33.8969, 35.4831),
    ])
    venues = search_venues_near(*HAMRA, kind="cafe", limit=5)

    names = [v["name"] for v in venues]
    assert names.count("Caribou Coffee") + names.count("Café Caribou") == 1
    assert "Urbanista" in names
    assert names[0] == "Caribou Coffee", "the nearer of the twins must win"


def test_far_apart_branches_of_a_chain_both_survive(monkeypatch):
    """Two real branches are not a mapping duplicate. ~1.4km apart here."""
    _overpass(monkeypatch, [
        _node("Starbucks", 33.8967, 35.4830, name_en="Starbucks"),
        _node("Starbucks", 33.9090, 35.4830, name_en="Starbucks"),
    ])
    venues = search_venues_near(*HAMRA, kind="cafe", radius_m=3000, limit=5)
    assert len(venues) == 2


def test_the_real_hamra_caribou_pair_is_left_alone(monkeypatch):
    """Documents a real, measured, genuinely ambiguous case — 295 m apart.

    These are the actual coordinates Overpass returned on 2026-08-03. Two
    spellings of Caribou that far apart could be one shop mapped twice or two
    branches on a dense street, and the data does not say which. This test
    exists so that if someone widens DUPLICATE_RADIUS_M, they do it knowing
    exactly which pair they are choosing to merge — not by accident.
    """
    _overpass(monkeypatch, [
        _node("كاريبو كوفي", 33.89570, 35.48119, name_en="Caribou Coffee"),
        _node("كاريبو كفي", 33.89530, 35.48435, name_en="Café Caribou"),
    ])
    venues = search_venues_near(*HAMRA, kind="cafe", radius_m=1500, limit=5)
    assert len(venues) == 2, (
        "295 m is outside DUPLICATE_RADIUS_M by design — see the note there"
    )


# ------------------------------------------------------------------ the query

def test_ways_and_relations_are_read_from_center(monkeypatch):
    """A park or cinema is almost always an outline, not a point."""
    _overpass(monkeypatch, [
        {"type": "way", "center": {"lat": 33.8968, "lon": 35.4831},
         "tags": {"name": "حديقة الصنائع", "name:en": "Sanayeh Garden"}},
        {"type": "relation", "center": {"lat": 33.8970, "lon": 35.4833},
         "tags": {"name": "Horsh Beirut"}},
    ])
    venues = search_venues_near(*HAMRA, kind="park", limit=5)
    assert [v["name"] for v in venues] == ["Sanayeh Garden", "Horsh Beirut"]


def test_an_element_with_no_coordinates_is_skipped_not_crashed(monkeypatch):
    _overpass(monkeypatch, [
        {"type": "way", "tags": {"name": "Nowhere"}},   # no center at all
        _node("Real Place", 33.8968, 35.4831),
    ])
    assert [v["name"] for v in search_venues_near(*HAMRA)] == ["Real Place"]


def test_query_uses_the_right_osm_tag_for_the_kind(monkeypatch):
    """A park is `leisure`, not `amenity` — assuming otherwise found nothing."""
    sent = []
    _overpass(monkeypatch, [], capture=sent)

    search_venues_near(*HAMRA, kind="park")
    assert '["leisure"="park"]' in sent[0]
    assert sent[0].startswith("[out:json]") and "nwr(around:" in sent[0]
    assert "out center" in sent[0]

    search_venues_near(*HAMRA, kind="cinema")
    assert '["amenity"="cinema"]' in sent[1]


def test_an_unknown_kind_falls_back_to_the_default(monkeypatch):
    sent = []
    _overpass(monkeypatch, [], capture=sent)
    search_venues_near(*HAMRA, kind="nightclub-that-we-do-not-support")
    key, value = VENUE_KINDS[DEFAULT_VENUE_KIND]
    assert f'["{key}"="{value}"]' in sent[0]


# ------------------------------------------------- failure vs genuinely empty

def test_a_failed_search_returns_none_not_an_empty_list(monkeypatch):
    """The distinction the whole feature rests on: 'the map service is down' is
    not 'there are no cafes here'. Telling a user the second when the first is
    true says something false about a real place."""
    def boom(*a, **kw):
        raise RuntimeError("overpass 504")
    monkeypatch.setattr(locations.httpx, "post", boom)
    monkeypatch.setattr(locations.time, "sleep", lambda _s: None)

    assert search_venues_near(*HAMRA) is None


def test_an_empty_area_returns_an_empty_list(monkeypatch):
    _overpass(monkeypatch, [])
    assert search_venues_near(*HAMRA) == []


def test_unnamed_elements_are_never_offered(monkeypatch):
    _overpass(monkeypatch, [
        {"type": "node", "lat": 33.8968, "lon": 35.4831, "tags": {"amenity": "cafe"}},
        _node("Named", 33.8969, 35.4832),
    ])
    assert [v["name"] for v in search_venues_near(*HAMRA)] == ["Named"]


# --------------------------------------------------------------- wiring guard

def test_the_model_is_offered_exactly_the_kinds_we_can_search():
    """A hardcoded enum in tools.py would drift the day a kind is added, and the
    model would keep offering the old set."""
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "suggest_venues")
    assert schema["input_schema"]["properties"]["kind"]["enum"] == sorted(VENUE_KINDS)


def test_the_user_agent_identifies_the_app():
    """Nominatim's usage policy blocks generic agents. It used to say
    'nudgy-hackathon-demo/1.0'."""
    ua = locations.HTTP_HEADERS["User-Agent"]
    assert "hackathon" not in ua.lower()
    assert "http" in ua, "the policy wants a way to contact whoever runs this"
