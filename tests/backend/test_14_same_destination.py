"""How many other people plan to reach the same destination around the same time."""
from datetime import datetime, timedelta, timezone

from conftest import register

# A destination that no other test uses, near 100 Feet Road in Indiranagar.
PLACE = (12.9790, 77.6440)


def plan(client, headers, departure: datetime, destination=PLACE):
    trip = client.post("/api/trips", json={"origin": "Trinity Circle", "destination": "Indiranagar lake", "origin_lat": 12.9727,
                                           "origin_lon": 77.6196, "destination_lat": destination[0], "destination_lon": destination[1],
                                           "departure_at": departure.isoformat()}, headers=headers)
    assert trip.status_code == 201, trip.text
    return trip.json()


def test_travellers_heading_to_the_same_place_are_counted_and_small_groups_hidden(client, authority):
    soon = datetime.now(timezone.utc) + timedelta(hours=5)
    people = [register(client)[1] for _ in range(4)]
    mine = plan(client, people[0], soon)
    assert mine["same_destination"]["travellers"] is None and mine["same_destination"]["fewer_than"] == 3
    trips = [plan(client, people[1], soon + timedelta(minutes=20)), plan(client, people[2], soon - timedelta(minutes=30))]
    plan(client, people[2], soon + timedelta(minutes=10))                    # the same person twice counts once
    plan(client, register(client)[1], soon + timedelta(hours=3))             # too much later
    plan(client, register(client)[1], soon, destination=(12.9900, 77.6440))  # more than 500 m away
    plan(client, people[3], soon + timedelta(minutes=45))

    def mine_now() -> dict:
        listed = client.get("/api/trips", headers=people[0]).json()["trips"]
        return next(t for t in listed if t["trip_id"] == mine["trip_id"])["same_destination"]

    assert mine_now()["travellers"] == 3 and mine_now()["window_minutes"] == 60
    # The count follows other people's plans: a cancelled trip no longer counts.
    client.patch(f"/api/trips/{trips[0]['trip_id']}/cancel", json={}, headers=people[1])
    assert mine_now() == {"travellers": None, "fewer_than": 3, "radius_m": 500, "window_minutes": 60}

    destinations = client.get("/api/demand?hours=24", headers=authority).json()["destinations"]
    near = [d for d in destinations if abs(d["latitude"] - PLACE[0]) < 0.005 and abs(d["longitude"] - PLACE[1]) < 0.005]
    assert near and near[0]["travellers"] >= 3 and "Indiranagar" in near[0]["near"]
    assert "Indiranagar lake" not in str(destinations), "what people typed is never shown to authorities"
