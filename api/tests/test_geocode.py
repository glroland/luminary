import json

from app import geocode


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_urlopen(payload, captured):
    def fake(request, timeout=None):
        captured.append((request, timeout))
        return _FakeResponse(payload)

    return fake


def test_describe_prefers_a_named_place_plus_locality_plus_state():
    address = {
        "tourism": "Grandfather Mountain State Park",
        "town": "Blowing Rock",
        "state": "North Carolina",
        "country": "United States",
    }
    assert geocode._describe(address, "display name unused") == (
        "Grandfather Mountain State Park, Blowing Rock, North Carolina"
    )


def test_describe_handles_a_real_nominatim_protected_area_response():
    # Captured verbatim from a real reverse-geocode of Grandfather Mountain
    # State Park's own coordinates against the public Nominatim instance --
    # `protected_area`, not `tourism`/`leisure`, is the address key it
    # actually uses for a US state park (boundary=protected_area in OSM).
    address = {
        "protected_area": "Grandfather Mountain State Park",
        "village": "Sugar Mountain",
        "county": "Watauga County",
        "state": "North Carolina",
        "ISO3166-2-lvl4": "US-NC",
        "country": "United States",
        "country_code": "us",
    }
    assert geocode._describe(address, "unused") == (
        "Grandfather Mountain State Park, Sugar Mountain, North Carolina"
    )


def test_describe_falls_back_to_locality_and_state_with_no_named_place():
    address = {"town": "Boone", "state": "North Carolina"}
    assert geocode._describe(address, None) == "Boone, North Carolina"


def test_describe_falls_back_to_county_when_no_city_town_or_village_is_present():
    # Captured verbatim from a real reverse-geocode of a rural road near
    # Boone, NC that had no city/town/village in its address at all --
    # without this fallback, _describe produced a bare "North Carolina"
    # with no county context, which is strictly worse than including it.
    address = {
        "road": "Mystery Hill Lane",
        "county": "Watauga County",
        "state": "North Carolina",
        "country": "United States",
    }
    assert geocode._describe(address, "unused") == "Watauga County, North Carolina"


def test_describe_prefers_city_town_or_village_over_county():
    address = {"village": "Example", "county": "Example County", "state": "Example State"}
    assert geocode._describe(address, None) == "Example, Example State"


def test_describe_falls_back_to_display_name_when_address_has_no_curated_fields():
    address = {"postcode": "28605", "road": "Main St"}
    assert geocode._describe(address, "Main St, 28605") == "Main St, 28605"


def test_describe_returns_none_when_nothing_is_available():
    assert geocode._describe({}, None) is None


def test_reverse_geocode_sends_coordinates_and_user_agent(monkeypatch):
    monkeypatch.setattr(geocode, "_rate_limit", lambda: None)
    monkeypatch.setenv("LUMINARY_GEOCODE_USER_AGENT", "test-agent/1.0")

    captured = []
    payload = {"address": {"city": "Boone", "state": "North Carolina"}, "display_name": "unused"}
    monkeypatch.setattr(geocode.urllib.request, "urlopen", _fake_urlopen(payload, captured))

    result = geocode.reverse_geocode(36.2168, -81.6746)

    assert result == "Boone, North Carolina"
    assert len(captured) == 1
    request, timeout = captured[0]
    assert request.headers.get("User-agent") == "test-agent/1.0"
    assert "lat=36.2168" in request.full_url
    assert "lon=-81.6746" in request.full_url
    assert timeout == 10.0


def test_reverse_geocode_returns_none_on_nominatims_own_error_response(monkeypatch):
    monkeypatch.setattr(geocode, "_rate_limit", lambda: None)
    captured = []
    monkeypatch.setattr(
        geocode.urllib.request, "urlopen", _fake_urlopen({"error": "Unable to geocode"}, captured)
    )

    assert geocode.reverse_geocode(0.0, 0.0) is None


def test_rate_limit_sleeps_to_enforce_minimum_interval(monkeypatch):
    # _rate_limit() reads time.monotonic() twice per call (once to compute
    # how long to wait, once to record the request's own timestamp) -- two
    # calls to it need four values here.
    times = iter([100.0, 100.0, 100.2, 100.2])
    monkeypatch.setattr(geocode.time, "monotonic", lambda: next(times))
    sleeps = []
    monkeypatch.setattr(geocode.time, "sleep", lambda seconds: sleeps.append(seconds))
    geocode._last_request_at = 0.0

    geocode._rate_limit()  # "now" = 100.0, nothing to wait for (last_request_at starts at 0)
    geocode._rate_limit()  # "now" = 100.2, less than 1s since the previous call -> must sleep

    assert sleeps == [100.0 + geocode._MIN_REQUEST_INTERVAL_SECONDS - 100.2]
