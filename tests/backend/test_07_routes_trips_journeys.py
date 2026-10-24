"""Route risk, planned trips and their notifications, and journey monitoring."""
from datetime import datetime, timedelta, timezone

import fake_google
from conftest import register


def _route_query(segments, origin: str, destination: str, **extra) -> dict:
    a, b = segments[origin], segments[destination]
    return {"origin_lat": a["latitude"], "origin_lon": a["longitude"], "destination_lat": b["latitude"], "destination_lon": b["longitude"], **extra}


def test_routes_are_scored_and_the_best_gets_departure_advice(client, citizen, segments):
    response = client.get("/api/routes", params=_route_query(segments, "100 Feet Road", "Silk Board Junction"), headers=citizen)
    assert response.status_code == 200, response.text
    routes = response.json()["routes"]
    assert len(routes) == 2 and routes[0]["source_type"] == "google_route"
    for route in routes:
        assert 0 <= route["risk_score"] <= 100
        assert set(route["components"]) == {"congestion", "traffic", "traffic_delay", "planned_demand", "incidents", "weather"}
        assert route["risk_level"] in ("low", "medium", "high") and isinstance(route["reasons"], list)
        assert route["roads"], "roads along the route are forecast"
    assert routes[0]["departure_advice"] is not None


def test_leaving_now_does_not_send_a_past_departure_time_to_google(client, citizen, segments):
    fake_google.CALLS.clear()
    client.get("/api/routes", params=_route_query(segments, "Trinity Circle", "Hebbal Flyover"), headers=citizen)
    later = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    client.get("/api/routes", params=_route_query(segments, "Trinity Circle", "Hebbal Flyover", departure_at=later), headers=citizen)
    payloads = [p for kind, p in fake_google.CALLS if kind == "routes"]
    assert "departureTime" not in payloads[0] and "departureTime" in payloads[1]


def test_routes_through_an_official_closure_are_restricted(client, citizen, authority, segments):
    closed = segments["Anil Kumble Circle"]
    notice = client.post("/api/incidents/official", json={"category": "road_closure", "description": "Closed for a public event",
                                                          "latitude": closed["latitude"], "longitude": closed["longitude"], "closure": True}, headers=authority).json()
    try:
        routes = client.get("/api/routes", params=_route_query(segments, "Trinity Circle", "Anil Kumble Circle"), headers=citizen).json()["routes"]
        assert routes[0]["restricted"] is True and any(i["closure"] for i in routes[0]["incidents"])
    finally:
        client.patch(f"/api/incidents/{notice['incident_id']}", json={"status": "resolved"}, headers=authority)


def test_trips_are_private_to_their_owner(client, segments):
    _, owner = register(client)
    _, other = register(client)
    a, b = segments["Ballari Road"], segments["Tumkur Road"]
    trip = client.post("/api/trips", json={"origin": "Ballari Road", "destination": "Tumkur Road", "origin_lat": a["latitude"], "origin_lon": a["longitude"],
                                           "destination_lat": b["latitude"], "destination_lon": b["longitude"],
                                           "departure_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}, headers=owner)
    assert trip.status_code == 201, trip.text
    assert trip.json()["segments"], "the trip is linked to the roads it passes"
    trip_id = trip.json()["trip_id"]
    assert all(t["trip_id"] != trip_id for t in client.get("/api/trips", headers=other).json()["trips"])
    assert client.patch(f"/api/trips/{trip_id}/cancel", json={}, headers=other).status_code in (403, 404)
    assert client.patch(f"/api/trips/{trip_id}/cancel", json={}, headers=owner).status_code == 200


def test_past_departures_are_rejected(client, citizen, segments):
    a, b = segments["Ballari Road"], segments["Tumkur Road"]
    late = client.post("/api/trips", json={"origin": "Ballari Road", "destination": "Tumkur Road", "origin_lat": a["latitude"], "origin_lon": a["longitude"],
                                           "destination_lat": b["latitude"], "destination_lon": b["longitude"],
                                           "departure_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}, headers=citizen)
    assert late.status_code == 422


def test_a_new_closure_on_a_planned_trip_notifies_the_owner_once(client, authority, segments):
    _, owner = register(client)
    a, b = segments["Marathahalli Bridge"], segments["ITPL Main Road"]
    trip = client.post("/api/trips", json={"origin": "Marathahalli Bridge", "destination": "ITPL Main Road", "origin_lat": a["latitude"],
                                           "origin_lon": a["longitude"], "destination_lat": b["latitude"], "destination_lon": b["longitude"],
                                           "departure_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}, headers=owner).json()
    client.post(f"/api/trips/{trip['trip_id']}/reassess", headers=owner)
    middle = {"latitude": (a["latitude"] + b["latitude"]) / 2, "longitude": (a["longitude"] + b["longitude"]) / 2}
    notice = client.post("/api/incidents/official", json={"category": "road_closure", "description": "Underpass closed", **middle, "closure": True}, headers=authority).json()
    try:
        second = client.post(f"/api/trips/{trip['trip_id']}/reassess", headers=owner).json()
        assert second["notification_id"] and "a new closure is on your route" in second["reasons"], second
        assert client.post(f"/api/trips/{trip['trip_id']}/reassess", headers=owner).json()["notification_id"] is None
        inbox = client.get("/api/notifications", headers=owner).json()
        assert inbox["unread"] == 1 and inbox["notifications"][0]["kind"] == "closure_on_route"
        client.post("/api/notifications/read-all", headers=owner)
        assert client.get("/api/notifications", headers=owner).json()["unread"] == 0
    finally:
        client.patch(f"/api/incidents/{notice['incident_id']}", json={"status": "resolved"}, headers=authority)


def test_journey_monitoring_reports_progress_and_problems(client, citizen):
    route = [[77.6412, 12.9716], [77.6300, 12.9550], [77.6233, 12.9176]]   # lon, lat
    journey = client.post("/api/journeys", json={"coordinates": route, "destination_lat": 12.9176, "destination_lon": 77.6233}, headers=citizen)
    assert journey.status_code == 201, journey.text
    path = f"/api/journeys/{journey.json()['journey_id']}/position"
    on_route = client.post(path, json={"latitude": 12.9600, "longitude": 77.6335, "accuracy_m": 20}, headers=citizen).json()
    assert on_route["status"] == "on_route" and 0 < on_route["progress_pct"] < 100
    assert client.post(path, json={"latitude": 12.9600, "longitude": 77.6335, "accuracy_m": 400}, headers=citizen).json()["status"] == "low_accuracy"
    assert client.post(path, json={"latitude": 12.9600, "longitude": 77.6600, "accuracy_m": 20}, headers=citizen).json()["status"] == "off_route"
    assert client.post(path, json={"latitude": 12.9177, "longitude": 77.6234, "accuracy_m": 15}, headers=citizen).json()["status"] == "arrived"
