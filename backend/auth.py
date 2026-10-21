"""Operator login: bcrypt passwords, JWT sessions (revocable), lockout, roles, operator management and route protection."""
import os
import re
import secrets
import string
import uuid
from datetime import timedelta
from typing import Literal, Optional

import bcrypt
import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import core
from core import FrasError
from store import iso, now

ALG = "HS256"
SESSION_HOURS = 12
MAX_FAILS, LOCK_MINUTES = 5, 15
INTERNAL_TOKEN = secrets.token_urlsafe(32)
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
OPEN_GET = re.compile(r"^/api/(|v1/.*|metrics|console/overview|console/fleet|console/fleet/trends|console/upgrades)$")
OPEN_ANY = re.compile(r"^/api/(auth/login|ext/.*|console/slack/oauth/callback)$")
AUTH_ONLY = re.compile(r"^/api/auth/(me|logout|change-password)$")
ADMIN_ONLY = re.compile(r"^/api/console/operators(/.*)?$")


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class PasswordBody(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=200)
    newPassword: str = Field(min_length=8, max_length=200)


class OperatorCreate(BaseModel):
    email: str = Field(max_length=120, pattern=EMAIL)
    name: str = Field("", max_length=80)
    role: Literal["admin", "operator"] = "operator"


class OperatorUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    role: Optional[Literal["admin", "operator"]] = None
    active: Optional[bool] = None


def hash_pw(p):
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def check_pw(p, h):
    try:
        return bcrypt.checkpw(p.encode(), h.encode())
    except ValueError:
        return False


def strong(p):
    return len(p) >= 8 and re.search(r"[A-Za-z]", p) and re.search(r"\d", p)


def temp_password():
    alphabet = string.ascii_letters + string.digits
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(14))
        if strong(p):
            return p


def public_user(u):
    return {k: u.get(k) for k in ("id", "email", "name", "role", "active", "mustChangePassword", "createdAt", "lastLoginAt")}


def register(app, api, store, db):
    users, sessions, attempts = db.users, db.sessions, db.login_attempts
    secret = os.environ["JWT_SECRET"]

    def err(request, code, message=None):
        status = core.ERROR_MAP.get(code, (401, ""))[0]
        return JSONResponse(status_code=status, content=core.error_envelope(getattr(request.state, "request_id", "req_unknown"), code,
                                                                            message or core.ERROR_MAP.get(code, (0, code))[1]))

    async def resolve(request):
        auth = request.headers.get("authorization", "")
        if auth[:7].lower() != "bearer " or auth[7:].startswith("fk_live."):
            return None, "AUTH_REQUIRED"
        try:
            claims = jwt.decode(auth[7:].strip(), secret, algorithms=[ALG])
        except jwt.ExpiredSignatureError:
            return None, "SESSION_EXPIRED"
        except jwt.InvalidTokenError:
            return None, "AUTH_REQUIRED"
        s = await sessions.find_one({"sid": claims.get("sid"), "revokedAt": None})
        u = s and await users.find_one({"id": claims.get("sub"), "active": True})
        if not u:
            return None, "SESSION_EXPIRED"
        return {**u, "sid": s["sid"]}, None

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        path, method = request.url.path, request.method
        if method == "OPTIONS" or not path.startswith("/api") or OPEN_ANY.match(path) or (method in ("GET", "HEAD") and OPEN_GET.match(path)):
            return await call_next(request)
        if secrets.compare_digest(request.headers.get("x-fras-internal", ""), INTERNAL_TOKEN):
            return await call_next(request)
        user, code = await resolve(request)
        if not user:
            return err(request, code)
        if user.get("mustChangePassword") and not AUTH_ONLY.match(path):
            return err(request, "PASSWORD_CHANGE_REQUIRED")
        if ADMIN_ONLY.match(path) and user["role"] != "admin":
            return err(request, "ADMIN_REQUIRED")
        request.state.user = user
        return await call_next(request)

    def client_ip(request):
        return (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()

    async def issue(u):
        sid = secrets.token_urlsafe(24)
        exp = now() + timedelta(hours=SESSION_HOURS)
        await sessions.insert_one({"sid": sid, "userId": u["id"], "createdAt": iso(), "expiresAt": exp, "revokedAt": None})
        return jwt.encode({"sub": u["id"], "sid": sid, "email": u["email"], "role": u["role"], "exp": exp, "type": "access"}, secret, algorithm=ALG)

    @api.post("/auth/login")
    async def login(body: LoginBody, request: Request):
        email = body.email.strip().lower()
        ident = f"{client_ip(request)}:{email}"
        a = await attempts.find_one({"identifier": ident})
        if a and a.get("lockedUntil") and a["lockedUntil"] > iso():
            raise FrasError("ACCOUNT_LOCKED", f"Too many failed attempts. Try again after {a['lockedUntil'][11:16]} UTC.")
        u = await users.find_one({"email": email})
        if not u or not check_pw(body.password, u["passwordHash"]) or not u.get("active", True):
            fails = (a or {}).get("count", 0) + 1
            upd = {"count": fails, "lastAt": iso()}
            if fails >= MAX_FAILS:
                upd = {"count": 0, "lockedUntil": iso(now() + timedelta(minutes=LOCK_MINUTES)), "lastAt": iso()}
            await attempts.update_one({"identifier": ident}, {"$set": upd}, upsert=True)
            await store.append_audit("LOGIN_FAILED", "auth", metadata={"email": email})
            if fails >= MAX_FAILS:
                raise FrasError("ACCOUNT_LOCKED", f"Too many failed attempts. Locked for {LOCK_MINUTES} minutes.")
            raise FrasError("INVALID_CREDENTIALS", "Email or password is incorrect." + (" Account is deactivated." if u and not u.get("active", True) and check_pw(body.password, u["passwordHash"]) else ""))
        await attempts.delete_one({"identifier": ident})
        await users.update_one({"id": u["id"]}, {"$set": {"lastLoginAt": iso()}})
        await store.append_audit("LOGIN_SUCCEEDED", u["email"], metadata={"role": u["role"]})
        return {"token": await issue(u), "expiresInHours": SESSION_HOURS, "user": public_user({**u, "lastLoginAt": iso()})}

    @api.get("/auth/me")
    async def me(request: Request):
        return {"user": public_user(request.state.user)}

    @api.post("/auth/logout")
    async def logout(request: Request):
        await sessions.update_one({"sid": request.state.user["sid"]}, {"$set": {"revokedAt": iso()}})
        return {"loggedOut": True}

    @api.post("/auth/change-password")
    async def change_password(body: PasswordBody, request: Request):
        u = request.state.user
        if not check_pw(body.currentPassword, u["passwordHash"]):
            raise FrasError("INVALID_CREDENTIALS", "Current password is incorrect.")
        if not strong(body.newPassword):
            raise FrasError("WEAK_PASSWORD")
        if body.newPassword == body.currentPassword:
            raise FrasError("WEAK_PASSWORD", "New password must differ from the current one.")
        await users.update_one({"id": u["id"]}, {"$set": {"passwordHash": hash_pw(body.newPassword), "mustChangePassword": False, "passwordChangedAt": iso()}})
        await sessions.update_many({"userId": u["id"], "sid": {"$ne": u["sid"]}, "revokedAt": None}, {"$set": {"revokedAt": iso()}})
        await store.append_audit("PASSWORD_CHANGED", u["email"])
        return {"user": public_user({**u, "mustChangePassword": False})}

    # ---------- operator management (admin) ----------
    async def get_user(uid):
        u = await users.find_one({"id": uid})
        if not u:
            raise FrasError("USER_NOT_FOUND")
        return u

    async def active_admins(exclude=None):
        return await users.count_documents({"role": "admin", "active": True, "id": {"$ne": exclude}})

    @api.get("/console/operators")
    async def operators_list():
        return {"operators": [public_user(u) async for u in users.find({}).sort("createdAt", 1)]}

    @api.post("/console/operators")
    async def operators_create(body: OperatorCreate, request: Request):
        email = body.email.strip().lower()
        if await users.find_one({"email": email}):
            raise FrasError("INVALID_STATE", "An operator with this email already exists")
        pw = temp_password()
        u = {"id": str(uuid.uuid4()), "email": email, "name": body.name.strip() or email.split("@")[0], "role": body.role, "active": True,
             "passwordHash": hash_pw(pw), "mustChangePassword": True, "createdAt": iso(), "createdBy": request.state.user["email"], "lastLoginAt": None}
        await users.insert_one(dict(u))
        await store.append_audit("OPERATOR_CREATED", request.state.user["email"], metadata={"email": email, "role": body.role})
        return {"operator": public_user(u), "temporaryPassword": pw}

    @api.put("/console/operators/{uid}")
    async def operators_update(uid: str, body: OperatorUpdate, request: Request):
        u = await get_user(uid)
        upd = body.model_dump(exclude_none=True)
        demoting = (upd.get("role") == "operator" or upd.get("active") is False) and u["role"] == "admin"
        if uid == request.state.user["id"] and (upd.get("active") is False or upd.get("role") == "operator"):
            raise FrasError("INVALID_STATE", "You cannot deactivate or demote your own account")
        if demoting and await active_admins(exclude=uid) == 0:
            raise FrasError("INVALID_STATE", "At least one active admin is required")
        await users.update_one({"id": uid}, {"$set": upd})
        if upd.get("active") is False:
            await sessions.update_many({"userId": uid, "revokedAt": None}, {"$set": {"revokedAt": iso()}})
        await store.append_audit("OPERATOR_UPDATED", request.state.user["email"], metadata={"email": u["email"], **upd})
        return {"operator": public_user(await get_user(uid))}

    @api.post("/console/operators/{uid}/reset-password")
    async def operators_reset(uid: str, request: Request):
        u = await get_user(uid)
        if uid == request.state.user["id"]:
            raise FrasError("INVALID_STATE", "Use Change password for your own account")
        pw = temp_password()
        await users.update_one({"id": uid}, {"$set": {"passwordHash": hash_pw(pw), "mustChangePassword": True}})
        await sessions.update_many({"userId": uid, "revokedAt": None}, {"$set": {"revokedAt": iso()}})
        await store.append_audit("OPERATOR_PASSWORD_RESET", request.state.user["email"], metadata={"email": u["email"]})
        return {"operator": public_user(await get_user(uid)), "temporaryPassword": pw}

    async def seed():
        await users.create_index("email", unique=True)
        await users.create_index("id", unique=True)
        await sessions.create_index("sid", unique=True)
        await sessions.create_index("expiresAt", expireAfterSeconds=0)
        await attempts.create_index("identifier", unique=True)
        email, pw = os.environ["ADMIN_EMAIL"].strip().lower(), os.environ["ADMIN_PASSWORD"]
        u = await users.find_one({"email": email})
        if not u:
            await users.insert_one({"id": str(uuid.uuid4()), "email": email, "name": "Admin", "role": "admin", "active": True, "passwordHash": hash_pw(pw),
                                    "mustChangePassword": False, "createdAt": iso(), "createdBy": "seed", "lastLoginAt": None})
        elif not u.get("passwordChangedAt") and not check_pw(pw, u["passwordHash"]):
            await users.update_one({"email": email}, {"$set": {"passwordHash": hash_pw(pw), "role": "admin", "active": True}})

    return seed
