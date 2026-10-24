"""Live tracking: a hazard ahead prompts a route check, a clearly better route is offered, and the
journey can switch to it."""
import fake_google
from test_07_routes_trips_journeys import _route_query

ROUTE = [[77.6412, 12.9716], [77.6300, 12.9550], [77.6233, 12.9176]]   # lon, lat


def start(client, headers, coordinates, destination_lat, destination_lon) -> str:
    journey = client.post("/api/journeys", json={"coordinates": coordinates, "destination_lat": destination_lat,
                                                 "destination_lon": destination_lon}, headers=headers)
    assert journey.status_code == 201, journey.text
    return journey.json()["journey_id"]


def test_an_accident_ahead_prompts_a_route_check(client, citizen, authority):
    report = client.post("/api/incidents", json={"category": "accident", "description": "Two cars collided in the left lane",
                                                 "latitude": 12.9550, "longitude": 77.6300}, headers=citizen).json()
    client.patch(f"/api/incidents/{report['incident_id']}", json={"status": "verified", "official_note": ""}, headers=authority)
    try:
        journey = start(client, citizen, ROUTE, 12.9176, 77.6233)
        update = client.post(f"/api/journeys/{journey}/position", json={"latitude": 12.9705, "longitude": 77.6404, "accuracy_m": 15},
                             headers=citizen).json()
        assert [a["category"] for a in update["alerts"]] == ["accident"]
        assert update["suggestion"]["message"].startswith("Accident is about") and "check for a better route" in update["suggestion"]["message"]
    finally:
        client.patch(f"/api/incidents/{report['incident_id']}", json={"status": "resolved", "official_note": ""}, headers=authority)


def test_a_clearly_better_route_is_offered_and_can_be_followed(client, citizen, segments, monkeypatch):
    monkeypatch.setitem(fake_google.TRAFFIC, "direct", 1.0)
    monkeypatch.setitem(fake_google.TRAFFIC, "alternative", 1.8)   # the curved route is now 19 minutes slower
    query = _route_query(segments, "100 Feet Road", "Silk Board Junction")
    routes = client.get("/api/routes", params=query, headers=citizen).json()["routes"]
    slow = max(routes, key=lambda r: r["baseline_duration_s"])["geometry"]["coordinates"]
    journey = start(client, citizen, slow, query["destination_lat"], query["destination_lon"])
    here = {"latitude": slow[0][1], "longitude": slow[0][0], "accuracy_m": 10}
    check = client.post(f"/api/journeys/{journey}/better-route", json=here, headers=citizen)
    assert check.status_code == 200, check.text
    offer = check.json()
    assert offer["better"] is True and "saves about 19 minutes" in offer["message"], offer["message"]
    assert client.post(f"/api/journeys/{journey}/better-route", json=here, headers=citizen).status_code == 429, "checks are rate limited"
    switched = client.post(f"/api/journeys/{journey}/route", json={"coordinates": offer["route"]["geometry"]["coordinates"]}, headers=citizen)
    assert switched.status_code == 200
    middle = offer["route"]["geometry"]["coordinates"][10]
    update = client.post(f"/api/journeys/{journey}/position", json={"latitude": middle[1], "longitude": middle[0], "accuracy_m": 10},
                         headers=citizen).json()
    assert update["status"] == "on_route", "positions are now checked against the new route"


def test_no_change_is_offered_when_the_route_is_still_good(client, citizen, segments):
    query = _route_query(segments, "Trinity Circle", "Hebbal Flyover")
    best = client.get("/api/routes", params=query, headers=citizen).json()["routes"][0]["geometry"]["coordinates"]
    journey = start(client, citizen, best, query["destination_lat"], query["destination_lon"])
    offer = client.post(f"/api/journeys/{journey}/better-route", json={"latitude": best[0][1], "longitude": best[0][0]}, headers=citizen).json()
    assert offer["better"] is False and offer["message"].startswith("Your route is still")
    client.post(f"/api/journeys/{journey}/end", headers=citizen)
    assert client.post(f"/api/journeys/{journey}/better-route", json={"latitude": best[0][1], "longitude": best[0][0]},
                       headers=citizen).status_code == 409
