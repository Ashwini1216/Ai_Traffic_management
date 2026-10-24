"""Travel risk: Google's traffic delay, weather, the reasons behind a score, and the travel notes
(safer route, high risk, leave earlier)."""
from datetime import datetime, timedelta, timezone

import pytest

import fake_google
from test_07_routes_trips_journeys import _route_query


@pytest.fixture
def traffic(monkeypatch):
    """Set how many times longer than free flow each fake Google route takes."""
    def set_traffic(direct: float, alternative: float) -> None:
        monkeypatch.setitem(fake_google.TRAFFIC, "direct", direct)
        monkeypatch.setitem(fake_google.TRAFFIC, "alternative", alternative)
    return set_traffic


def test_heavy_traffic_raises_the_risk_and_says_how_much_earlier_to_leave(client, citizen, segments, traffic):
    traffic(2.0, 2.0)  # every route takes twice as long as on a clear road
    body = client.get("/api/routes", params=_route_query(segments, "100 Feet Road", "Silk Board Junction"), headers=citizen).json()
    best = body["routes"][0]
    assert best["components"]["traffic_delay"] == 1.0 and best["components"]["traffic"] == 1.0
    assert best["traffic_delay_s"] in (780, 1080) and best["risk_score"] >= 90 and best["risk_level"] == "high"
    assert any(reason.startswith("Traffic is adding about") for reason in best["reasons"])
    kinds = {note["kind"]: note for note in body["travel_notes"]}
    assert kinds["leave_early"]["delay_minutes"] == round(best["traffic_delay_s"] / 60)
    assert "right now" in kinds["leave_early"]["message"] and "high_risk" in kinds
    # Departure times are compared on the forecast evidence, since the delay is known only for now.
    advice = best["departure_advice"]
    if advice["supported"]:
        assert advice["options"][0]["risk_score"] == best["forecast_risk_score"] < best["risk_score"]


def test_a_later_trip_uses_the_expected_delay(client, citizen, segments, traffic):
    traffic(1.5, 1.5)
    later = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    body = client.get("/api/routes", params=_route_query(segments, "Trinity Circle", "Hebbal Flyover", departure_at=later), headers=citizen).json()
    note = next(n for n in body["travel_notes"] if n["kind"] == "leave_early")
    assert "is expected to add" in note["message"] and "arrive on time" in note["message"]


def test_light_traffic_gives_no_leave_early_note(client, citizen, segments, traffic):
    traffic(1.05, 1.05)
    body = client.get("/api/routes", params=_route_query(segments, "100 Feet Road", "Silk Board Junction"), headers=citizen).json()
    assert all(note["kind"] != "leave_early" for note in body["travel_notes"])
    assert body["routes"][0]["traffic_delay_s"] < 120


def test_hazards_on_the_route_are_named_in_the_reasons(client, citizen, authority, segments):
    road = segments["Silk Board Junction"]
    notice = client.post("/api/incidents/official", json={"category": "metro_construction", "description": "Lane closed for metro work",
                                                          "latitude": road["latitude"], "longitude": road["longitude"]}, headers=authority).json()
    try:
        best = client.get("/api/routes", params=_route_query(segments, "100 Feet Road", "Silk Board Junction"), headers=citizen).json()["routes"][0]
        assert any(r.startswith("Metro construction 0 m from the route (official notice)") for r in best["reasons"]), best["reasons"]
    finally:
        client.patch(f"/api/incidents/{notice['incident_id']}", json={"status": "resolved"}, headers=authority)


def test_storms_fog_and_wind_count_as_weather_risk(client, monkeypatch):
    import routing
    import weather

    def forecast(code, rain=0.0, wind=10.0):
        return lambda lat, lon, moment: {"weather_code": str(code), "precipitation_mm": rain, "wind_kph": wind,
                                         "time_gap_minutes": 0, "distance_m": 0}

    moment, point = datetime.now(timezone.utc), (12.97, 77.6)
    for code, rain, wind, expected, reason in ((95, 3.0, 10, 0.5, "Thunderstorm forecast"), (61, 7.5, 10, 0.4, "Heavy rain forecast (7.5 mm in the hour)"),
                                               (61, 1.0, 10, 0.25, "Rain forecast"), (45, 0.0, 10, 0.2, "Fog forecast: lower visibility"),
                                               (1, 0.0, 45, 0.15, "Strong wind forecast (45 km/h)"), (1, 0.0, 10, 0.0, None)):
        monkeypatch.setattr(weather, "forecast_for", forecast(code, rain, wind))
        score, _, reasons = routing._weather_component(point, point, moment)
        assert round(score, 3) == expected and reasons == ([reason] if reason else []), (code, score, reasons)


def route(index, risk, duration, restricted=False, delay=None, reasons=()):
    return {"route_index": index, "risk_score": risk, "risk_level": "high" if risk >= 60 else "medium" if risk >= 30 else "low",
            "baseline_duration_s": duration, "restricted": restricted, "traffic_delay_s": delay, "reasons": list(reasons)}


def test_the_travel_notes_explain_a_safer_route():
    import routing

    notes = routing.travel_notes([route(1, 35, 1500), route(0, 72, 1200)], leave_now=True)
    assert notes == [{"kind": "safer_route", "route_index": 1, "message": "Safer route suggested: the fastest route has high risk "
                      "(72/100); the recommended one lowers it to 35/100 for about 5 more minutes."}]
    closure = routing.travel_notes([route(1, 40, 1260), route(0, 30, 1200, restricted=True)], leave_now=True)
    assert closure[0]["message"].startswith("The fastest route passes an official road closure") and "1 more minute." in closure[0]["message"]
    assert routing.travel_notes([route(1, 30, 1500), route(0, 36, 1200)], leave_now=True) == [], "a small difference is not worth a detour"
    risky = routing.travel_notes([route(0, 75, 1200, reasons=["Heavy congestion expected on Hosur Road around 9:10 am"])], leave_now=False)
    assert risky[0]["kind"] == "high_risk" and "Hosur Road" in risky[0]["message"]
