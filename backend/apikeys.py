"""Scoped API keys (read / write / admin) and the /api/ext gateway that enforces them for every API endpoint."""
import re
import secrets
from datetime import datetime, timedelta
from typing import Literal, Optional

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Request, Response
from pydantic import BaseModel, Field

from core import FrasError
from store import iso, now

ph = PasswordHasher()
PREFIX = "fk_live"
PROBE_TOKEN = secrets.token_urlsafe(24)
RANK = {"read": 1, "write": 2, "admin": 3}
EMAIL = r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$"
ADMIN_PATHS = ("console/apikeys", "console/alerts/config", "console/slack/", "console/settings")


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: Literal["read", "write", "admin"]
    ownerEmail: str = Field("", max_length=120, pattern=EMAIL)
    expiresInDays: Optional[int] = Field(None, ge=1, le=3650)


class KeyUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    scope: Optional[Literal["read", "write", "admin"]] = None
    ownerEmail: Optional[str] = Field(None, max_length=120, pattern=EMAIL)
    enabled: Optional[bool] = None


class VerifyBody(BaseModel):
    key: str = Field(min_length=10, max_length=200)


def required_scope(method, path):
    if path.startswith(ADMIN_PATHS) or re.match(r"^console/fleet/[^/]+/routing", path):
        return "admin"
    return "read" if method in ("GET", "HEAD", "OPTIONS") else "write"


def make_token():
    kid = secrets.token_hex(8)
    return kid, f"{PREFIX}.{kid}.{secrets.token_urlsafe(32)}"


def register(api, app, store, db):
    keys, events = db.api_keys, db.api_key_events

    def view(d):
        return {**{k: v for k, v in d.items() if k not in ("_id", "tokenHash")},
                "masked": f"{PREFIX}.{d['keyId']}.••••{d['last4']}",
                "status": "revoked" if d.get("revokedAt") else "expired" if d.get("expiresAt") and d["expiresAt"] < iso() else "active" if d["enabled"] else "disabled"}

    async def insert_key(name, scope, email, expires_days, rotated_from=None):
        kid, token = make_token()
        doc = {"keyId": kid, "tokenHash": ph.hash(token), "last4": token[-4:], "name": name, "scope": scope, "ownerEmail": email or None,
               "enabled": True, "createdAt": iso(), "expiresAt": iso(now() + timedelta(days=expires_days)) if expires_days else None,
               "revokedAt": None, "lastUsedAt": None, "lastUsedPath": None, "useCount": 0, "rotatedFrom": rotated_from}
        await keys.insert_one(dict(doc))
        return {**view(doc), "token": token}

    async def authenticate(raw):
        if not raw:
            raise FrasError("API_KEY_MISSING")
        parts = raw.strip().split(".")
        if len(parts) != 3 or parts[0] != PREFIX:
            raise FrasError("API_KEY_INVALID")
        doc = await keys.find_one({"keyId": parts[1]})
        if not doc:
            raise FrasError("API_KEY_INVALID")
        try:
            ph.verify(doc["tokenHash"], raw.strip())
        except (VerificationError, InvalidHashError):
            raise FrasError("API_KEY_INVALID")
        if doc.get("revokedAt"):
            raise FrasError("API_KEY_REVOKED")
        if not doc["enabled"]:
            raise FrasError("API_KEY_DISABLED")
        if doc.get("expiresAt") and doc["expiresAt"] < iso():
            raise FrasError("API_KEY_REVOKED", "API key has expired.")
        return doc

    async def record(doc, method, path, status, probe=False):
        if probe:
            return
        await keys.update_one({"keyId": doc["keyId"]}, {"$set": {"lastUsedAt": iso(), "lastUsedPath": f"{method} /{path}"}, "$inc": {"useCount": 1}})
        await events.insert_one({"keyId": doc["keyId"], "name": doc["name"], "method": method, "path": f"/{path}", "status": status, "at": iso()})

    # ---------- gateway: /api/ext/<any api path> ----------
    @api.api_route("/ext/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def gateway(path: str, request: Request):
        auth = request.headers.get("authorization", "")
        raw = request.headers.get("x-api-key") or (auth[7:].strip() if auth[:7].lower() == "bearer " else "") or None
        probe = secrets.compare_digest(request.headers.get("x-fras-probe", ""), PROBE_TOKEN)
        doc = await authenticate(raw)
        path = path.lstrip("/")
        need = required_scope(request.method, path)
        if RANK[doc["scope"]] < RANK[need]:
            await record(doc, request.method, path, 403, probe)
            raise FrasError("INSUFFICIENT_SCOPE", f"'{doc['scope']}' key cannot {request.method} /{path} (needs {need})")
        if path.startswith("_probe/"):
            await record(doc, request.method, path, 200, probe)
            return {"ok": True, "scope": doc["scope"], "required": need, "keyId": doc["keyId"]}
        if path.startswith("ext/"):
            raise FrasError("INVALID_STATE", "Nested gateway calls are not allowed")
        fwd = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept", "x-request-id", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-for")}
        fwd.setdefault("x-forwarded-host", request.headers.get("host", ""))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway", timeout=60) as c:
            r = await c.request(request.method, f"/api/{path}", params=request.query_params.multi_items(), content=await request.body(), headers=fwd)
        await record(doc, request.method, path, r.status_code, probe)
        out = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-disposition", "x-checksum-sha256", "x-request-id")}
        out.update({"X-API-Key-Id": doc["keyId"], "X-API-Scope": doc["scope"]})
        return Response(content=r.content, status_code=r.status_code, headers=out)

    # ---------- key management (console) ----------
    async def get_key(kid):
        d = await keys.find_one({"keyId": kid})
        if not d:
            raise FrasError("API_KEY_NOT_FOUND")
        return d

    @api.get("/console/apikeys")
    async def keys_list():
        rows = [view(d) async for d in keys.find({}).sort("createdAt", -1)]
        log = await events.find({}, {"_id": 0}).sort("at", -1).limit(40).to_list(None)
        return {"keys": rows, "events": log, "gateway": "/api/ext", "header": "X-API-Key",
                "scopes": {"read": "GET any endpoint", "write": "read + POST/PUT/DELETE", "admin": "write + key management, alert destinations, Slack routing"}}

    @api.post("/console/apikeys")
    async def keys_create(body: KeyCreate):
        k = await insert_key(body.name, body.scope, body.ownerEmail, body.expiresInDays)
        await store.append_audit("API_KEY_CREATED", "key-admin", metadata={"keyId": k["keyId"], "scope": k["scope"], "name": k["name"]})
        return k

    @api.put("/console/apikeys/{kid}")
    async def keys_update(kid: str, body: KeyUpdate):
        d = await get_key(kid)
        if d.get("revokedAt"):
            raise FrasError("INVALID_STATE", "Revoked keys cannot be edited")
        upd = body.model_dump(exclude_none=True)
        if "ownerEmail" in upd:
            upd["ownerEmail"] = upd["ownerEmail"] or None
        await keys.update_one({"keyId": kid}, {"$set": upd})
        await store.append_audit("API_KEY_UPDATED", "key-admin", metadata={"keyId": kid, **upd})
        return view(await get_key(kid))

    @api.post("/console/apikeys/{kid}/revoke")
    async def keys_revoke(kid: str):
        res = await keys.update_one({"keyId": kid, "revokedAt": None}, {"$set": {"revokedAt": iso(), "enabled": False}})
        if res.matched_count != 1:
            await get_key(kid)
            raise FrasError("INVALID_STATE", "Key already revoked")
        await store.append_audit("API_KEY_REVOKED", "key-admin", metadata={"keyId": kid})
        return view(await get_key(kid))

    @api.post("/console/apikeys/{kid}/rotate")
    async def keys_rotate(kid: str):
        d = await get_key(kid)
        if d.get("revokedAt"):
            raise FrasError("INVALID_STATE", "Revoked keys cannot be rotated")
        days = None
        if d.get("expiresAt"):
            days = max(1, (datetime.fromisoformat(d["expiresAt"].replace("Z", "+00:00")) - datetime.fromisoformat(d["createdAt"].replace("Z", "+00:00"))).days)
        k = await insert_key(d["name"], d["scope"], d.get("ownerEmail"), days, rotated_from=kid)
        await keys.update_one({"keyId": kid}, {"$set": {"revokedAt": iso(), "enabled": False, "rotatedTo": k["keyId"]}})
        await store.append_audit("API_KEY_ROTATED", "key-admin", metadata={"from": kid, "to": k["keyId"]})
        return k

    @api.post("/console/apikeys/verify")
    async def keys_verify(body: VerifyBody):
        probes = [("read", "GET", "v1/validator/status"), ("write", "POST", "_probe/write"), ("admin", "GET", "console/apikeys")]
        out = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway", timeout=30) as c:
            for level, method, p in probes:
                r = await c.request(method, f"/api/ext/{p}", headers={"X-API-Key": body.key.strip(), "X-Fras-Probe": PROBE_TOKEN})
                err = r.json().get("error", {}) if r.status_code >= 400 else {}
                out.append({"level": level, "request": f"{method} /api/ext/{p}", "allowed": r.status_code < 400, "status": r.status_code, "code": err.get("code")})
        key = None
        parts = body.key.strip().split(".")
        if len(parts) == 3:
            d = await keys.find_one({"keyId": parts[1]})
            key = view(d) if d and out[0]["code"] != "API_KEY_INVALID" else None
        return {"key": key, "checks": out, "verifiedAt": iso(), "valid": any(x["allowed"] for x in out)}

    async def seed():
        await keys.create_index("keyId", unique=True)
        await events.create_index("at")

    return seed

