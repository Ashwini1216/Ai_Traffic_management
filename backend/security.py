"""Accounts, tokens, and role checks shared by every API module."""
from __future__ import annotations

import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException
from jose import JWTError, jwt
from passlib.context import CryptContext

import core  # noqa: F401  (loads .env before the settings below are read)

JWT_ALGORITHM = "HS256"
_configured_jwt_secret = os.getenv("JWT_SECRET_KEY", "")
JWT_SECRET = _configured_jwt_secret if _configured_jwt_secret and not _configured_jwt_secret.startswith("REPLACE_") else secrets.token_urlsafe(48)
PASSWORDS = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")


def create_access_token(username: str, role: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    return jwt.encode({"sub": username, "role": role, "exp": expires}, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_bearer(authorization: str | None) -> dict:
    provided = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not provided:
        raise HTTPException(401, "Sign in is required.")
    try:
        claims = jwt.decode(provided, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if not claims.get("sub") or not claims.get("role"):
            raise HTTPException(401, "Invalid account token.")
        return claims
    except JWTError as exc:
        raise HTTPException(401, "Invalid or expired account token.") from exc


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
