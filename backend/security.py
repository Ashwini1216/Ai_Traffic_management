"""Accounts, tokens, and role checks shared by every API module."""
from __future__ import annotations

import hmac
import math
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException
from jose import JWTError, jwt
from passlib.context import CryptContext

import core  # also loads .env before the settings below are read

JWT_ALGORITHM = "HS256"
_configured_jwt_secret = os.getenv("JWT_SECRET_KEY", "")
JWT_SECRET = _configured_jwt_secret if _configured_jwt_secret and not _configured_jwt_secret.startswith("REPLACE_") else secrets.token_urlsafe(48)
PASSWORDS = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")
AUTHORITY_PASSWORD_MIN = 12
# Checked when no account matches, so a sign-in takes as long whether or not the account exists.
_NO_ACCOUNT_HASH = PASSWORDS.hash(secrets.token_urlsafe(16))


def create_access_token(username: str, role: str, version: int = 0) -> str:
    """A 12-hour token; `version` is the account's token_version, so a password change ends older sessions."""
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": username, "role": role, "ver": version, "iat": now, "exp": now + timedelta(hours=12)},
                      JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_password(password: str, account: dict | None) -> bool:
    if account is None:
        PASSWORDS.verify(password, _NO_ACCOUNT_HASH)
        return False
    return PASSWORDS.verify(password, account["password_hash"])


# ---------- sign-in protection ----------

def sign_in_limits() -> tuple[int, timedelta]:
    """LOGIN_MAX_FAILURES wrong passwords for one account within LOGIN_LOCKOUT_MINUTES pause its sign-in."""
    failures = max(1, int(os.getenv("LOGIN_MAX_FAILURES") or 5))
    return failures, timedelta(minutes=max(1, int(os.getenv("LOGIN_LOCKOUT_MINUTES") or 15)))


def check_sign_in_allowed(account_key: str, client: str) -> None:
    """Refuse to check a password after too many wrong ones: the limit for one account (a correct
    sign-in resets it), or four times the limit from one network address across all accounts.
    A paused account stays paused even for the right password until the window has passed."""
    import store

    limit, window = sign_in_limits()
    now = datetime.now(timezone.utc)
    since = (now - window).isoformat()
    last_success = store.one("SELECT MAX(attempted_at) AS latest FROM login_attempts WHERE account_key=? AND outcome='signed_in'",
                             (account_key,))["latest"]
    for column, value, start, allowed, whom in (("account_key", account_key, max(since, last_success or since), limit, "this account"),
                                                ("client", client, since, limit * 4, "this network address")):
        failures = store.rows(f"""SELECT attempted_at FROM login_attempts WHERE {column}=? AND outcome='wrong_password'
          AND attempted_at>? ORDER BY attempted_at DESC LIMIT ?""", (value, start, allowed))
        if len(failures) >= allowed:
            wait_s = max(1, math.ceil((core.parse_utc(failures[-1]["attempted_at"]) + window - now).total_seconds()))
            minutes = math.ceil(wait_s / 60)
            raise HTTPException(429, f"Too many wrong passwords for {whom}. Sign-in is paused; try again in "
                                     f"{minutes} minute{'' if minutes == 1 else 's'}.", headers={"Retry-After": str(wait_s)})


def record_sign_in(account_key: str, client: str, outcome: str) -> None:
    import store

    now = datetime.now(timezone.utc)
    with store.connect() as db:
        db.execute("INSERT INTO login_attempts(account_key,client,outcome,attempted_at) VALUES(?,?,?,?)",
                   (account_key, client, outcome, now.isoformat()))
        db.execute("DELETE FROM login_attempts WHERE attempted_at<?", ((now - timedelta(days=90)).isoformat(),))


def _decode_bearer(authorization: str | None) -> dict:
    provided = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not provided:
        raise HTTPException(401, "Sign in is required.")
    try:
        claims = jwt.decode(provided, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(401, "Invalid or expired account token. Sign in again.") from exc
    if not claims.get("sub") or not claims.get("role"):
        raise HTTPException(401, "Invalid account token.")
    import store

    account = store.one("SELECT token_version FROM user_accounts WHERE username=?", (str(claims["sub"]),))
    if account is None:
        raise HTTPException(401, "This account no longer exists.")
    if int(claims.get("ver") or 0) != (account["token_version"] or 0):
        raise HTTPException(401, "This session ended because the account's password was changed. Sign in again.")
    return claims


def _legacy_token(authorization: str | None) -> bool:
    expected = os.getenv("AUTHORITY_TOKEN")
    provided = authorization.removeprefix("Bearer ").strip() if authorization else ""
    return bool(expected and provided and hmac.compare_digest(provided, expected))


def authority_token(authorization: str | None = Header(default=None)) -> str:
    if _legacy_token(authorization):
        return "legacy_authority_token"
    if not authorization:
        raise HTTPException(401, "Sign in with an authority account to perform this action.")
    claims = _decode_bearer(authorization)
    if claims.get("role") != "authority":
        raise HTTPException(403, "Authority role is required for this action.")
    # Tokens last 12 hours, so confirm the account still holds the role (it may have been revoked).
    import store

    account = store.one("SELECT role FROM user_accounts WHERE username=?", (str(claims["sub"]),))
    if not account or account["role"] != "authority":
        raise HTTPException(403, "Authority access for this account has been revoked.")
    return str(claims["sub"])


def citizen_token(authorization: str | None = Header(default=None)) -> str:
    claims = _decode_bearer(authorization)
    if claims.get("role") != "citizen":
        raise HTTPException(403, "A commuter account is required for this action.")
    return str(claims["sub"])


def signed_in_user(authorization: str | None = Header(default=None)) -> str:
    """Any signed-in commuter or authority. Guards billed map services and data writes."""
    if _legacy_token(authorization):
        return "legacy_authority_token"
    return str(_decode_bearer(authorization)["sub"])


def account(authorization: str | None = Header(default=None)) -> dict:
    """The signed-in account's name and role."""
    if _legacy_token(authorization):
        return {"username": "legacy_authority_token", "role": "authority"}
    claims = _decode_bearer(authorization)
    return {"username": str(claims["sub"]), "role": str(claims["role"])}
