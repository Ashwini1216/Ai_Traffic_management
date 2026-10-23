"""Address search results and how Google's refusals are explained to the user."""
import io
import json
import urllib.error

import fake_google


def test_address_search_returns_places(client, citizen):
    found = client.get("/api/geocode?q=Indiranagar", headers=citizen)
    assert found.status_code == 200 and found.json()["results"][0]["latitude"] == 12.9719
    assert client.get("/api/geocode?q=nowhere%20road", headers=citizen).json()["results"] == []


def test_a_refused_address_search_states_googles_reason(client, citizen):
    refused = client.get("/api/geocode?q=denied%20place", headers=citizen)
    assert refused.status_code == 502
    assert "You must enable Billing" in refused.json()["detail"]


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
        assert exc.status_code == 502 and "API key not valid" in exc.detail
    else:
        raise AssertionError("the refusal was not reported")
