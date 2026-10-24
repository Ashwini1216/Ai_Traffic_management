"""Advance notice before each planned trip: expected traffic, the delay, and better routes or times."""
from datetime import datetime, timedelta, timezone

import fake_google
from conftest import register


def plan(client, headers, segments, minutes_ahead: int) -> dict:
    a, b = segments["100 Feet Road"], segments["Silk Board Junction"]
    trip = client.post("/api/trips", json={"origin": "100 Feet Road", "destination": "Silk Board Junction", "origin_lat": a["latitude"],
                                           "origin_lon": a["longitude"], "destination_lat": b["latitude"], "destination_lon": b["longitude"],
                                           "departure_at": (datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)).isoformat()},
                       headers=headers)
    assert trip.status_code == 201, trip.text
    return trip.json()


def reminders(client, headers) -> list[dict]:
    return [n for n in client.get("/api/notifications", headers=headers).json()["notifications"] if n["kind"] == "departure_reminder"]


def run_job(client, authority) -> dict:
    run = client.post("/api/jobs/trip_reminders/run", headers=authority).json()
    assert run["status"] == "ok", run
    return run["details"]


def test_a_trip_leaving_soon_gets_one_advance_notice(client, authority, segments):
    _, owner = register(client)
    soon = plan(client, owner, segments, 40)
    plan(client, owner, segments, 6 * 60)   # too far ahead for a notice yet
    run_job(client, authority)
    sent = reminders(client, owner)
    assert len(sent) == 1 and sent[0]["trip_id"] == soon["trip_id"]
    assert "100 Feet Road to Silk Board Junction leaves at" in sent[0]["message"] and "Expected traffic:" in sent[0]["message"]
    assert sent[0]["evidence"]["risk_level"] in ("low", "medium", "high")
    run_job(client, authority)
    assert len(reminders(client, owner)) == 1, "the same trip is not reminded twice"


def test_heavy_traffic_in_the_notice_says_how_much_earlier_to_leave(client, authority, segments, monkeypatch):
    monkeypatch.setitem(fake_google.TRAFFIC, "direct", 2.0)
    monkeypatch.setitem(fake_google.TRAFFIC, "alternative", 2.0)
    _, owner = register(client)
    plan(client, owner, segments, 30)
    run_job(client, authority)
    message = reminders(client, owner)[0]["message"]
    assert "Traffic is expected to add about" in message and "leave about" in message and "earlier" in message


def test_a_changed_departure_time_brings_a_new_notice_and_cancelled_trips_none(client, authority, segments):
    _, owner = register(client)
    trip = plan(client, owner, segments, 45)
    run_job(client, authority)
    a, b = segments["100 Feet Road"], segments["Silk Board Junction"]
    moved = client.patch(f"/api/trips/{trip['trip_id']}", json={"origin": "100 Feet Road", "destination": "Silk Board Junction",
                                                                "origin_lat": a["latitude"], "origin_lon": a["longitude"],
                                                                "destination_lat": b["latitude"], "destination_lon": b["longitude"],
                                                                "departure_at": (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()},
                         headers=owner)
    assert moved.status_code == 200, moved.text
    run_job(client, authority)
    assert len(reminders(client, owner)) == 2
    _, other = register(client)
    cancelled = plan(client, other, segments, 20)
    client.patch(f"/api/trips/{cancelled['trip_id']}/cancel", json={}, headers=other)
    run_job(client, authority)
    assert reminders(client, other) == []
