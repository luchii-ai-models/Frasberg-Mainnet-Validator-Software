import logging
import os
import uuid
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import artifacts  # noqa: E402
import extras  # noqa: E402
import ops  # noqa: E402
import apikeys  # noqa: E402
import auth  # noqa: E402
import core  # noqa: E402
import selftests  # noqa: E402
from core import FrasError  # noqa: E402
from store import KNOWN_VERSIONS, Store, clean, iso  # noqa: E402

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
store = Store(db)

app = FastAPI(title="Frasberg Mainnet Validator")
api = APIRouter(prefix="/api")
NETWORK = "frasberg-mainnet"

SCHEDULE = [
    {"epoch": 100, "validator": {"minimum": "1.0.0", "recommended": "1.1.0", "maximum": "1.1.x", "channel": "stable"}, "luchii": {"minimum": "1.0.0", "recommended": "1.1.0", "maximum": "1.1.x"}},
    {"epoch": 200, "validator": {"minimum": "2.0.0", "recommended": "2.2.0", "maximum": "2.2.x", "channel": "stable"}, "luchii": {"minimum": "2.0.0", "recommended": "2.2.0", "maximum": "2.2.x"}},
    {"epoch": 300, "validator": {"minimum": "3.0.0", "recommended": "3.5.0", "maximum": "3.5.x", "channel": "stable"}, "luchii": {"minimum": "3.0.0", "recommended": "3.5.0", "maximum": "3.5.x"}},
    {"epoch": 400, "validator": {"minimum": "4.0.0", "recommended": "4.2.1", "maximum": "4.9.x", "channel": "stable"}, "luchii": {"minimum": "4.0.0", "recommended": "4.2.1", "maximum": "4.9.x"}},
    {"epoch": 900, "validator": {"minimum": "6.2.0", "recommended": "6.4.1", "maximum": "6.x", "channel": "stable"}, "luchii": {"minimum": "4.0.0", "recommended": "4.2.1"}},
    {"epoch": 1000, "validator": {"minimum": "6.4.0", "recommended": "6.4.1", "maximum": "6.x", "channel": "stable"}, "luchii": {"minimum": "4.2.0", "recommended": "4.2.1"}},
]

VALIDATOR_SEED = {
    "_id": "self", "validatorId": "fras_val_001", "network": NETWORK, "validatorVersion": "6.4.1", "coreVersion": "6.4.1",
    "runtimeVersion": "6.4.1", "luchiiVersion": "4.2.0", "channel": "stable", "epoch": 900, "commission": 5,
    "stake": 250000, "uptime": 99.98, "voteParticipation": 98.7, "missedBlocks": 0.8, "apiAvailability": 99.97,
    "securityScanPassed": True, "identityVerified": True, "governanceStanding": True,
    "stack": {
        "Frasberg Core": ["Consensus Engine", "Identity Engine", "Validator Runtime", "Reward Engine"],
        "Luchii Runtime": ["Inference Layer", "Routing Layer", "Governance Layer"],
        "Frasberg Secure Runtime": ["Authentication", "API Keys", "Permissions", "Monitoring"],
    },
}


@app.middleware("http")
async def request_id_mw(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(FrasError)
async def fras_error_handler(request: Request, exc: FrasError):
    status = core.ERROR_MAP.get(exc.code, (400, ""))[0]
    return JSONResponse(status_code=status, content=core.error_envelope(getattr(request.state, "request_id", "req_unknown"), exc.code, exc.message))


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception):
    logging.exception("unhandled")
    return JSONResponse(status_code=500, content=core.error_envelope(getattr(request.state, "request_id", "req_unknown"), "INTERNAL_ERROR"))


# ---------- helpers ----------
async def get_validator():
    return await db.validator.find_one({"_id": "self"}, {"_id": 0})


async def schedule_for(epoch):
    doc = await store.schedule.find_one({"epoch": {"$lte": epoch}}, {"_id": 0}, sort=[("epoch", -1)])
    return doc or await store.schedule.find_one({}, {"_id": 0}, sort=[("epoch", 1)])


async def required_versions(epoch):
    s = await schedule_for(epoch)
    out = {"network": NETWORK, "epoch": s["epoch"], "validator": s["validator"], "luchii": s["luchii"],
           "core": {"min": s["validator"]["minimum"], "max": s["validator"]["maximum"]},
           "enforcement": s["enforcement"], "publishedAt": s["publishedAt"]}
    if s.get("signature"):
        out["policyId"] = s["policyId"]
        out["signature"] = s["signature"]
    core.validate_response("required-versions", out)
    return out


async def compliance():
    v = await get_validator()
    req = await required_versions(v["epoch"])
    settings = await store.get_settings()
    return core.evaluate_compliance({**v, "frozen": settings["validatorsFrozen"]}, req), v, req


async def guard_writes():
    if (await store.get_settings())["safeMode"]:
        raise FrasError("SAFE_MODE")


def policy_for(s):
    return {"policyId": f"frasberg-mainnet-policy-epoch-{s['epoch']}", "version": "1.0.0", "network": NETWORK, "epoch": s["epoch"],
            "requirements": {"validatorMin": s["validator"]["minimum"], "validatorRecommended": s["validator"]["recommended"],
                             "validatorChannel": s["validator"]["channel"], "luchiiMin": s["luchii"]["minimum"]},
            "publishedAt": s["publishedAt"]}


@app.on_event("startup")
async def seed():
    await store.ensure_indexes()
    if not await db.validator.find_one({"_id": "self"}):
        await db.validator.insert_one(dict(VALIDATOR_SEED))
    published = {100: "2026-01-01", 200: "2026-02-01", 300: "2026-03-01", 400: "2026-04-01", 900: "2026-09-28", 1000: "2026-06-01"}
    for s in SCHEDULE:
        if not await store.schedule.find_one({"epoch": s["epoch"]}):
            await store.schedule.insert_one({**s, "enforcement": {"startsEpoch": s["epoch"], "graceEndsEpoch": s["epoch"] + 1},
                                             "publishedAt": f"{published[s['epoch']]}T00:00:00Z"})
    if not await store.keys.find_one({}):
        await store.create_key("frasberg-foundation")
        await store.create_key("frasberg-foundation")
    if await store.keys.find_one({"status": "active"}):
        for s in await store.schedule.find({"signature": {"$exists": False}}, {"_id": 0}).to_list(None):
            env = await store.sign_policy(policy_for(s))
            await store.schedule.update_one({"epoch": s["epoch"]}, {"$set": {"policyId": env["policy"]["policyId"],
                                            "signature": {"keyId": env["keyId"], "signature": env["signature"]}}})
        await store.publish_batch("publisher-seed")
    await extras_seed()
    await ops_seed()
    await apikeys_seed()
    await auth_seed()


# ---------- public validator API (spec endpoints) ----------
@api.get("/")
async def root():
    return {"service": "frasberg-mainnet-validator", "network": NETWORK}


@api.get("/v1/validator/version")
async def validator_version():
    v = await get_validator()
    out = {k: v[k] for k in ["validatorId", "network", "validatorVersion", "coreVersion", "luchiiVersion", "runtimeVersion", "channel", "epoch"]}
    core.validate_response("validator-version", out)
    return out


@api.get("/v1/validator/status")
async def validator_status():
    c, v, _ = await compliance()
    return {"validatorId": v["validatorId"], "status": "healthy" if c["healthy"] else "unhealthy", "uptime": v["uptime"],
            "voteParticipation": v["voteParticipation"], "missedBlocks": v["missedBlocks"], "apiAvailability": v["apiAvailability"],
            "epoch": v["epoch"], "commission": v["commission"]}


@api.get("/v1/validator/compliance")
async def validator_compliance():
    c, v, req = await compliance()
    return {"compliant": c["compliant"], "code": c["code"], "validatorVersion": v["validatorVersion"], "requiredVersion": req["validator"]["minimum"],
            "validator": c["validator"], "luchii": c["luchii"], "versionClass": c["versionClass"], "delegationEligible": c["delegationEligible"],
            "rewardsEnabled": c["rewardsEnabled"], "votingEnabled": c["votingEnabled"]}


@api.get("/v1/network/required-versions")
async def network_required_versions(epoch: Optional[int] = None):
    if epoch is not None and epoch < 0:
        raise FrasError("INVALID_POLICY", "epoch must be >= 0")
    return await required_versions(epoch if epoch is not None else (await get_validator())["epoch"])


@api.get("/v1/network/schedule")
async def network_schedule():
    return [await required_versions(s["epoch"]) for s in await store.schedule.find({}, {"epoch": 1}).sort("epoch", 1).to_list(None)]


@api.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics():
    m = await metric_snapshot()
    lines = []
    for mid, doc in (await store.metric_values()).items():
        labels = ",".join(f'{k}="{v}"' for k, v in sorted(doc.get("labels", {}).items()))
        lines.append(f"{doc['name']}{{{labels}}} {doc['value']}" if labels else f"{doc['name']} {doc['value']}")
    for k in ("audit_outbox_pending_total", "rotation_lock_active_total"):
        lines.append(f"{k} {m[k]}")
    lines.append(f"audit_outbox_dead_letter_current {m['deadLetterCurrent']}")
    return "\n".join(sorted(lines)) + "\n"


# ---------- console ----------
class ValidatorUpdate(BaseModel):
    validatorVersion: Optional[str] = None
    luchiiVersion: Optional[str] = None
    channel: Optional[str] = None
    epoch: Optional[int] = Field(None, ge=0)
    commission: Optional[float] = None
    stake: Optional[float] = None
    uptime: Optional[float] = None
    voteParticipation: Optional[float] = None
    missedBlocks: Optional[float] = None
    apiAvailability: Optional[float] = None
    securityScanPassed: Optional[bool] = None
    identityVerified: Optional[bool] = None
    governanceStanding: Optional[bool] = None


@api.get("/console/overview")
async def overview():
    c, v, req = await compliance()
    return {"validator": v, "compliance": c, "required": req, "settings": await store.get_settings(),
            "healthRequirements": core.HEALTH_REQUIREMENTS, "delegationCriteria": core.DELEGATION_CRITERIA}


@api.put("/console/validator")
async def update_validator(body: ValidatorUpdate):
    await guard_writes()
    upd = body.model_dump(exclude_none=True)
    if "validatorVersion" in upd:
        if not core.semver_valid(upd["validatorVersion"]):
            raise FrasError("INVALID_VERSION")
        upd["coreVersion"] = upd["runtimeVersion"] = upd["validatorVersion"]
        upd.setdefault("channel", core.channel_of(upd["validatorVersion"]))
    if "channel" in upd and upd["channel"] not in core.CHANNELS:
        raise FrasError("INVALID_VERSION", "Unknown release channel")
    if "luchiiVersion" in upd and not core.semver_valid(upd["luchiiVersion"]):
        raise FrasError("INVALID_VERSION")
    await db.validator.update_one({"_id": "self"}, {"$set": upd})
    await store.append_audit("VALIDATOR_UPDATED", "validator-admin", metadata=upd)
    return await overview()


class SettingsBody(BaseModel):
    busMode: Optional[str] = None
    governanceFrozen: Optional[bool] = None
    validatorsFrozen: Optional[bool] = None
    safeMode: Optional[bool] = None
    clearBusMode: bool = False


@api.put("/console/settings")
async def update_settings(body: SettingsBody):
    upd = body.model_dump(exclude_none=True, exclude={"clearBusMode"})
    if body.clearBusMode:
        upd["busMode"] = None
    if upd.get("busMode") not in (None, "fail", "timeout", "crash_before_ack"):
        raise FrasError("INVALID_STATE", "busMode must be fail|timeout|crash_before_ack")
    return await store.set_settings(**upd)


@api.get("/console/schedule")
async def console_schedule():
    v = await get_validator()
    rows = []
    for s in await network_schedule():
        req = s
        rows.append({**s, "currentClass": core.version_class(v["validatorVersion"], req)})
    return {"rows": rows, "currentEpoch": v["epoch"], "channels": core.CHANNELS}


# keys
class RotateBody(BaseModel):
    oldKeyId: str
    newKeyId: str
    owner: str = "node-a"
    injectFailure: bool = False


@api.get("/console/keys")
async def keys_list():
    return await store.list_keys()


@api.post("/console/keys")
async def keys_create():
    await guard_writes()
    return await store.create_key()


@api.post("/console/keys/rotate")
async def keys_rotate(body: RotateBody):
    await guard_writes()
    return await store.rotate_key(body.oldKeyId, body.newKeyId, owner=body.owner, inject_failure=body.injectFailure)


@api.post("/console/keys/rollback")
async def keys_rollback(body: RotateBody):
    await guard_writes()
    return await store.rollback_rotation(body.oldKeyId, body.newKeyId, owner=body.owner)


@api.post("/console/keys/{key_id}/revoke")
async def keys_revoke(key_id: str):
    await guard_writes()
    key = await store.get_key(key_id)
    if key and key["status"] == "active":
        raise FrasError("INVALID_STATE", "Rotate to a new key before revoking the active signing key")
    return await store.revoke_key(key_id)


# policies
class PolicyBody(BaseModel):
    policy: dict


class VerifyBody(BaseModel):
    policy: dict
    keyId: Optional[str] = None
    signature: Optional[str] = None


@api.get("/console/policies")
async def policies_list():
    return await store.list_policies()


@api.post("/console/policies/canonicalize")
async def policies_canonicalize(body: PolicyBody):
    return {"canonical": core.canonicalize(body.policy), "schemaErrors": core.schema_errors("validator-policy", body.policy)}


@api.post("/console/policies/sign")
async def policies_sign(body: PolicyBody):
    await guard_writes()
    return await store.sign_policy(body.policy)


@api.post("/console/policies/verify")
async def policies_verify(body: VerifyBody):
    return await store.verify_signed(body.model_dump())


# audit
class TamperBody(BaseModel):
    mutation: str = Field(pattern="^(payload|link|hash|gap)$")
    index: int = Field(1, ge=0)


@api.get("/console/audit")
async def audit_list(limit: int = 200, event_type: Optional[str] = None):
    q = {"event_type": event_type} if event_type else {}
    total = await store.audit.count_documents({})
    rows = await store.audit.find(q, {"_id": 0}).sort("sequence", -1).limit(limit).to_list(None)
    return {"total": total, "records": rows}


@api.get("/console/audit/verify")
async def audit_verify():
    return await store.verify_chain()


@api.post("/console/audit/simulate-tamper")
async def audit_tamper(body: TamperBody):
    recs = await store.audit_records()
    if not recs:
        raise FrasError("INVALID_STATE", "Audit log empty")
    i = min(body.index, len(recs) - 1)
    if body.mutation == "payload":
        recs[i]["payload"] = {"tampered": True}
    elif body.mutation == "link":
        recs[i]["previous_hash"] = "fake"
    elif body.mutation == "hash":
        recs[i]["current_hash"] = "tampered"
    else:
        recs.pop(i)
    return {"mutation": body.mutation, "index": i, "result": core.verify_audit_chain(recs),
            "note": "Simulated on an in-memory copy; the stored audit log is append-only and was not modified."}


# outbox
class PublishBody(BaseModel):
    mode: Optional[str] = None
    owner: str = "publisher-a"


class ReplayBody(BaseModel):
    eventId: Optional[str] = None
    all: bool = False
    injectFailure: bool = False


def _ob(d):
    d = clean(d)
    d.pop("payload", None)
    return d


@api.get("/console/outbox")
async def outbox_state():
    pending = await store.outbox.find({"published_at": None}).sort("created_at", 1).limit(100).to_list(None)
    recent = await store.outbox.find({"published_at": {"$ne": None}}).sort("published_at", -1).limit(15).to_list(None)
    dead = await store.dead.find({}).sort("moved_at", -1).limit(100).to_list(None)
    return {"pending": [_ob(d) for d in pending], "recentPublished": [_ob(d) for d in recent], "deadLetter": [_ob(d) for d in dead],
            "pendingCount": await store.outbox.count_documents({"published_at": None}),
            "publishedCount": await store.outbox.count_documents({"published_at": {"$ne": None}}),
            "deadCount": await store.dead.count_documents({}), "consumerProcessed": await store.consumer.count_documents({}),
            "busDeliveries": await store.bus.count_documents({}), "settings": await store.get_settings(),
            "retrySchedule": core.RETRY_SECONDS, "maxRetries": core.MAX_RETRIES}


@api.post("/console/outbox/publish")
async def outbox_publish(body: PublishBody):
    mode = body.mode or (await store.get_settings())["busMode"]
    return await store.publish_batch(body.owner, mode)


@api.post("/console/outbox/recover-claims")
async def outbox_recover():
    return {"recovered": await store.recover_expired_claims()}


@api.post("/console/outbox/expire-claims")
async def outbox_expire_claims():
    ids = [d["event_id"] async for d in store.outbox.find({"published_at": None, "claim_owner": {"$ne": None}}, {"event_id": 1})]
    for i in ids:
        await store.expire_claim(i)
    await store.outbox.update_many({"published_at": None}, {"$set": {"next_retry_at": None}})
    return {"expired": len(ids)}


@api.post("/console/outbox/replay")
async def outbox_replay(body: ReplayBody):
    if body.all:
        return await store.replay_all()
    if not body.eventId:
        raise FrasError("DEAD_LETTER_NOT_FOUND")
    return {"replayed": await store.replay_dead_letter(body.eventId, inject_failure=body.injectFailure)}


# locks
class LockBody(BaseModel):
    name: str = Field(min_length=1)
    owner: str = "node-a"
    ttlMs: int = Field(30000, ge=100, le=600000)
    token: Optional[int] = None
    fail: bool = False


@api.get("/console/locks")
async def locks_list():
    return {"locks": await store.list_locks(), "fencingCounter": (await store.counters.find_one({"_id": "fencing:rotation"}) or {}).get("value", 0)}


@api.post("/console/locks/acquire")
async def locks_acquire(body: LockBody):
    return {"token": await store.acquire_lock(body.name, body.owner, body.ttlMs)}


@api.post("/console/locks/renew")
async def locks_renew(body: LockBody):
    return {"renewed": await store.renew_lock(body.name, body.owner, body.token, body.fail, body.ttlMs)}


@api.post("/console/locks/release")
async def locks_release(body: LockBody):
    if body.token is None:
        raise FrasError("LOCK_NOT_OWNER", "token required")
    return {"released": await store.release_lock(body.name, body.token)}


@api.post("/console/locks/expire")
async def locks_expire(body: LockBody):
    await store.expire_lock(body.name)
    return {"expired": body.name}


# metrics
async def metric_snapshot():
    c = store.counter
    acq, failed = await c("rotation_lock_acquired_total"), await c("rotation_lock_acquire_failed_total")
    w5, w1h = await store.lock_window(300), await store.lock_window(3600)
    attempts, success = await c("audit_outbox_publish_attempts_total"), await c("audit_outbox_publish_success_total")
    base = (await store.settings.find_one({"_id": "outbox_baseline"})) or {"attempts": 0, "success": 0}
    attempts, success = attempts - base["attempts"], success - base["success"]
    avail_den = w1h["acquired"] + w1h["error"]
    availability = w1h["acquired"] / avail_den if avail_den else 1.0
    burn = (1 - availability) / 0.0005
    cont_den = w5["acquired"] + w5["denied"]
    contention = w5["denied"] / cont_den if cont_den else 0.0
    cp, cf = await c("chaos_test_pass_total"), await c("chaos_test_fail_total")
    locks = await store.list_locks()
    pending = await store.outbox.count_documents({"published_at": None})
    out = {
        "lockAcquired": acq, "lockDenied": await c("rotation_lock_denied_total"), "lockFailed": failed,
        "window5m": w5, "window1h": w1h,
        "contentionRate": contention, "lockAvailability": availability,
        "burnRate": burn, "burnLevel": "critical" if burn >= 14.4 else "high" if burn >= 6 else "warning" if burn >= 3 else "healthy",
        "errorBudgetRemaining": max(0.0, 1 - (0.9995 - availability) / 0.0005) if availability < 0.9995 else 1.0,
        "staleFence": await c("rotation_lock_stale_fence_total"), "staleRelease": await c("rotation_lock_stale_release_total"),
        "renewFailures": await c("rotation_lock_renew_failure_total"), "lockExpired": await c("rotation_lock_expired_total"),
        "tokenMax": await c("rotation_lock_token_max"), "durationMsMax": await c("rotation_lock_duration_ms_max"),
        "publishAttempts": attempts, "publishSuccess": success, "publishFailures": await c("audit_outbox_publish_failures_total"),
        "outboxSuccessRate": success / attempts if attempts else 1.0, "retries": await c("audit_outbox_retry_total"),
        "deadLetterTotal": await c("audit_outbox_dead_letter_total"), "deadLetterCurrent": await store.dead.count_documents({}),
        "replaySuccess": await c("audit_outbox_replay_success_total"), "replayFailures": await c("audit_outbox_replay_failure_total"),
        "idempotencyHits": await c("audit_outbox_idempotency_hit_total"), "duplicateDropped": await c("audit_outbox_duplicate_dropped_total"),
        "claimRecovered": await c("audit_outbox_claim_recovered_total"),
        "chaosPass": cp, "chaosFail": cf, "chaosPassRate": cp / (cp + cf) if cp + cf else None,
        "audit_outbox_pending_total": pending, "rotation_lock_active_total": sum(1 for l in locks if l["active"]),
    }
    out["alerts"] = [{"name": n, "firing": core.alert_fires(n, val), "severity": core.ALERT_THRESHOLDS[n][2], "service": core.ALERT_THRESHOLDS[n][3], "value": val}
                     for n, val in [("LockAvailabilityLow", availability if avail_den else None), ("OutboxBacklog", pending),
                                    ("ErrorBudgetFastBurn", burn if avail_den else None), ("StaleFenceDetected", out["staleFence"]),
                                    ("AuditDeadLetterGrowth", out["deadLetterCurrent"]), ("ReplayFailures", out["replayFailures"]),
                                    ("LockContentionRateHigh", contention if cont_den else None), ("ChaosFailure", cf)]]
    return out


@api.post("/console/metrics/reset-window")
async def metrics_reset_window():
    removed = (await store.lock_samples.delete_many({})).deleted_count
    await store.settings.update_one({"_id": "outbox_baseline"}, {"$set": {"attempts": await store.counter("audit_outbox_publish_attempts_total"),
                                    "success": await store.counter("audit_outbox_publish_success_total")}}, upsert=True)
    await store.append_audit("SLO_WINDOW_RESET", "validator-ops", metadata={"samplesCleared": removed})
    return {"samplesCleared": removed}


@api.get("/console/metrics")
async def metrics_summary():
    return await metric_snapshot()


@api.get("/console/artifacts")
async def get_artifacts():
    return {"grafana": artifacts.GRAFANA_DASHBOARD, "prometheusRules": artifacts.PROMETHEUS_RULES, "alertmanager": artifacts.ALERTMANAGER,
            "runbooks": artifacts.RUNBOOKS, "schemas": core.SCHEMAS, "errorMap": {k: {"http": v[0], "message": v[1]} for k, v in core.ERROR_MAP.items()},
            "inhibitionRules": core.INHIBITION_RULES, "retention": core.RETENTION, "allowedLabels": core.ALLOWED_LABELS}


# rollbacks
class RollbackBody(BaseModel):
    level: Optional[str] = None
    reason: Optional[str] = None
    ticketId: Optional[str] = None
    incidentId: Optional[str] = None
    targetVersion: Optional[str] = None
    requester: str = "platform-lead"


class ActorBody(BaseModel):
    actor: str = Field(min_length=1)


@api.get("/console/rollbacks")
async def rollbacks_list():
    rows = await store.rollbacks.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    return {"rollbacks": rows, "levels": core.ROLLBACK_LEVEL_INFO, "requiredApprovals": core.REQUIRED_APPROVALS, "knownVersions": KNOWN_VERSIONS}


@api.post("/console/rollbacks")
async def rollbacks_create(body: RollbackBody):
    return await store.create_rollback(body.model_dump(exclude={"requester"}), body.requester)


@api.post("/console/rollbacks/{rb_id}/approve")
async def rollbacks_approve(rb_id: str, body: ActorBody):
    return await store.approve_rollback(rb_id, body.actor)


@api.post("/console/rollbacks/{rb_id}/reject")
async def rollbacks_reject(rb_id: str, body: ActorBody):
    return await store.reject_rollback(rb_id, body.actor)


@api.post("/console/rollbacks/{rb_id}/execute")
async def rollbacks_execute(rb_id: str):
    return await store.execute_rollback(rb_id)


@api.post("/console/rollbacks/{rb_id}/verify")
async def rollbacks_verify(rb_id: str):
    return await store.verify_rollback(rb_id)


# tests
class TestRunBody(BaseModel):
    suites: Optional[List[str]] = None
    seed: Optional[int] = None
    runs: int = Field(1000, ge=1, le=5000)


@api.get("/console/tests/suites")
async def tests_suites():
    return {"suites": selftests.list_suites(), "tests": [{"suite": t["suite"], "name": t["name"]} for t in selftests.TESTS]}


@api.post("/console/tests/run")
async def tests_run(body: TestRunBody):
    result = await selftests.run_tests(db, body.suites, body.seed, body.runs)
    result["runId"], result["ranAt"] = str(uuid.uuid4()), iso()
    chaos = [r for r in result["results"] if r["suite"] == "chaos"]
    if chaos:
        await store.inc("chaos_test_pass_total", sum(r["status"] == "passed" for r in chaos))
        await store.inc("chaos_test_fail_total", sum(r["status"] == "failed" for r in chaos))
        await store.append_audit("CHAOS_TEST", "chaos-harness", metadata={"passed": sum(r["status"] == "passed" for r in chaos), "total": len(chaos)})
    await store.append_audit("TEST_RUN", "test-runner", metadata={"runId": result["runId"], "passed": result["passed"], "total": result["total"], "seed": result["seed"]})
    await store.test_runs.insert_one(dict(result))
    return result


@api.get("/console/tests/latest")
async def tests_latest():
    return await store.test_runs.find_one({}, {"_id": 0}, sort=[("ranAt", -1)]) or {}


# readiness
@api.get("/console/readiness")
async def readiness():
    c, v, req = await compliance()
    chain = await store.verify_chain()
    m = await metric_snapshot()
    settings = await store.get_settings()
    s = await store.schedule.find_one({"epoch": req["epoch"]})
    sig_ok = False
    if s and s.get("signature"):
        sig_ok = (await store.verify_signed({"policy": policy_for(s), **s["signature"]}))["valid"]
    keys = await store.list_keys()
    last = await store.test_runs.find_one({}, sort=[("ranAt", -1)]) or {}
    suites = {r["suite"] for r in last.get("results", [])}
    full_run = {"semver", "contracts", "property", "signatures", "keys", "audit", "locks", "outbox", "rollback", "observability", "chaos"} <= suites
    checks = [
        ("Required version endpoint", True, f"epoch {req['epoch']} min {req['validator']['minimum']}"),
        ("Validator version compliant", c["compliant"], c["code"]),
        ("Delegation eligible", c["delegationEligible"], ", ".join(k for k, ok in c["criteria"].items() if not ok) or "all criteria met"),
        ("Launch policy signed & verified", sig_ok, req.get("policyId", "unsigned")),
        ("Active signing key present", any(k["status"] == "active" for k in keys), f"{len(keys)} keys"),
        ("Audit chain verified", chain["valid"], chain["code"]),
        ("Outbox healthy (< 100 pending)", m["audit_outbox_pending_total"] < 100, f"{m['audit_outbox_pending_total']} pending"),
        ("Dead-letter queue empty", m["deadLetterCurrent"] == 0, f"{m['deadLetterCurrent']} rows"),
        ("Lock contention < 1%", m["contentionRate"] < 0.01, f"{m['contentionRate'] * 100:.2f}%"),
        ("Stale fence events = 0", m["staleFence"] == 0, f"{m['staleFence']} events"),
        ("Burn rate healthy", m["burnLevel"] in ("healthy",), f"{m['burnRate']:.2f}x"),
        ("Grafana dashboard valid", all(p["title"] and p["targets"] for p in artifacts.GRAFANA_DASHBOARD["panels"]), f"{len(artifacts.GRAFANA_DASHBOARD['panels'])} panels"),
        ("Alert rules loaded", "alert:" in artifacts.PROMETHEUS_RULES, "prometheus + alertmanager"),
        ("Full test suite executed", full_run, f"last run {last.get('ranAt', 'never')}"),
        ("All tests passing", bool(last) and last.get("failed", 1) == 0, f"{last.get('passed', 0)}/{last.get('total', 0)}"),
        ("Chaos suite passes", "chaos" in suites and all(r["status"] == "passed" for r in last.get("results", []) if r["suite"] == "chaos"), "chaos harness"),
        ("Not in safe mode / no freezes", not (settings["safeMode"] or settings["governanceFrozen"] or settings["validatorsFrozen"]), str(settings)),
    ]
    rows = [{"name": n, "passed": bool(p), "details": d} for n, p, d in checks]
    decision = "GO" if all(r["passed"] for r in rows) else "NO-GO"
    return {"decision": decision, "checks": rows, "passed": sum(r["passed"] for r in rows), "total": len(rows), "evaluatedAt": iso()}


extras_seed, alerting = extras.register(api, store, db, required_versions, metric_snapshot, get_validator)
ops_seed = ops.register(api, store, db, required_versions, get_validator, alerting)
apikeys_seed = apikeys.register(api, app, store, db)
auth_seed = auth.register(app, api, store, db)
app.include_router(api)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID"])

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
