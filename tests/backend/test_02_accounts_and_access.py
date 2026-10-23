"""Accounts, roles, and the authority-access workflow."""
import uuid

from conftest import AUTHORITY_LOGIN, bearer, register


def test_registration_needs_an_email_and_only_creates_commuters(client):
    email = f"Asha.Rao+{uuid.uuid4().hex[:6]}@Example.com"
    created = client.post("/api/auth/register", json={"email": email, "password": "citizen-password-1", "role": "authority"})
    assert created.status_code == 201, created.text
    user = created.json()["user"]
    assert user["role"] == "citizen" and user["email"] == email.casefold() and user["username"].startswith("asha_rao")
    assert client.post("/api/auth/register", json={"email": email.upper(), "password": "citizen-password-1"}).status_code == 409
    assert client.post("/api/auth/register", json={"email": "not-an-email", "password": "citizen-password-1"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": f"x{uuid.uuid4().hex[:6]}@example.com", "password": "short"}).status_code == 422
    assert client.post("/api/auth/register", json={"username": "no_email_given", "password": "citizen-password-1"}).status_code == 422


def test_people_sign_in_with_their_email(client):
    email = f"meera_{uuid.uuid4().hex[:6]}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": "citizen-password-1"})
    signed_in = client.post("/api/auth/login", json={"email": email.upper(), "password": "citizen-password-1"})
    assert signed_in.status_code == 200 and signed_in.json()["user"]["email"] == email
    assert client.get("/api/auth/me", headers=bearer(signed_in.json()["access_token"])).json()["email"] == email
    wrong = client.post("/api/auth/login", json={"email": email, "password": "not-the-password"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Email or password is incorrect."
    assert client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "citizen-password-1"}).status_code == 422


def test_the_authority_from_env_signs_in_with_username_or_email(client):
    by_username = client.post("/api/auth/login", json=AUTHORITY_LOGIN)
    by_email = client.post("/api/auth/login", json={"email": "Officer@Example.gov.in", "password": AUTHORITY_LOGIN["password"]})
    assert by_username.status_code == 200 and by_email.status_code == 200, (by_username.text, by_email.text)
    assert by_email.json()["user"]["role"] == "authority" and by_email.json()["user"]["username"] == AUTHORITY_LOGIN["username"]


def test_authority_pages_reject_commuters_and_anonymous_users(client, citizen):
    for path in ("/api/models", "/api/incidents", "/api/demand", "/api/quarantine", "/api/jobs", "/api/access-requests", "/api/cameras"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=citizen).status_code == 403, path


def test_paid_google_services_need_a_signed_in_user(client):
    assert client.get("/api/geocode?q=Indiranagar").status_code == 401
    assert client.get("/api/routes?origin_lat=12.97&origin_lon=77.64&destination_lat=12.92&destination_lon=77.62").status_code == 401
    assert client.post("/api/weather/fetch?latitude=12.97&longitude=77.64").status_code == 401


def test_access_request_needs_verifiable_details(client, citizen):
    body = {"full_name": "Asha Rao", "organization": "Bengaluru Traffic Police", "designation": "Sub-inspector",
            "reason": "I coordinate incident response for the south division."}
    assert client.post("/api/access-requests", json=body, headers=citizen).status_code == 422


def test_access_request_approval_then_revocation(client, authority):
    email = f"officer_{uuid.uuid4().hex[:6]}@example.com"
    name, headers = register(client, email)
    body = {"full_name": "Asha Rao", "organization": "Bengaluru Traffic Police", "designation": "Sub-inspector",
            "official_email": "asha.rao@example.gov.in", "reason": "I coordinate incident response for the south division."}
    assert client.post("/api/access-requests", json=body, headers=headers).status_code == 201
    assert client.post("/api/access-requests", json=body, headers=headers).status_code == 409
    pending = client.get("/api/access-requests?status=pending", headers=authority).json()["requests"]
    request = next(r for r in pending if r["username"] == name)
    decided = client.patch(f"/api/access-requests/{request['request_id']}", json={"status": "approved"}, headers=authority)
    assert decided.status_code == 200, decided.text
    assert client.patch(f"/api/access-requests/{request['request_id']}", json={"status": "rejected"}, headers=authority).status_code == 409
    # The new authority signs in again and gets the authority role.
    login = client.post("/api/auth/login", json={"email": email, "password": "citizen-password-1"}).json()
    assert login["user"]["role"] == "authority"
    new_authority = bearer(login["access_token"])
    assert client.get("/api/models", headers=new_authority).status_code == 200
    # Revocation takes effect immediately, even for a token issued before it.
    assert client.post(f"/api/authorities/{name}/revoke", headers=authority).status_code == 200
    assert client.get("/api/models", headers=new_authority).status_code in (401, 403)


def test_an_authority_cannot_revoke_itself(client, authority):
    assert client.post(f"/api/authorities/{AUTHORITY_LOGIN['username']}/revoke", headers=authority).status_code == 422
