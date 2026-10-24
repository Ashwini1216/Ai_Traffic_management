"""Place search (Google first, OpenStreetMap as the free fallback) and how refusals are explained."""
import io
import json
import time
import urllib.error
from datetime import datetime, timedelta, timezone

import fake_google


def test_place_search_finds_places_by_name_with_google(client, citizen):
    found = client.get("/api/geocode?q=Avalahalli", headers=citizen).json()
    assert found["provider"] == "google_places" and found["results"][0]["name"] == "Avalahalli"
    assert abs(found["results"][0]["latitude"] - 12.9457) < 1e-4 and found["attribution"]


def test_a_refused_google_search_falls_back_to_openstreetmap(client, citizen):
    found = client.get("/api/geocode?q=denied%20Avalahalli", headers=citizen).json()
    assert found["provider"] == "openstreetmap" and found["results"][0]["name"] == "Avalahalli"
    assert "OpenStreetMap contributors" in found["attribution"]


def test_places_outside_bengaluru_are_not_offered(client, citizen):
    assert client.get("/api/geocode?q=Delhi%20market", headers=citizen).json()["results"] == []
    assert client.get("/api/geocode?q=nowhere%20road", headers=citizen).json()["results"] == []


def test_the_search_service_can_be_chosen_or_switched_off(client, citizen, monkeypatch):
    monkeypatch.setenv("PLACE_SEARCH", "openstreetmap")
    assert client.get("/api/geocode?q=Avalahalli%20park", headers=citizen).json()["provider"] == "openstreetmap"
    monkeypatch.setenv("PLACE_SEARCH", "google")
    refused = client.get("/api/geocode?q=denied%20place", headers=citizen)
    assert refused.status_code == 502 and "Places API" in refused.json()["detail"]
    monkeypatch.setenv("PLACE_SEARCH", "off")
    assert client.get("/api/geocode?q=Avalahalli", headers=citizen).status_code == 503


def test_without_a_google_key_openstreetmap_is_used(client, citizen, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "")
    found = client.get("/api/geocode?q=Avalahalli%20lake", headers=citizen).json()
    assert found["provider"] == "openstreetmap" and found["results"][0]["name"] == "Avalahalli"


def test_the_map_settings_tell_the_website_which_map_to_use(client, monkeypatch):
    config = client.get("/api/map/config").json()
    assert config["google_routing_configured"] is True and config["google_maps_browser_key"] == ""
    assert config["place_search"] == "auto"
    monkeypatch.setenv("VITE_GOOGLE_MAPS_API_KEY", "REPLACE_WITH_RESTRICTED_BROWSER_KEY")
    assert client.get("/api/map/config").json()["google_maps_browser_key"] == "", "the .env.example placeholder is not a key"
    monkeypatch.setenv("VITE_GOOGLE_MAPS_API_KEY", "browser-key-for-tests")
    assert client.get("/api/map/config").json()["google_maps_browser_key"] == "browser-key-for-tests"


def test_a_trip_can_be_planned_with_place_names_only(client, citizen):
    departure = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    trip = client.post("/api/trips", json={"origin": "Avalahalli", "destination": "100 Feet Road, Indiranagar",
                                           "departure_at": departure}, headers=citizen)
    assert trip.status_code == 201, trip.text
    saved = next(t for t in client.get("/api/trips", headers=citizen).json()["trips"] if t["trip_id"] == trip.json()["trip_id"])
    assert abs(saved["origin_lat"] - 12.9457) < 1e-4 and abs(saved["destination_lat"] - 12.9719) < 1e-4
    moved = client.patch(f"/api/trips/{saved['trip_id']}", json={"origin": "Avalahalli", "destination": "Avalahalli Banashankari",
                                                                  "departure_at": departure}, headers=citizen)
    assert moved.status_code == 200, moved.text
    saved = next(t for t in client.get("/api/trips", headers=citizen).json()["trips"] if t["trip_id"] == saved["trip_id"])
    assert abs(saved["destination_lat"] - 12.9457) < 1e-4, "a changed place name is searched again"
    unknown = client.post("/api/trips", json={"origin": "nowhere lane", "destination": "Avalahalli", "departure_at": departure}, headers=citizen)
    assert unknown.status_code == 422 and "could not find" in unknown.json()["detail"]


def test_an_authority_can_place_a_road_on_the_map_by_name(client, authority, segments):
    road = segments["Ballari Road"]
    try:
        placed = client.post(f"/api/segments/{road['segment_id']}/geocode", headers=authority)
        assert placed.status_code == 200, placed.text
        assert abs(placed.json()["latitude"] - 12.9719) < 1e-4 and placed.json()["location_source"] == "geocoded"
    finally:
        client.patch(f"/api/segments/{road['segment_id']}", json={"latitude": road["latitude"], "longitude": road["longitude"]}, headers=authority)


def test_openstreetmap_requests_are_spaced_out_and_cached():
    import places

    started = time.monotonic()
    places._openstreetmap("spacing check one", 5)
    places._openstreetmap("spacing check two", 5)
    assert time.monotonic() - started >= 1.0, "OpenStreetMap allows at most one request per second"
    before = len([c for c in fake_google.CALLS if c[0] == "openstreetmap"])
    places._openstreetmap("spacing check one", 5)
    assert len([c for c in fake_google.CALLS if c[0] == "openstreetmap"]) == before, "repeated searches come from the cache"


def test_a_refused_route_request_states_googles_reason(monkeypatch):
    import core

    def refuse(request, timeout=None, context=None):
        body = json.dumps({"error": {"code": 403, "message": "API key not valid. Please pass a valid API key."}}).encode()
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body))

    monkeypatch.setattr(core.urllib.request, "urlopen", refuse)
    real = fake_google.ORIGINALS["google_post_json"]
    try:
        real("https://routes.googleapis.com/directions/v2:computeRoutes", {})
    except core.HTTPException as exc:
        assert exc.status_code == 502 and "Google Routes refused the request: API key not valid" in exc.detail
    else:
        raise AssertionError("the refusal was not reported")
