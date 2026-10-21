"""MongoDB-backed Frasberg stores: append-only audit chain, outbox, fenced locks, signing keys, rollbacks."""
import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

import core
from core import FrasError

LOCK_TTL_MS = 30000
CLAIM_LEASE_S = 30
KNOWN_VERSIONS = ["1.0.0", "1.1.0", "2.0.0", "2.2.0", "3.0.0", "3.5.0", "4.0.0", "4.2.1", "4.9.0",
                  "6.0.0", "6.1.0", "6.1.3", "6.2.0", "6.3.0", "6.4.0", "6.4.1"]


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat().replace("+00:00", "Z")


def clean(doc):
    if doc is None:
        return None
    doc = {k: v for k, v in doc.items() if k != "_id"}
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = iso(v if v.tzinfo else v.replace(tzinfo=timezone.utc))
    return doc


def aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class PublisherCrash(Exception):
    pass


class Store:
    def __init__(self, db, prefix=""):
        self.db = db
        self.prefix = prefix
        c = lambda n: db[f"{prefix}{n}"]
        self.audit, self.outbox, self.dead = c("audit_log"), c("audit_outbox"), c("audit_outbox_dead_letter")
        self.keys, self.locks, self.counters = c("signing_keys"), c("rotation_lock"), c("counters")
        self.metrics, self.bus, self.consumer = c("metrics"), c("event_bus"), c("consumer_processed")
        self.policies, self.rollbacks, self.validator = c("signed_policies"), c("rollback_requests"), c("validator")
        self.schedule, self.settings, self.test_runs = c("required_versions"), c("settings"), c("test_runs")
        self.lock_samples = c("lock_samples")
        self.all = [self.audit, self.outbox, self.dead, self.keys, self.locks, self.counters, self.metrics, self.bus,
                    self.consumer, self.policies, self.rollbacks, self.validator, self.schedule, self.settings, self.lock_samples]

    async def lock_window(self, seconds):
        since = now() - timedelta(seconds=seconds)
        out = {"acquired": 0, "denied": 0, "error": 0}
        async for g in self.lock_samples.aggregate([{"$match": {"ts": {"$gte": since}}}, {"$group": {"_id": "$result", "n": {"$sum": 1}}}]):
            out[g["_id"]] = g["n"]
        return out

    async def ensure_indexes(self):
        await self.audit.create_index([("sequence", ASCENDING)], unique=True)
        await self.audit.create_index("event_id", unique=True)
        await self.audit.create_index("event_type")
        await self.outbox.create_index("event_id", unique=True)
        await self.outbox.create_index([("published_at", ASCENDING), ("claim_until", ASCENDING)])
        await self.dead.create_index("event_id", unique=True)
        await self.consumer.create_index("event_id", unique=True)
        await self.keys.create_index("key_id", unique=True)
        await self.rollbacks.create_index("rollback_id", unique=True)
        await self.schedule.create_index("epoch", unique=True)
        await self.lock_samples.create_index("ts", expireAfterSeconds=7200)

    async def reset(self):
        """Self-test namespace only: clears isolated selftest_ collections."""
        assert self.prefix.startswith("selftest_")
        for col in self.all:
            await col.delete_many({})

    # ---------- metrics (low-cardinality) ----------
    async def inc(self, name, by=1, labels=None):
        labels = labels or {}
        core.validate_labels(labels)
        mid = name + (json.dumps(labels, sort_keys=True) if labels else "")
        await self.metrics.update_one({"_id": mid}, {"$inc": {"value": by}, "$set": {"name": name, "labels": labels}}, upsert=True)

    async def gauge_max(self, name, value):
        await self.metrics.update_one({"_id": name}, {"$max": {"value": value}, "$set": {"name": name, "labels": {}}}, upsert=True)

    async def metric_values(self):
        out = {}
        async for m in self.metrics.find({}):
            out[m["_id"]] = m
        return out

    async def counter(self, name):
        m = await self.metrics.find_one({"_id": name})
        return m["value"] if m else 0

    async def next_seq(self, name):
        doc = await self.counters.find_one_and_update({"_id": name}, {"$inc": {"value": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
        return doc["value"]

    # ---------- immutable audit log + outbox ----------
    async def append_audit(self, event_type, actor, key_id=None, metadata=None, fail_before_commit=False):
        event = {"id": str(uuid.uuid4()), "type": event_type, "actor": actor, "timestamp": iso()}
        if key_id:
            event["keyId"] = key_id
        if metadata:
            event["metadata"] = metadata
        if fail_before_commit:
            raise FrasError("INTERNAL_ERROR", "Injected failure before commit; nothing written")
        for _ in range(50):
            tail = await self.audit.find_one({}, sort=[("sequence", DESCENDING)])
            seq = (tail["sequence"] + 1) if tail else 1
            prev = tail["current_hash"] if tail else core.GENESIS
            record = {"sequence": seq, "event_id": event["id"], "event_type": event_type, "actor": actor,
                      "timestamp": event["timestamp"], "previous_hash": prev,
                      "current_hash": core.hash_audit_record(prev, event), "payload": event,
                      "retention": core.retention(event_type)}
            try:
                await self.audit.insert_one(record)
                break
            except DuplicateKeyError:
                await asyncio.sleep(0.002)
        else:
            raise FrasError("INTERNAL_ERROR", "Audit append contention")
        await self.outbox.insert_one({"event_id": event["id"], "event_type": event_type, "payload": event, "created_at": now(),
                                      "published_at": None, "delivered_at": None, "retry_count": 0, "next_retry_at": None,
                                      "last_error": None, "claim_owner": None, "claim_until": None})
        await self.inc("audit_records_total", labels={"event_type": event_type})
        return clean(record)

    async def audit_records(self, limit=0):
        cur = self.audit.find({}, {"_id": 0}).sort("sequence", ASCENDING)
        if limit:
            cur = cur.limit(limit)
        return await cur.to_list(None)

    async def verify_chain(self):
        return core.verify_audit_chain(await self.audit_records())

    # ---------- distributed lock with fencing ----------
    async def acquire_lock(self, name, owner, ttl_ms=LOCK_TTL_MS):
        token = await self.next_seq("fencing:rotation")
        await self.inc("rotation_lock_acquire_total")
        t = now()
        prior = await self.locks.find_one({"_id": name})
        try:
            await self.locks.find_one_and_update(
                {"_id": name, "expires_at": {"$lt": t}},
                {"$set": {"token": token, "owner": owner, "acquired_at": t, "expires_at": t + timedelta(milliseconds=ttl_ms)}},
                upsert=True)
        except DuplicateKeyError:
            await self.inc("rotation_lock_denied_total")
            await self.lock_samples.insert_one({"ts": t, "result": "denied"})
            raise FrasError("ROTATION_IN_PROGRESS")
        except Exception:
            await self.inc("rotation_lock_acquire_failed_total")
            await self.lock_samples.insert_one({"ts": t, "result": "error"})
            raise
        await self.lock_samples.insert_one({"ts": t, "result": "acquired"})
        if prior and aware(prior["expires_at"]) < t:
            await self.inc("rotation_lock_expired_total")
        await self.inc("rotation_lock_acquired_total")
        await self.gauge_max("rotation_lock_token_max", token)
        return token

    async def renew_lock(self, name, owner, token=None, fail=False, ttl_ms=LOCK_TTL_MS):
        await self.inc("rotation_lock_renew_attempt_total")
        if fail:
            await self.inc("rotation_lock_renew_failure_total")
            raise FrasError("LOCK_RENEWAL_FAILED")
        doc = await self.locks.find_one({"_id": name})
        if not doc or aware(doc["expires_at"]) < now():
            await self.inc("rotation_lock_renew_failure_total")
            await self.inc("rotation_lock_expired_total")
            raise FrasError("LOCK_EXPIRED")
        if doc["owner"] != owner or (token is not None and doc["token"] != token):
            await self.inc("rotation_lock_renew_failure_total")
            await self.inc("rotation_lock_owner_mismatch_total")
            raise FrasError("LOCK_NOT_OWNER")
        res = await self.locks.update_one({"_id": name, "owner": owner, "token": doc["token"], "expires_at": {"$gte": now()}},
                                          {"$set": {"expires_at": now() + timedelta(milliseconds=ttl_ms)}})
        if res.matched_count != 1:
            raise FrasError("LOCK_EXPIRED")
        await self.inc("rotation_lock_renew_success_total")
        await self.inc("rotation_lock_renewed_total")
        return True

    async def release_lock(self, name, token):
        doc = await self.locks.find_one_and_delete({"_id": name, "token": token})
        if not doc:
            await self.inc("rotation_lock_stale_release_total")
            raise FrasError("LOCK_NOT_OWNER", "Stale owner cannot release lock")
        ms = int((now() - aware(doc["acquired_at"])).total_seconds() * 1000)
        await self.inc("rotation_lock_duration_ms_sum", ms)
        await self.inc("rotation_lock_duration_ms_count")
        await self.gauge_max("rotation_lock_duration_ms_max", ms)
        return True

    async def expire_lock(self, name):
        await self.locks.update_one({"_id": name}, {"$set": {"expires_at": now() - timedelta(seconds=1)}})

    async def transfer_lock(self, name, owner):
        await self.locks.update_one({"_id": name}, {"$set": {"owner": owner}})

    async def list_locks(self):
        out = []
        async for d in self.locks.find({}):
            d["name"] = d["_id"]
            d["active"] = aware(d["expires_at"]) >= now()
            out.append(clean(d))
        return out

    # ---------- signing keys ----------
    async def fenced_update(self, key_id, fields, token):
        res = await self.keys.update_one({"key_id": key_id, "fencing_token": {"$lt": token}},
                                         {"$set": {**fields, "fencing_token": token}})
        if res.matched_count != 1:
            await self.inc("rotation_lock_stale_fence_total")
            await self.inc("rotation_lock_stale_token_total")
            raise FrasError("STALE_FENCING_TOKEN")

    async def get_key(self, key_id):
        return await self.keys.find_one({"key_id": key_id})

    async def create_key(self, actor="validator-admin", key_id=None):
        has_active = await self.keys.find_one({"status": "active"})
        n = await self.next_seq("key_id")
        key_id = key_id or f"validator-key-{n:03d}"
        priv, pub = core.generate_keypair()
        t = now()
        doc = {"key_id": key_id, "public_key": pub, "private_key": priv, "created_at": iso(t), "activates_at": iso(t),
               "expires_at": iso(t + timedelta(days=365)), "status": "rotating" if has_active else "active",
               "fencing_token": 0, "revoked_at": None}
        await self.keys.insert_one(doc)
        await self.append_audit("KEY_CREATED", actor, key_id, {"status": doc["status"]})
        return self.public_key(doc)

    @staticmethod
    def public_key(d):
        return {k: d.get(k) for k in ["key_id", "public_key", "created_at", "activates_at", "expires_at", "status", "fencing_token", "revoked_at"]}

    async def list_keys(self):
        return [self.public_key(d) async for d in self.keys.find({}).sort("created_at", ASCENDING)]

    async def rotate_key(self, old_id, new_id, actor="rotation-system", owner="node-a", inject_failure=False, hold_ms=0):
        lock = f"rotation:{old_id}"
        token = await self.acquire_lock(lock, owner)
        try:
            old, new = await self.get_key(old_id), await self.get_key(new_id)
            if not old or not new:
                raise FrasError("KEY_UNKNOWN")
            if old["status"] != "active" or new["status"] == "revoked" or old_id == new_id:
                raise FrasError("INVALID_STATE", "Rotation requires an active old key and a non-revoked new key")
            await self.append_audit("KEY_ROTATION_STARTED", actor, old_id, {"newKeyId": new_id, "fencingToken": token})
            if hold_ms:
                await asyncio.sleep(hold_ms / 1000)
            if inject_failure:
                raise FrasError("ROTATION_FAILED")
            await self.fenced_update(old_id, {"status": "retired"}, token)
            await self.fenced_update(new_id, {"status": "active"}, token)
            await self.append_audit("KEY_ROTATION_COMPLETED", actor, new_id, {"retired": old_id, "active": new_id, "fencingToken": token})
            return {"retired": old_id, "active": new_id, "fencingToken": token}
        finally:
            await self.release_lock(lock, token)

    async def rollback_rotation(self, old_id, new_id, actor="rotation-system", owner="node-a"):
        old, new = await self.get_key(old_id), await self.get_key(new_id)
        if not old or not new:
            raise FrasError("KEY_UNKNOWN")
        if old["status"] == "revoked":
            raise FrasError("ROLLBACK_NOT_ALLOWED")
        lock = f"rotation:{new_id}"
        token = await self.acquire_lock(lock, owner)
        try:
            await self.fenced_update(old_id, {"status": "active"}, token)
            await self.fenced_update(new_id, {"status": "retired"}, token)
            await self.append_audit("KEY_ROTATION_ROLLED_BACK", actor, old_id, {"revertedKey": new_id, "fencingToken": token})
        finally:
            await self.release_lock(lock, token)
        return {"active": old_id, "retired": new_id}

    async def revoke_key(self, key_id, actor="validator-admin"):
        key = await self.get_key(key_id)
        if not key:
            raise FrasError("KEY_UNKNOWN")
        if key["status"] == "revoked":
            raise FrasError("INVALID_STATE", "Key already revoked")
        await self.keys.update_one({"key_id": key_id}, {"$set": {"status": "revoked", "revoked_at": iso()}})
        await self.append_audit("KEY_REVOKED", actor, key_id)
        return self.public_key(await self.get_key(key_id))

    # ---------- signed policies ----------
    async def sign_policy(self, policy, actor="frasberg-foundation"):
        core.validate_response("validator-policy", policy)
        key = await self.keys.find_one({"status": "active"})
        if not key:
            raise FrasError("KEY_NOT_ACTIVE", "No active signing key")
        envelope = {"policy": policy, "keyId": key["key_id"], "signature": core.sign_policy(policy, key["private_key"]),
                    "canonical": core.canonicalize(policy), "signedAt": iso()}
        await self.policies.insert_one(dict(envelope))
        await self.append_audit("POLICY_SIGNED", actor, key["key_id"], {"policyId": policy["policyId"], "epoch": policy["epoch"]})
        return envelope

    async def verify_signed(self, envelope):
        policy, key_id, sig = envelope.get("policy"), envelope.get("keyId"), envelope.get("signature")
        if not isinstance(policy, dict) or not core.schema_valid("validator-policy", policy):
            return {"valid": False, "code": "INVALID_POLICY", "errors": core.schema_errors("validator-policy", policy if isinstance(policy, dict) else {})}
        key = await self.get_key(key_id) if key_id else None
        if not key:
            return {"valid": False, "code": "KEY_UNKNOWN"}
        if not core.can_verify(key["status"]):
            return {"valid": False, "code": "KEY_REVOKED"}
        if not core.verify_policy(policy, sig or "", key["public_key"]):
            await self.inc("policy_signature_failure_total")
            return {"valid": False, "code": "INVALID_SIGNATURE"}
        return {"valid": True, "code": "COMPLIANT", "keyStatus": key["status"]}

    async def list_policies(self, limit=50):
        return await self.policies.find({}, {"_id": 0}).sort("signedAt", DESCENDING).limit(limit).to_list(None)

    # ---------- outbox publisher ----------
    async def claim_one(self, owner, lease_s=CLAIM_LEASE_S, event_id=None):
        t = now()
        f = {"published_at": None, "$and": [{"$or": [{"claim_until": None}, {"claim_until": {"$lt": t}}]},
                                           {"$or": [{"next_retry_at": None}, {"next_retry_at": {"$lte": t}}]}]}
        if event_id:
            f["event_id"] = event_id
        doc = await self.outbox.find_one_and_update(
            f, {"$set": {"claim_owner": owner, "claim_until": t + timedelta(seconds=lease_s), "publisher_id": owner}},
            sort=[("created_at", ASCENDING)], return_document=ReturnDocument.AFTER)
        if doc:
            await self.inc("audit_outbox_claim_total")
        return doc

    async def claim_batch(self, owner, limit=100):
        out = []
        for _ in range(limit):
            d = await self.claim_one(owner)
            if not d:
                break
            out.append(d)
        return out

    async def consumer_handle(self, event_id):
        try:
            await self.consumer.insert_one({"event_id": event_id, "processed_at": now()})
            return True
        except DuplicateKeyError:
            await self.inc("audit_outbox_idempotency_hit_total")
            await self.inc("audit_outbox_duplicate_dropped_total")
            return False

    async def bus_publish(self, ev, mode=None):
        if mode == "fail":
            raise RuntimeError("EVENT_BUS_UNAVAILABLE")
        if mode == "timeout":
            await asyncio.wait_for(asyncio.sleep(1), timeout=0.05)
        await self.bus.insert_one({"event_id": ev["event_id"], "event_type": ev["event_type"], "delivered_at": now()})
        await self.consumer_handle(ev["event_id"])
        if mode == "crash_before_ack":
            raise PublisherCrash("publisher crashed before ack")

    async def mark_published(self, event_id):
        res = await self.outbox.update_one({"event_id": event_id, "published_at": None},
                                           {"$set": {"published_at": now(), "delivered_at": now(), "claim_owner": None, "claim_until": None}})
        return res.modified_count == 1

    async def _handle_failure(self, ev, err, keep_claim=False):
        retry = ev["retry_count"] + 1
        await self.inc("audit_outbox_publish_failures_total")
        if retry > core.MAX_RETRIES:
            dl = {k: v for k, v in ev.items() if k != "_id"}
            dl.update({"retry_count": retry, "last_error": str(err), "moved_at": now(), "replay_claim_owner": None, "replay_claimed_at": None})
            try:
                await self.dead.insert_one(dl)
            except DuplicateKeyError:
                pass
            await self.outbox.delete_one({"event_id": ev["event_id"], "published_at": None})
            await self.inc("audit_outbox_dead_letter_total")
            return "dead_letter"
        upd = {"retry_count": retry, "last_error": str(err) or type(err).__name__,
               "next_retry_at": now() + timedelta(seconds=core.next_retry_delay(retry))}
        if not keep_claim:
            upd.update({"claim_owner": None, "claim_until": None})
        await self.outbox.update_one({"event_id": ev["event_id"]}, {"$set": upd})
        await self.inc("audit_outbox_retry_total")
        return "retry"

    async def publish_event(self, ev, mode=None):
        await self.inc("audit_outbox_publish_attempts_total")
        try:
            await self.bus_publish(ev, mode)
        except PublisherCrash:
            return "crashed"
        except asyncio.TimeoutError:
            return await self._handle_failure(ev, "EVENT_BUS_TIMEOUT", keep_claim=True) and "timeout"
        except Exception as e:
            return await self._handle_failure(ev, e)
        await self.mark_published(ev["event_id"])
        await self.inc("audit_outbox_publish_success_total")
        return "published"

    async def publish_batch(self, owner="publisher-a", mode=None, limit=100):
        claimed = await self.claim_batch(owner, limit)
        results = {}
        for ev in claimed:
            r = await self.publish_event(ev, mode)
            results[r] = results.get(r, 0) + 1
        return {"claimed": len(claimed), "results": results}

    async def expire_claim(self, event_id):
        await self.outbox.update_one({"event_id": event_id}, {"$set": {"claim_until": now() - timedelta(seconds=1)}})
        await self.inc("audit_outbox_claim_expired_total")

    async def recover_expired_claims(self):
        res = await self.outbox.update_many({"published_at": None, "claim_until": {"$lt": now()}},
                                            {"$set": {"claim_owner": None, "claim_until": None}})
        if res.modified_count:
            await self.inc("audit_outbox_claim_recovered_total", res.modified_count)
        return res.modified_count

    async def replay_dead_letter(self, event_id, worker="replay-worker", inject_failure=False):
        await self.inc("audit_outbox_replay_total")
        row = await self.dead.find_one_and_update({"event_id": event_id, "replay_claim_owner": None},
                                                  {"$set": {"replay_claim_owner": worker, "replay_claimed_at": now()}})
        if not row:
            if await self.dead.find_one({"event_id": event_id}):
                raise FrasError("REPLAY_IN_PROGRESS")
            raise FrasError("DEAD_LETTER_NOT_FOUND")
        release = {"$set": {"replay_claim_owner": None, "replay_claimed_at": None}}
        if await self.consumer.find_one({"event_id": event_id}) or await self.outbox.find_one({"event_id": event_id, "published_at": {"$ne": None}}):
            await self.dead.delete_one({"event_id": event_id, "replay_claim_owner": worker})
            await self.inc("audit_outbox_duplicate_replay_total")
            return False
        try:
            if inject_failure:
                raise RuntimeError("Injected failure during replay")
            try:
                await self.outbox.insert_one({"event_id": row["event_id"], "event_type": row["event_type"], "payload": row["payload"],
                                              "created_at": row["created_at"], "published_at": None, "delivered_at": None,
                                              "retry_count": 0, "next_retry_at": None, "last_error": None,
                                              "claim_owner": None, "claim_until": None})
            except DuplicateKeyError:
                await self.inc("audit_outbox_duplicate_replay_total")
            await self.dead.delete_one({"event_id": event_id, "replay_claim_owner": worker})
        except Exception:
            await self.dead.update_one({"event_id": event_id}, release)
            await self.inc("audit_outbox_replay_failure_total")
            raise
        await self.inc("audit_outbox_replay_success_total")
        await self.inc("audit_outbox_replayed_total")
        return True

    async def replay_all(self, worker="replay-worker"):
        ids = [d["event_id"] async for d in self.dead.find({"replay_claim_owner": None}, {"event_id": 1})]
        results = {"replayed": 0, "skipped": 0}
        for i in ids:
            ok = await self.replay_dead_letter(i, worker)
            results["replayed" if ok else "skipped"] += 1
        return results

    # ---------- settings / validator ----------
    async def get_settings(self):
        s = await self.settings.find_one({"_id": "global"}) or {}
        return {"busMode": s.get("busMode"), "governanceFrozen": s.get("governanceFrozen", False),
                "validatorsFrozen": s.get("validatorsFrozen", False), "safeMode": s.get("safeMode", False)}

    async def set_settings(self, **kw):
        await self.settings.update_one({"_id": "global"}, {"$set": kw}, upsert=True)
        return await self.get_settings()

    # ---------- rollbacks ----------
    async def create_rollback(self, req, requester):
        v = await self.validator.find_one({"_id": "self"})
        rb_id = str(uuid.uuid4())
        try:
            core.validate_rollback(req, KNOWN_VERSIONS, v["validatorVersion"])
        except FrasError as e:
            await self.append_audit("ROLLBACK_VALIDATION_FAILED", requester, metadata={"rollbackId": rb_id, "reason": e.code, "level": req.get("level")})
            raise
        doc = {"rollback_id": rb_id, "level": req["level"], "reason": req["reason"], "ticket_id": req["ticketId"],
               "incident_id": req["incidentId"], "current_version": v["validatorVersion"], "target_version": req["targetVersion"],
               "requester": requester, "approvals": [], "status": "requested", "history": ["ROLLBACK_REQUESTED"], "created_at": iso()}
        await self.rollbacks.insert_one(dict(doc))
        await self.append_audit("ROLLBACK_REQUESTED", requester, metadata=self._rb_meta(doc))
        return doc

    @staticmethod
    def _rb_meta(doc, **extra):
        return {"rollbackId": doc["rollback_id"], "level": doc["level"], "ticketId": doc["ticket_id"], "targetVersion": doc["target_version"], **extra}

    async def get_rollback(self, rb_id):
        doc = await self.rollbacks.find_one({"rollback_id": rb_id}, {"_id": 0})
        if not doc:
            raise FrasError("ROLLBACK_NOT_FOUND")
        return doc

    async def approve_rollback(self, rb_id, approver):
        doc = await self.get_rollback(rb_id)
        if doc["status"] != "requested":
            raise FrasError("INVALID_STATE", f"Rollback is {doc['status']}")
        if approver == doc["requester"]:
            await self.append_audit("ROLLBACK_APPROVAL_DENIED", approver, metadata=self._rb_meta(doc, reason="SELF_APPROVAL_FORBIDDEN"))
            raise FrasError("SELF_APPROVAL_FORBIDDEN")
        res = await self.rollbacks.update_one({"rollback_id": rb_id, "approvals": {"$ne": approver}},
                                              {"$push": {"approvals": approver, "history": "ROLLBACK_APPROVED"}})
        if res.modified_count != 1:
            raise FrasError("DUPLICATE_APPROVAL")
        await self.append_audit("ROLLBACK_APPROVED", approver, metadata=self._rb_meta(doc))
        return await self.get_rollback(rb_id)

    async def reject_rollback(self, rb_id, actor):
        doc = await self.get_rollback(rb_id)
        if doc["status"] != "requested":
            raise FrasError("INVALID_STATE")
        await self.rollbacks.update_one({"rollback_id": rb_id}, {"$set": {"status": "rejected"}, "$push": {"history": "ROLLBACK_REJECTED"}})
        await self.append_audit("ROLLBACK_REJECTED", actor, metadata=self._rb_meta(doc))
        return await self.get_rollback(rb_id)

    async def execute_rollback(self, rb_id, actor="rollback-service"):
        doc = await self.get_rollback(rb_id)
        if doc["status"] != "requested":
            raise FrasError("INVALID_STATE")
        core.validate_approvals(doc["level"], doc["approvals"], doc["requester"])
        settings = await self.get_settings()
        if settings["governanceFrozen"] and doc["level"] == "L3":
            raise FrasError("GOVERNANCE_FREEZE")
        lvl = doc["level"]
        if lvl == "L1":
            await self.set_settings(validatorsFrozen=True)
        elif lvl == "L2":
            await self.set_settings(governanceFrozen=True)
        elif lvl == "L3":
            await self.validator.update_one({"_id": "self"}, {"$set": {"validatorVersion": doc["target_version"], "coreVersion": doc["target_version"],
                                                                       "runtimeVersion": doc["target_version"], "channel": core.channel_of(doc["target_version"])}})
        else:
            await self.set_settings(safeMode=True, governanceFrozen=True, validatorsFrozen=True)
        await self.rollbacks.update_one({"rollback_id": rb_id}, {"$set": {"status": "executed", "executed_at": iso()}, "$push": {"history": "ROLLBACK_EXECUTED"}})
        await self.append_audit("ROLLBACK_EXECUTED", actor, metadata=self._rb_meta(doc))
        return await self.get_rollback(rb_id)

    async def verify_rollback(self, rb_id, actor="validator-ops"):
        doc = await self.get_rollback(rb_id)
        if doc["status"] != "executed":
            raise FrasError("INVALID_STATE", "ROLLBACK_VERIFIED requires execution")
        chain = await self.verify_chain()
        if not chain["valid"]:
            raise FrasError("INVALID_STATE", f"Audit chain invalid: {chain['code']}")
        await self.rollbacks.update_one({"rollback_id": rb_id}, {"$set": {"status": "verified"}, "$push": {"history": "ROLLBACK_VERIFIED"}})
        await self.append_audit("ROLLBACK_VERIFIED", actor, metadata=self._rb_meta(doc))
        return await self.get_rollback(rb_id)
