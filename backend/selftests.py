"""Built-in Frasberg self-test runner: unit, contract, property (seeded + shrinking), race and chaos suites."""
import asyncio
import copy
import os
import random
import string
import time
from datetime import timedelta

import core
from core import FrasError
from store import Store, now

TESTS = []


def t(suite, name):
    def deco(fn):
        TESTS.append({"suite": suite, "name": name, "fn": fn})
        return fn
    return deco


def raises(code, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except FrasError as e:
        assert code is None or e.code == code or e.message == code, f"expected {code}, got {e.code}"
        return
    raise AssertionError(f"expected {code} to be raised")


async def araises(code, coro):
    try:
        await coro
    except FrasError as e:
        assert code is None or e.code == code, f"expected {code}, got {e.code}"
        return
    except Exception:
        if code is None:
            return
        raise
    raise AssertionError(f"expected {code} to be raised")


VALID_POLICY = {"policyId": "policy-1", "version": "1.0.0", "network": "frasberg-mainnet", "epoch": 900,
                "requirements": {"validatorMin": "6.2.0", "validatorRecommended": "6.4.1", "validatorChannel": "stable", "luchiiMin": "4.0.0"},
                "publishedAt": "2026-09-28T00:00:00Z"}


def policy(**kw):
    p = copy.deepcopy(VALID_POLICY)
    p.update(kw)
    return p


# ---------- property engine (seed replay + shrinking) ----------
def shrink_int(x):
    out = []
    if x != 0:
        out.append(0)
        out.append(x // 2)
        out.append(x - 1 if x > 0 else x + 1)
    return [v for v in out if v != x]


def shrink_str(s):
    return [s[:i] + s[i + 1:] for i in range(len(s))] + ([s[: len(s) // 2]] if len(s) > 1 else [])


def prop(gen, check, shrinker, seed, runs=1000):
    rng = random.Random(seed)
    for i in range(runs):
        value = gen(rng)
        if not check(value):
            cur, steps = value, 0
            changed = True
            while changed and steps < 500:
                changed = False
                for cand in shrinker(cur):
                    steps += 1
                    if not check(cand):
                        cur, changed = cand, True
                        break
            raise AssertionError(f"property failed after {i + 1} runs; seed={seed}; counterexample={value!r}; shrunk={cur!r}")
    return f"{runs} runs, seed={seed}"


def gen_int(min_v=0, max_v=2 ** 53):
    return lambda r: r.choice([min_v, min_v + 1, max_v, r.randint(min_v, max_v), r.randint(min_v, min_v + 1000)])


def gen_str(r):
    alphabet = string.printable + "éß漢"
    return "".join(r.choice(alphabet) for _ in range(r.randint(0, 12)))


# ======================= semver & channels =======================
@t("semver", "compareVersions orders MAJOR.MINOR.PATCH")
def _(ctx):
    assert core.compare_versions("6.4.1", "6.2.0") == 1 and core.compare_versions("6.2.0", "6.4.1") == -1
    assert core.compare_versions("6.2.0", "6.2.0") == 0


@t("semver", "isValidatorCompliant spec example")
def _(ctx):
    r = core.is_validator_compliant({"validatorVersion": "6.4.1", "luchiiVersion": "4.2.0"},
                                    {"validator": {"minimum": "6.2.0"}, "luchii": {"minimum": "4.0.0"}})
    assert r == {"compliant": True, "validatorPass": True, "luchiiPass": True}


@t("semver", "stable rejects rc")
def _(ctx):
    assert core.validate_version("6.2.0-rc.1", "6.2.0", "stable", "rc")["compliant"] is False


@t("semver", "rc accepts rc")
def _(ctx):
    assert core.validate_version("6.2.0-rc.1", "6.2.0-rc.1", "rc", "rc")["compliant"] is True


@t("semver", "beta rejects alpha")
def _(ctx):
    r = core.validate_version("6.2.0-alpha.1", "6.2.0-alpha.1", "beta", "alpha")
    assert r == {"compliant": False, "code": "INVALID_CHANNEL"}


@t("semver", "invalid version code")
def _(ctx):
    assert core.validate_version("latest", "6.2.0", "stable", "stable")["code"] == "INVALID_VERSION"
    assert core.validate_version("6.1.9", "6.2.0", "stable", "stable")["code"] == "VERSION_TOO_OLD"


@t("semver", "prerelease precedence rc < release")
def _(ctx):
    assert core.semver_cmp("6.2.0-rc.1", "6.2.0") == -1 and core.semver_cmp("6.2.0-rc.2", "6.2.0-rc.10") == -1
    assert core.semver_cmp("6.2.0-alpha", "6.2.0-beta") == -1


@t("semver", "version classes supported/deprecated/required/unsupported")
def _(ctx):
    req = {"validator": {"minimum": "6.2.0", "recommended": "6.4.1", "maximum": "6.x"}}
    assert core.version_class("6.4.1", req) == "supported"
    assert core.version_class("6.3.0", req) == "deprecated"
    assert core.version_class("6.2.0", req) == "required"
    assert core.version_class("6.1.3", req) == "unsupported" and core.version_class("7.0.0", req) == "unsupported"


@t("semver", "epoch schedule maximum 4.9.x")
def _(ctx):
    assert core.within_max("4.9.7", "4.9.x") and not core.within_max("4.10.0", "4.9.x") and core.within_max("4.2.1", "4.9.x")


# ======================= schema contracts =======================
@t("contracts", "accepts valid policy")
def _(ctx):
    assert core.schema_valid("validator-policy", VALID_POLICY)


@t("contracts", "rejects missing policyId")
def _(ctx):
    assert not core.schema_valid("validator-policy", {"version": "1.0.0"})


@t("contracts", "rejects unknown properties (injected)")
def _(ctx):
    assert not core.schema_valid("validator-policy", policy(injected=True))


@t("contracts", "required-versions: rejects missing validator / epoch / negative epoch / invalid semver / extra props")
def _(ctx):
    for bad in [{"epoch": 900}, {"validator": {"minimum": "latest"}}, {"network": "frasberg-mainnet"}, {"epoch": -1}, {"epoch": 900, "evil": True}]:
        raises("INVALID_POLICY", core.validate_response, "required-versions", bad)


@t("contracts", "version schema: rejects nightly channel, missing validatorVersion, hack prop")
def _(ctx):
    base = {"validatorId": "fras_val_001", "validatorVersion": "6.4.1", "channel": "stable", "luchiiVersion": "4.2.0", "epoch": 900}
    assert core.schema_valid("validator-version", base)
    assert not core.schema_valid("validator-version", {**base, "channel": "nightly"})
    assert not core.schema_valid("validator-version", {"epoch": 100})
    assert not core.schema_valid("validator-version", {**base, "hack": True})


@t("contracts", "rejects invalid publishedAt date-time")
def _(ctx):
    assert not core.schema_valid("validator-policy", policy(publishedAt="yesterday"))


@t("property", "valid epochs >= 0 always accepted")
def _(ctx):
    return prop(gen_int(0), lambda e: core.schema_valid("validator-policy", policy(epoch=e)), shrink_int, ctx["seed"], ctx["runs"])


@t("property", "negative epochs always rejected")
def _(ctx):
    return prop(gen_int(-(2 ** 53), -1), lambda e: not core.schema_valid("validator-policy", policy(epoch=e)), shrink_int, ctx["seed"], ctx["runs"])


@t("property", "unknown channels always rejected")
def _(ctx):
    def check(ch):
        if ch in core.CHANNELS:
            return True
        p = policy()
        p["requirements"]["validatorChannel"] = ch
        return not core.schema_valid("validator-policy", p)
    return prop(gen_str, check, shrink_str, ctx["seed"], ctx["runs"])


@t("property", "canonical JSON is key-order independent")
def _(ctx):
    def gen(r):
        keys = list(VALID_POLICY.keys())
        r.shuffle(keys)
        return keys
    return prop(gen, lambda ks: core.canonicalize({k: VALID_POLICY[k] for k in ks}) == core.canonicalize(VALID_POLICY),
                lambda x: [], ctx["seed"], min(ctx["runs"], 300))


@t("property", "semver compare is antisymmetric")
def _(ctx):
    def gen(r):
        mk = lambda: f"{r.randint(0, 9)}.{r.randint(0, 9)}.{r.randint(0, 9)}" + r.choice(["", "-rc.1", "-beta.2", "-alpha"])
        return (mk(), mk())
    return prop(gen, lambda p: core.semver_cmp(p[0], p[1]) == -core.semver_cmp(p[1], p[0]), lambda x: [], ctx["seed"], ctx["runs"])


# ======================= signatures =======================
@t("signatures", "canonical form matches spec example")
def _(ctx):
    assert core.canonicalize({"network": "frasberg-mainnet", "epoch": 900, "validator": {"minimum": "6.2.0"}}) == \
        '{"epoch":900,"network":"frasberg-mainnet","validator":{"minimum":"6.2.0"}}'


@t("signatures", "accepts valid / rejects modified policy / modified signature / wrong key")
def _(ctx):
    priv, pub = core.generate_keypair()
    _, other = core.generate_keypair()
    sig = core.sign_policy(VALID_POLICY, priv)
    assert core.verify_policy(VALID_POLICY, sig, pub)
    assert not core.verify_policy({**VALID_POLICY, "epoch": 901}, sig, pub)
    raw = bytearray(__import__("base64").b64decode(sig))
    raw[0] ^= 1
    assert not core.verify_policy(VALID_POLICY, core.b64(bytes(raw)), pub)
    assert not core.verify_policy(VALID_POLICY, sig, other)


@t("signatures", "revoked key fails verification with KEY_REVOKED; unknown key KEY_UNKNOWN")
async def _(ctx):
    s = ctx["store"]
    await s.create_key()
    env = await s.sign_policy(VALID_POLICY)
    assert (await s.verify_signed(env))["code"] == "COMPLIANT"
    await s.revoke_key(env["keyId"])
    r = await s.verify_signed(env)
    assert r == {"valid": False, "code": "KEY_REVOKED"}
    assert (await s.verify_signed({**env, "keyId": "validator-key-999"}))["code"] == "KEY_UNKNOWN"
    assert (await s.verify_signed({**env, "policy": {**VALID_POLICY, "evil": 1}}))["code"] == "INVALID_POLICY"


# ======================= key lifecycle =======================
@t("keys", "rotation retires old key and activates new; old signatures remain valid")
async def _(ctx):
    s = ctx["store"]
    k1, k2 = await s.create_key(), await s.create_key()
    env = await s.sign_policy(VALID_POLICY)
    await s.rotate_key(k1["key_id"], k2["key_id"])
    assert (await s.get_key(k1["key_id"]))["status"] == "retired" and (await s.get_key(k2["key_id"]))["status"] == "active"
    assert (await s.verify_signed(env))["valid"] is True


@t("keys", "rollback restores old active key; revoked key cannot roll back")
async def _(ctx):
    s = ctx["store"]
    k1, k2 = await s.create_key(), await s.create_key()
    await s.rotate_key(k1["key_id"], k2["key_id"])
    await s.rollback_rotation(k1["key_id"], k2["key_id"])
    assert (await s.get_key(k1["key_id"]))["status"] == "active" and (await s.get_key(k2["key_id"]))["status"] == "retired"
    await s.revoke_key(k1["key_id"])
    await araises("ROLLBACK_NOT_ALLOWED", s.rollback_rotation(k1["key_id"], k2["key_id"]))


@t("keys", "revocation rules: revoked cannot verify, rotating cannot sign")
def _(ctx):
    assert core.can_verify("revoked") is False and core.can_sign("rotating") is False and core.can_sign("active")


@t("keys", "every transition emits an audit event")
async def _(ctx):
    s = ctx["store"]
    k1, k2 = await s.create_key(), await s.create_key()
    await s.rotate_key(k1["key_id"], k2["key_id"])
    await s.rollback_rotation(k1["key_id"], k2["key_id"])
    await s.revoke_key(k2["key_id"])
    types = [r["event_type"] for r in await s.audit_records()]
    for e in ["KEY_CREATED", "KEY_ROTATION_STARTED", "KEY_ROTATION_COMPLETED", "KEY_ROTATION_ROLLED_BACK", "KEY_REVOKED"]:
        assert e in types, e


@t("keys", "failed rotation leaves no COMPLETED event and no key state change")
async def _(ctx):
    s = ctx["store"]
    k1, k2 = await s.create_key(), await s.create_key()
    await araises("ROTATION_FAILED", s.rotate_key(k1["key_id"], k2["key_id"], inject_failure=True))
    assert not any(r["event_type"] == "KEY_ROTATION_COMPLETED" for r in await s.audit_records())
    assert (await s.get_key(k1["key_id"]))["status"] == "active"
    assert not await s.locks.find_one({}), "lock must be released"


@t("keys", "concurrent rotation of same key rejected with ROTATION_IN_PROGRESS")
async def _(ctx):
    s = ctx["store"]
    k1, k2 = await s.create_key(), await s.create_key()
    r = await asyncio.gather(s.rotate_key(k1["key_id"], k2["key_id"], owner="node-a", hold_ms=150),
                             s.rotate_key(k1["key_id"], k2["key_id"], owner="node-b", hold_ms=150), return_exceptions=True)
    errs = [x for x in r if isinstance(x, FrasError)]
    assert len(errs) == 1 and errs[0].code in ("ROTATION_IN_PROGRESS", "INVALID_STATE")


# ======================= audit chain =======================
async def _chain(s, n=6):
    for i in range(n):
        await s.append_audit("KEY_CREATED", "validator-admin", f"key-{i}")
    return await s.audit_records()


@t("audit", "accepts intact chain")
async def _(ctx):
    assert core.verify_audit_chain(await _chain(ctx["store"]))["valid"]


@t("audit", "rejects modified payload / broken linkage / hash tampering")
async def _(ctx):
    recs = await _chain(ctx["store"])
    a = copy.deepcopy(recs); a[1]["payload"] = {"tampered": True}
    b = copy.deepcopy(recs); b[1]["previous_hash"] = "fake"
    c = copy.deepcopy(recs); c[5]["current_hash"] = "tampered"
    assert [core.verify_audit_chain(x)["valid"] for x in (a, b, c)] == [False, False, False]


@t("audit", "detects missing event (sequence gap)")
async def _(ctx):
    recs = await _chain(ctx["store"])
    recs.pop(3)
    assert core.verify_audit_chain(recs)["code"] == "SEQUENCE_GAP"


@t("audit", "concurrent appends keep a single linear chain")
async def _(ctx):
    s = ctx["store"]
    await asyncio.gather(*[s.append_audit("TEST_RUN", f"w{i}") for i in range(20)])
    assert (await s.verify_chain())["valid"]


@t("audit", "failure before commit writes neither audit nor outbox")
async def _(ctx):
    s = ctx["store"]
    await araises("INTERNAL_ERROR", s.append_audit("KEY_CREATED", "x", fail_before_commit=True))
    assert await s.audit.count_documents({}) == 0 and await s.outbox.count_documents({}) == 0


@t("audit", "revocation event shape")
async def _(ctx):
    s = ctx["store"]
    k = await s.create_key()
    await s.revoke_key(k["key_id"])
    rec = (await s.audit_records())[-1]
    assert rec["event_type"] == "KEY_REVOKED" and rec["payload"]["actor"] == "validator-admin" and rec["payload"]["keyId"] == k["key_id"]


# ======================= locks & fencing =======================
@t("locks", "single winner across 3 concurrent acquirers")
async def _(ctx):
    s = ctx["store"]
    r = await asyncio.gather(*[s.acquire_lock("rotation:key1", n) for n in ("node-a", "node-b", "node-c")], return_exceptions=True)
    assert len([x for x in r if isinstance(x, int)]) == 1


@t("locks", "different keys acquire independently")
async def _(ctx):
    s = ctx["store"]
    r = await asyncio.gather(s.acquire_lock("rotation:key1", "node-a"), s.acquire_lock("rotation:key2", "node-b"))
    assert len(r) == 2


@t("locks", "expired lock is reclaimed; tokens increase monotonically")
async def _(ctx):
    s = ctx["store"]
    t1 = await s.acquire_lock("rotation:key1", "node-a")
    await s.expire_lock("rotation:key1")
    t2 = await s.acquire_lock("rotation:key1", "node-b")
    assert t2 > t1


@t("locks", "stale owner cannot release")
async def _(ctx):
    s = ctx["store"]
    t1 = await s.acquire_lock("rotation:key1", "node-a")
    await s.expire_lock("rotation:key1")
    await s.acquire_lock("rotation:key1", "node-b")
    await araises("LOCK_NOT_OWNER", s.release_lock("rotation:key1", t1))


@t("locks", "renewal: owner ok, non-owner LOCK_NOT_OWNER, expired LOCK_EXPIRED, outage LOCK_RENEWAL_FAILED")
async def _(ctx):
    s = ctx["store"]
    await s.acquire_lock("validator-key-1", "node-a")
    assert await s.renew_lock("validator-key-1", "node-a")
    await araises("LOCK_NOT_OWNER", s.renew_lock("validator-key-1", "node-b"))
    await araises("LOCK_EXPIRED", s.renew_lock("expired-lock", "node-a"))
    await araises("LOCK_RENEWAL_FAILED", s.renew_lock("validator-key-1", "node-a", fail=True))
    await s.transfer_lock("validator-key-1", "node-b")
    await araises("LOCK_NOT_OWNER", s.renew_lock("validator-key-1", "node-a"))


@t("locks", "fence: validateFence & ensureFence semantics")
def _(ctx):
    assert core.validate_fence(11, 10) is False and core.validate_fence(11, 11) is True
    raises("STALE_FENCING_TOKEN", core.ensure_fence, 11, 10)
    core.ensure_fence(11, 11)
    core.ensure_fence(11, 12)


@t("locks", "atomic fenced update: 10->11 ok, 11->10 rejected, 11->11 rejected")
async def _(ctx):
    s = ctx["store"]
    k = await s.create_key()
    await s.fenced_update(k["key_id"], {"status": "active"}, 10)
    await s.fenced_update(k["key_id"], {"status": "active"}, 11)
    await araises("STALE_FENCING_TOKEN", s.fenced_update(k["key_id"], {"status": "retired"}, 10))
    await araises("STALE_FENCING_TOKEN", s.fenced_update(k["key_id"], {"status": "retired"}, 11))


@t("locks", "concurrent fenced writers: highest token wins")
async def _(ctx):
    s = ctx["store"]
    k = await s.create_key()
    await s.fenced_update(k["key_id"], {}, 11)
    r = await asyncio.gather(s.fenced_update(k["key_id"], {}, 10), s.fenced_update(k["key_id"], {}, 12), return_exceptions=True)
    assert len([x for x in r if x is None]) == 1 and (await s.get_key(k["key_id"]))["fencing_token"] == 12


# ======================= outbox =======================
@t("outbox", "same event inserted once (dedup constraint)")
async def _(ctx):
    s = ctx["store"]
    await s.outbox.insert_one({"event_id": "evt-1"})
    try:
        await s.outbox.insert_one({"event_id": "evt-1"})
        raise AssertionError("duplicate accepted")
    except Exception as e:
        assert "duplicate" in str(e).lower()


@t("outbox", "only one publisher claims an event")
async def _(ctx):
    s = ctx["store"]
    rec = await s.append_audit("KEY_CREATED", "x")
    r = await asyncio.gather(s.claim_one("pub-a", event_id=rec["event_id"]), s.claim_one("pub-b", event_id=rec["event_id"]))
    assert len([x for x in r if x]) == 1


@t("outbox", "publisher crash before ack: claim expires, event recovered, consumer dedups")
async def _(ctx):
    s = ctx["store"]
    rec = await s.append_audit("KEY_CREATED", "x")
    ev = await s.claim_one("publisher-a")
    assert await s.publish_event(ev, "crash_before_ack") == "crashed"
    assert await s.claim_one("publisher-b") is None, "claim lease still held"
    await s.expire_claim(rec["event_id"])
    recovered = await s.claim_one("publisher-b")
    assert recovered["event_id"] == rec["event_id"] and recovered["claim_owner"] == "publisher-b"
    assert await s.publish_event(recovered) == "published"
    assert await s.consumer.count_documents({"event_id": rec["event_id"]}) == 1
    assert await s.counter("audit_outbox_idempotency_hit_total") >= 1


@t("outbox", "consumer deduplicates replayed event")
async def _(ctx):
    s = ctx["store"]
    assert await s.consumer_handle("evt-x") is True and await s.consumer_handle("evt-x") is False
    assert await s.consumer.count_documents({"event_id": "evt-x"}) == 1


@t("outbox", "retry schedule 0/60/300/900/3600 then dead-letter")
async def _(ctx):
    s = ctx["store"]
    assert [core.next_retry_delay(i) for i in range(7)] == [0, 60, 300, 900, 3600, 3600, 3600]
    rec = await s.append_audit("KEY_CREATED", "x")
    for _ in range(core.MAX_RETRIES + 1):
        await s.outbox.update_one({"event_id": rec["event_id"]}, {"$set": {"next_retry_at": None}})
        await s.publish_batch(mode="fail")
    assert await s.dead.find_one({"event_id": rec["event_id"]}) and not await s.outbox.find_one({"event_id": rec["event_id"]})


@t("outbox", "bus timeout keeps claim (not published) and is recoverable")
async def _(ctx):
    s = ctx["store"]
    rec = await s.append_audit("KEY_CREATED", "x")
    await s.publish_batch(mode="timeout")
    doc = await s.outbox.find_one({"event_id": rec["event_id"]})
    assert doc["published_at"] is None and doc["claim_owner"] == "publisher-a"
    await s.expire_claim(rec["event_id"])
    assert await s.recover_expired_claims() == 1
    await s.outbox.update_one({"event_id": rec["event_id"]}, {"$set": {"next_retry_at": None}})
    assert await s.claim_one("publisher-b")


async def _dead(s):
    rec = await s.append_audit("KEY_CREATED", "x")
    ev = await s.outbox.find_one({"event_id": rec["event_id"]})
    ev["retry_count"] = core.MAX_RETRIES
    await s._handle_failure(ev, "boom")
    return rec["event_id"]


@t("outbox", "dead-letter replay requeues and removes; duplicate replay idempotent")
async def _(ctx):
    s = ctx["store"]
    eid = await _dead(s)
    assert await s.replay_dead_letter(eid) is True
    assert await s.outbox.count_documents({"event_id": eid}) == 1 and not await s.dead.find_one({"event_id": eid})
    await araises("DEAD_LETTER_NOT_FOUND", s.replay_dead_letter(eid))
    assert await s.outbox.count_documents({"event_id": eid}) == 1


@t("outbox", "published event is not replayed")
async def _(ctx):
    s = ctx["store"]
    eid = await _dead(s)
    await s.consumer_handle(eid)
    assert await s.replay_dead_letter(eid) is False


@t("outbox", "replay failure keeps dead-letter row and writes no outbox row")
async def _(ctx):
    s = ctx["store"]
    eid = await _dead(s)
    try:
        await s.replay_dead_letter(eid, inject_failure=True)
        raise AssertionError("expected failure")
    except RuntimeError:
        pass
    assert await s.dead.find_one({"event_id": eid}) and not await s.outbox.find_one({"event_id": eid})


@t("outbox", "concurrent replay: only one worker wins")
async def _(ctx):
    s = ctx["store"]
    eid = await _dead(s)
    r = await asyncio.gather(s.replay_dead_letter(eid, "worker-a"), s.replay_dead_letter(eid, "worker-b"), return_exceptions=True)
    assert len([x for x in r if x is True]) == 1 and await s.outbox.count_documents({"event_id": eid}) == 1


@t("outbox", "stale claim recovery makes event available to another worker")
async def _(ctx):
    s = ctx["store"]
    rec = await s.append_audit("KEY_CREATED", "x")
    await s.outbox.update_one({"event_id": rec["event_id"]}, {"$set": {"claim_owner": "worker-a", "claim_until": now() - timedelta(minutes=1)}})
    await s.recover_expired_claims()
    assert (await s.claim_one("worker-b"))["claim_owner"] == "worker-b"


# ======================= rollback =======================
RB = {"level": "L3", "reason": "critical consensus issue", "ticketId": "OPS-1", "incidentId": "INC-1", "targetVersion": "6.2.0"}


@t("rollback", "input validation edge cases")
def _(ctx):
    kv, cur = ["6.2.0", "6.4.1"], "6.4.1"
    raises("INVALID_LEVEL", core.validate_rollback, {**RB, "level": "L5"}, kv, cur)
    raises("INVALID_REASON", core.validate_rollback, {**RB, "reason": ""}, kv, cur)
    raises("INVALID_REASON", core.validate_rollback, {**RB, "reason": "x" * 1000}, kv, cur)
    raises("INVALID_TARGET_VERSION", core.validate_rollback, {**RB, "targetVersion": "latest"}, kv, cur)
    raises("INVALID_TICKET", core.validate_rollback, {k: v for k, v in RB.items() if k != "ticketId"}, kv, cur)
    raises("INCIDENT_REQUIRED", core.validate_rollback, {**RB, "incidentId": None}, kv, cur)
    raises("TARGET_VERSION_UNKNOWN", core.validate_rollback_target, "999.999.999", None, kv)
    raises("INVALID_ROLLBACK_DIRECTION", core.validate_rollback_target, "8.0.0", "7.0.0")


@t("rollback", "approval gates L1=0, L2=1, L3=2, L4=3; duplicates counted once; no self approval")
def _(ctx):
    core.validate_approvals("L1", [])
    raises("INSUFFICIENT_APPROVALS", core.validate_approvals, "L2", [])
    core.validate_approvals("L2", ["platform-lead"])
    raises("INSUFFICIENT_APPROVALS", core.validate_approvals, "L3", ["platform-lead"])
    core.validate_approvals("L3", ["platform-lead", "security-lead"])
    raises("INSUFFICIENT_APPROVALS", core.validate_approvals, "L4", ["platform-lead", "security-lead"])
    core.validate_approvals("L4", ["platform-lead", "security-lead", "governance-lead"])
    raises("INSUFFICIENT_APPROVALS", core.validate_approvals, "L3", ["security-lead", "security-lead"])
    raises("SELF_APPROVAL_FORBIDDEN", core.validate_approvals, "L3", ["platform-lead", "security-lead"], "platform-lead")


@t("rollback", "lifecycle history invariants")
def _(ctx):
    raises("INSUFFICIENT_APPROVALS", core.validate_rollback_history, ["ROLLBACK_REQUESTED"])
    assert core.validate_rollback_history(["ROLLBACK_REQUESTED", "ROLLBACK_APPROVED", "ROLLBACK_EXECUTED", "ROLLBACK_VERIFIED"])
    raises("INVALID_STATE", core.validate_rollback_history, ["ROLLBACK_APPROVED"])


@t("rollback", "end-to-end L3: request, approvals, execute, verify, audit trail")
async def _(ctx):
    s = ctx["store"]
    await s.validator.insert_one({"_id": "self", "validatorVersion": "6.4.1"})
    rb = await s.create_rollback(RB, "platform-lead")
    await araises("SELF_APPROVAL_FORBIDDEN", s.approve_rollback(rb["rollback_id"], "platform-lead"))
    await s.approve_rollback(rb["rollback_id"], "security-lead")
    await araises("INSUFFICIENT_APPROVALS", s.execute_rollback(rb["rollback_id"]))
    await s.approve_rollback(rb["rollback_id"], "governance-lead")
    await s.execute_rollback(rb["rollback_id"])
    done = await s.verify_rollback(rb["rollback_id"])
    assert done["status"] == "verified" and (await s.validator.find_one({"_id": "self"}))["validatorVersion"] == "6.2.0"
    types = [r["event_type"] for r in await s.audit_records()]
    assert types.count("ROLLBACK_APPROVED") == 2 and "ROLLBACK_APPROVAL_DENIED" in types and "ROLLBACK_VERIFIED" in types


@t("rollback", "governance freeze blocks L3 rollback")
async def _(ctx):
    s = ctx["store"]
    await s.validator.insert_one({"_id": "self", "validatorVersion": "6.4.1"})
    await s.set_settings(governanceFrozen=True)
    rb = await s.create_rollback(RB, "platform-lead")
    for a in ("security-lead", "governance-lead"):
        await s.approve_rollback(rb["rollback_id"], a)
    await araises("GOVERNANCE_FREEZE", s.execute_rollback(rb["rollback_id"]))


@t("rollback", "error mapping: HTTP status + envelope with requestId")
def _(ctx):
    assert core.ERROR_MAP["INSUFFICIENT_APPROVALS"][0] == 403 and core.ERROR_MAP["SELF_APPROVAL_FORBIDDEN"][0] == 403
    assert core.ERROR_MAP["INVALID_LEVEL"][0] == 400 and core.ERROR_MAP["TARGET_VERSION_UNKNOWN"][0] == 404
    env = core.error_envelope("req_123", "INVALID_LEVEL", "Invalid level.")
    assert env["requestId"] == "req_123" and env["success"] is False and env["error"]["code"] == "INVALID_LEVEL"


# ======================= observability =======================
@t("observability", "alert thresholds incl. exact boundaries and missing data")
def _(ctx):
    f = core.alert_fires
    assert f("LockAvailabilityLow", 0.9994) and not f("LockAvailabilityLow", 0.9995) and not f("LockAvailabilityLow", 0.9999)
    assert f("OutboxBacklog", 1001) and not f("OutboxBacklog", 1000) and not f("OutboxBacklog", 999)
    assert f("ErrorBudgetFastBurn", 14.41) and not f("ErrorBudgetFastBurn", 14.4) and f("ErrorBudgetMediumBurn", 7)
    assert f("ReplayFailures", 0.1) and f("ChaosFailure", 1)
    assert not any(f(n, None) for n in core.ALERT_THRESHOLDS)


@t("observability", "inhibition rules respect environment isolation")
def _(ctx):
    assert core.is_inhibited("PostgresUnavailable", "OutboxBacklog", {"environment": "prod"})
    assert core.is_inhibited("OutboxBacklog", "ReplayFailures", {"environment": "prod"})
    assert core.is_inhibited("LockAvailabilityLow", "ErrorBudgetFastBurn", {"environment": "prod"})
    assert not core.is_inhibited("PostgresUnavailable", "OutboxBacklog", {"environment": "prod"}, {"environment": "staging"})


@t("observability", "metric label allowlist and cardinality guard")
def _(ctx):
    core.validate_labels({"service": "outbox", "environment": "prod"})
    for bad in ("request_id", "event_id", "validator_id"):
        raises(f"INVALID_LABEL:{bad}", core.validate_labels, {bad: "x"})
    raises("LABEL_CARDINALITY_LIMIT", core.validate_label_value_count, 101)


@t("observability", "audit retention classes")
def _(ctx):
    assert core.retention("ROLLBACK_EXECUTED") == "FOREVER" and core.retention("KEY_REVOKED") == "FOREVER"
    assert core.retention("CHAOS_TEST") == "7Y" and core.retention("STALE_FENCING_TOKEN") == "FOREVER"


@t("observability", "grafana dashboard: mandatory panels, titles and queries")
def _(ctx):
    import artifacts
    d = artifacts.GRAFANA_DASHBOARD
    titles = [p["title"] for p in d["panels"]]
    for need in ("Lock Availability", "Outbox Pending", "Chaos Pass Rate"):
        assert need in titles
    assert all(p.get("title") and p.get("targets") for p in d["panels"])


@t("observability", "prometheus rules & alertmanager YAML parse, alerts carry labels and runbooks")
def _(ctx):
    import yaml
    import artifacts
    rules = yaml.safe_load(artifacts.PROMETHEUS_RULES)
    alerts = [r for g in rules["groups"] for r in g["rules"] if "alert" in r]
    assert alerts and all(a["labels"].get("severity") and a["labels"].get("service") for a in alerts)
    assert all("runbook" in a.get("annotations", {}) for a in alerts)
    am = yaml.safe_load(artifacts.ALERTMANAGER)
    assert am["route"]["routes"] and am["inhibit_rules"]


# ======================= chaos =======================
@t("chaos", "PUBLISHER_CRASH: event survives crash, no duplicate processing")
async def _(ctx):
    s = ctx["store"]
    rec = await s.append_audit("KEY_CREATED", "x")
    await s.publish_batch("publisher-a", mode="crash_before_ack")
    await s.expire_claim(rec["event_id"])
    await s.publish_batch("publisher-b")
    assert (await s.outbox.find_one({"event_id": rec["event_id"]}))["published_at"] is not None
    assert await s.consumer.count_documents({}) == 1 and (await s.verify_chain())["valid"]


@t("chaos", "DUPLICATE_DELIVERY: consumer processes once, audit unchanged")
async def _(ctx):
    s = ctx["store"]
    rec = await s.append_audit("KEY_CREATED", "x")
    before = await s.audit.count_documents({})
    ev = await s.outbox.find_one({"event_id": rec["event_id"]})
    await s.bus_publish(ev)
    await s.bus_publish(ev)
    assert await s.consumer.count_documents({"event_id": rec["event_id"]}) == 1 and await s.audit.count_documents({}) == before


@t("chaos", "NETWORK_PARTITION / LOCK_SPLIT_BRAIN: single fencing winner, stale token rejected after heal")
async def _(ctx):
    s = ctx["store"]
    k = await s.create_key()
    r = await asyncio.gather(s.acquire_lock("validator-key", "node-a"), s.acquire_lock("validator-key", "node-b"), return_exceptions=True)
    winners = [x for x in r if isinstance(x, int)]
    assert len(winners) == 1
    await s.expire_lock("validator-key")
    newer = await s.acquire_lock("validator-key", "node-b")
    await s.fenced_update(k["key_id"], {}, newer)
    await araises("STALE_FENCING_TOKEN", s.fenced_update(k["key_id"], {}, winners[0]))


@t("chaos", "EVENT_BUS_TIMEOUT / OUTBOX_BACKLOG: backlog drains to zero after recovery")
async def _(ctx):
    s = ctx["store"]
    for i in range(15):
        await s.append_audit("TEST_RUN", f"w{i}")
    await s.publish_batch(mode="fail")
    assert await s.outbox.count_documents({"published_at": None}) == 15
    await s.outbox.update_many({}, {"$set": {"next_retry_at": None}})
    await s.publish_batch()
    assert await s.outbox.count_documents({"published_at": None}) == 0 and await s.consumer.count_documents({}) == 15


@t("chaos", "DB_FAILOVER (simulated): partial write never commits, chain stays valid")
async def _(ctx):
    s = ctx["store"]
    await _chain(s, 3)
    await araises("INTERNAL_ERROR", s.append_audit("KEY_CREATED", "x", fail_before_commit=True))
    await s.append_audit("KEY_CREATED", "after-failover")
    assert (await s.verify_chain())["valid"] and await s.audit.count_documents({}) == 4


@t("chaos", "AUDIT_REPLAY: dead-letter backlog replays with no duplicates")
async def _(ctx):
    s = ctx["store"]
    ids = [await _dead(s) for _ in range(5)]
    r = await s.replay_all()
    await s.publish_batch()
    assert r["replayed"] == 5 and await s.dead.count_documents({}) == 0
    assert all([await s.consumer.count_documents({"event_id": i}) == 1 for i in ids]) and (await s.verify_chain())["valid"]


async def run_tests(db, suites=None, seed=None, runs=1000):
    seed = int(seed if seed is not None else os.environ.get("FC_SEED", 0) or 0)
    store = Store(db, prefix="selftest_")
    results = []
    started = time.time()
    await store.ensure_indexes()
    for test in TESTS:
        if suites and test["suite"] not in suites:
            continue
        await store.reset()
        ctx = {"store": store, "seed": seed, "runs": runs}
        t0 = time.perf_counter()
        status, detail = "passed", None
        try:
            out = test["fn"](ctx)
            if asyncio.iscoroutine(out):
                out = await out
            detail = out if isinstance(out, str) else None
        except AssertionError as e:
            status, detail = "failed", str(e) or "assertion failed"
        except Exception as e:
            status, detail = "failed", f"{type(e).__name__}: {getattr(e, 'code', '')} {e}"
        results.append({"suite": test["suite"], "name": test["name"], "status": status, "detail": detail,
                        "durationMs": round((time.perf_counter() - t0) * 1000, 1)})
    await store.reset()
    passed = sum(r["status"] == "passed" for r in results)
    return {"seed": seed, "runs": runs, "total": len(results), "passed": passed, "failed": len(results) - passed,
            "durationMs": round((time.time() - started) * 1000), "results": results}


def list_suites():
    out = {}
    for t_ in TESTS:
        out[t_["suite"]] = out.get(t_["suite"], 0) + 1
    return out
