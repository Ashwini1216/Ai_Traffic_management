"""Controlled authority access: requests, review, and revocation.

Authority accounts cannot be created by sign-up. A commuter applies with their official
details; an existing authority reviews the request. Every decision is audit-logged. The
role takes effect at the next sign-in, and a revoked role stops working immediately
because role checks read the account's current role (see security.authority_token).
"""
from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import store
from core import iso_now
from security import authority_token, citizen_token

router = APIRouter()


class AccessRequestInput(BaseModel):
    full_name: str = Field(min_length=3, max_length=120)
    organization: str = Field(min_length=3, max_length=160)
    designation: str = Field(min_length=2, max_length=120)
    official_email: str | None = Field(default=None, max_length=254)
    employee_id: str | None = Field(default=None, max_length=80)
    reason: str = Field(min_length=20, max_length=1000)


class AccessDecision(BaseModel):
    status: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=500)


def _audit(db, action: str, entity_id: str, actor: str, details: dict) -> None:
    db.execute("INSERT INTO audit_log(action,entity_type,entity_id,actor,occurred_at,details_json) VALUES(?,?,?,?,?,?)",
               (action, "account", entity_id, actor, iso_now(), json.dumps(details)))


@router.post("/api/access-requests", status_code=201)
def request_access(payload: AccessRequestInput, actor: str = Depends(citizen_token)):
    if not payload.official_email and not payload.employee_id:
        raise HTTPException(422, "Provide an official email or an employee ID so the request can be verified.")
    if store.one("SELECT request_id FROM authority_requests WHERE username=? AND status='pending'", (actor,)):
        raise HTTPException(409, "You already have a pending request.")
    request_id = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("""INSERT INTO authority_requests(request_id,username,full_name,organization,designation,official_email,
          employee_id,reason,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (request_id, actor, payload.full_name, payload.organization, payload.designation, payload.official_email,
                    payload.employee_id, payload.reason, "pending", iso_now()))
        _audit(db, "authority_access_requested", actor, actor, {"request_id": request_id})
    return {"request_id": request_id, "status": "pending",
            "message": "An authority will review your request. You will need to sign in again once it is approved."}


@router.get("/api/access-requests/mine")
def my_requests(actor: str = Depends(citizen_token)):
    return {"requests": store.rows("""SELECT request_id,organization,designation,status,created_at,reviewed_at,review_note
      FROM authority_requests WHERE username=? ORDER BY created_at DESC""", (actor,))}


@router.get("/api/access-requests")
def list_requests(status: str | None = Query(default="pending"), actor: str = Depends(authority_token)):
    if status:
        rows = store.rows("SELECT * FROM authority_requests WHERE status=? ORDER BY created_at DESC LIMIT 200", (status,))
    else:
        rows = store.rows("SELECT * FROM authority_requests ORDER BY created_at DESC LIMIT 200")
    return {"requests": rows}


@router.patch("/api/access-requests/{request_id}")
def decide(request_id: str, payload: AccessDecision, actor: str = Depends(authority_token)):
    request = store.one("SELECT * FROM authority_requests WHERE request_id=?", (request_id,))
    if not request:
        raise HTTPException(404, "Request not found.")
    if request["status"] != "pending":
        raise HTTPException(409, "This request has already been decided.")
    with store.connect() as db:
        db.execute("UPDATE authority_requests SET status=?,reviewed_by=?,reviewed_at=?,review_note=? WHERE request_id=?",
                   (payload.status, actor, iso_now(), payload.note.strip() or None, request_id))
        if payload.status == "approved":
            db.execute("UPDATE user_accounts SET role='authority' WHERE username=?", (request["username"],))
        _audit(db, f"authority_access_{payload.status}", request["username"], actor, {"request_id": request_id, "note": payload.note})
    return {"request_id": request_id, "status": payload.status, "username": request["username"]}


@router.get("/api/authorities")
def list_authorities(actor: str = Depends(authority_token)):
    return {"authorities": store.rows("SELECT username,created_at FROM user_accounts WHERE role='authority' ORDER BY username")}


@router.post("/api/authorities/{username}/revoke")
def revoke(username: str, actor: str = Depends(authority_token)):
    if username == actor:
        raise HTTPException(422, "You cannot revoke your own authority access.")
    account = store.one("SELECT role FROM user_accounts WHERE username=?", (username,))
    if not account or account["role"] != "authority":
        raise HTTPException(404, "Authority account not found.")
    if store.one("SELECT COUNT(*) AS n FROM user_accounts WHERE role='authority'")["n"] <= 1:
        raise HTTPException(409, "At least one authority account must remain.")
    with store.connect() as db:
        db.execute("UPDATE user_accounts SET role='citizen' WHERE username=?", (username,))
        _audit(db, "authority_access_revoked", username, actor, {})
    return {"username": username, "role": "citizen"}
