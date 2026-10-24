"""Sign-in security: pausing after wrong passwords, the authority portal, and password changes."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import AUTHORITY_LOGIN, bearer, register

PASSWORD = "citizen-password-1"


@pytest.fixture(autouse=True)
def no_earlier_attempts(client):
    """All test requests come from one address, so each test starts and ends with no attempts recorded."""
    import store

    store.execute("DELETE FROM login_attempts")
    yield
    store.execute("DELETE FROM login_attempts")


def new_commuter(client) -> tuple[str, dict]:
    email = f"guard_{uuid.uuid4().hex[:8]}@example.com"
    return email, register(client, email)[1]


def sign_in(client, password, **who):
    return client.post("/api/auth/login", json={**who, "password": password})


def test_sign_in_pauses_after_five_wrong_passwords_then_resumes(client):
    email, _ = new_commuter(client)
    for _ in range(5):
        assert sign_in(client, "wrong-password-1", email=email).status_code == 401
    paused = sign_in(client, PASSWORD, email=email)
    assert paused.status_code == 429 and "Sign-in is paused; try again in 15 minutes" in paused.json()["detail"]
    assert 14 * 60 < int(paused.headers["Retry-After"]) <= 15 * 60
    import store

    store.execute("UPDATE login_attempts SET attempted_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),))
    assert sign_in(client, PASSWORD, email=email).status_code == 200, "the pause ends after 15 minutes"


def test_a_correct_sign_in_resets_the_count(client):
    email, _ = new_commuter(client)
    for _ in range(4):
        sign_in(client, "wrong-password-1", email=email)
    assert sign_in(client, PASSWORD, email=email).status_code == 200
    for _ in range(4):
        assert sign_in(client, "wrong-password-1", email=email).status_code == 401
    assert sign_in(client, PASSWORD, email=email).status_code == 200


def test_the_username_and_email_of_one_account_share_one_count(client):
    for number in range(5):
        who = {"username": AUTHORITY_LOGIN["username"]} if number % 2 else {"email": "officer@example.gov.in"}
        assert sign_in(client, "wrong-password-1", **who).status_code == 401
    assert sign_in(client, AUTHORITY_LOGIN["password"], username=AUTHORITY_LOGIN["username"]).status_code == 429


def test_one_network_address_is_limited_across_accounts(client, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "2")
    for number in range(8):
        assert sign_in(client, "x", email=f"nobody{number}@example.com").status_code == 401
    refused = sign_in(client, "x", email="someone-else@example.com")
    assert refused.status_code == 429 and "this network address" in refused.json()["detail"]


def test_the_authority_sign_in_gives_no_token_to_a_commuter(client):
    email, _ = new_commuter(client)
    refused = client.post("/api/auth/login", json={"email": email, "password": PASSWORD, "portal": "authority"})
    assert refused.status_code == 403 and "access_token" not in refused.json()
    assert refused.json()["detail"] == "This account is not an authority account."
    officer = client.post("/api/auth/login", json={**AUTHORITY_LOGIN, "portal": "citizen"})
    assert officer.status_code == 403 and "authority sign-in" in officer.json()["detail"]
    assert client.post("/api/auth/login", json={**AUTHORITY_LOGIN, "portal": "authority"}).status_code == 200


def test_changing_the_password_ends_the_other_sessions(client):
    email, first = new_commuter(client)
    second = bearer(sign_in(client, PASSWORD, email=email).json()["access_token"])
    change = {"current_password": PASSWORD, "new_password": "a-new-password-2"}
    wrong = client.post("/api/auth/password", json={**change, "current_password": "not-the-password"}, headers=first)
    assert wrong.status_code == 403 and wrong.json()["detail"] == "The current password is incorrect."
    same = client.post("/api/auth/password", json={**change, "new_password": PASSWORD}, headers=first)
    assert same.status_code == 422
    changed = client.post("/api/auth/password", json=change, headers=first)
    assert changed.status_code == 200, changed.text
    me = client.get("/api/auth/me", headers=bearer(changed.json()["access_token"]))
    assert me.status_code == 200 and me.json()["password_changed_at"]
    for old in (first, second):
        ended = client.get("/api/auth/me", headers=old)
        assert ended.status_code == 401 and "password was changed" in ended.json()["detail"]
    assert sign_in(client, PASSWORD, email=email).status_code == 401
    assert sign_in(client, "a-new-password-2", email=email).status_code == 200


def test_authority_passwords_need_twelve_characters(client, appmod, monkeypatch):
    token = sign_in(client, AUTHORITY_LOGIN["password"], **{"username": AUTHORITY_LOGIN["username"]}).json()["access_token"]
    short = client.post("/api/auth/password", json={"current_password": AUTHORITY_LOGIN["password"], "new_password": "eleven-char"},
                        headers=bearer(token))
    assert short.status_code == 422 and "at least 12 characters" in short.json()["detail"]
    monkeypatch.setenv("AUTHORITY_USERNAME", f"officer_{uuid.uuid4().hex[:6]}")
    monkeypatch.setenv("AUTHORITY_PASSWORD", "short-pass")
    monkeypatch.setenv("AUTHORITY_EMAIL", "")
    with pytest.raises(RuntimeError, match="at least 12 characters"):
        appmod.startup()


def test_authorities_see_recent_authority_sign_ins_only(client, authority, citizen):
    sign_in(client, "wrong-password-1", username=AUTHORITY_LOGIN["username"])
    sign_in(client, AUTHORITY_LOGIN["password"], username=AUTHORITY_LOGIN["username"])
    email, _ = new_commuter(client)
    sign_in(client, PASSWORD, email=email)
    history = client.get("/api/auth/sign-ins", headers=authority).json()
    assert [row["outcome"] for row in history["sign_ins"]] == ["signed_in", "wrong_password"]
    assert {row["username"] for row in history["sign_ins"]} == {AUTHORITY_LOGIN["username"]}
    assert history["max_failures"] == 5 and history["pause_minutes"] == 15
    assert client.get("/api/auth/sign-ins", headers=citizen).status_code == 403
