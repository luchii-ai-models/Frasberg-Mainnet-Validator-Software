"""Frasberg validator API regression tests (public API, console, policies, keys, locks, audit, outbox, rollbacks, tests, readiness)."""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
A = f"{BASE_URL}/api"
S = requests.Session()


def env_err(r, code, status=None):
    if status:
        assert r.status_code == status, r.text
    d = r.json()
    assert d["success"] is False and d["requestId"] and d["error"]["code"] == code, d


def put_validator(**kw):
    r = S.put(f"{A}/console/validator", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def clear_settings():
    S.put(f"{A}/console/settings", json={"safeMode": False, "governanceFrozen": False, "validatorsFrozen": False, "clearBusMode": True})


# ---- public API ----
class TestPublic:
    def test_version(self):
        d = S.get(f"{A}/v1/validator/version").json()
        assert d["validatorId"] == "fras_val_001" and d["validatorVersion"] == "6.4.1" and d["luchiiVersion"] == "4.2.0" and d["epoch"] == 900

    def test_status_compliance(self):
        s = S.get(f"{A}/v1/validator/status").json()
        assert s["commission"] == 5 and s["status"] == "healthy"
        c = S.get(f"{A}/v1/validator/compliance").json()
        assert c["compliant"] is True and c["code"] == "COMPLIANT" and c["delegationEligible"] is True

    def test_required_versions(self):
        d = S.get(f"{A}/v1/network/required-versions?epoch=400").json()
        assert d["validator"]["minimum"] == "4.0.0" and d["validator"]["maximum"] == "4.9.x"
        d = S.get(f"{A}/v1/network/required-versions").json()
        assert d["epoch"] == 900 and d["validator"]["minimum"] == "6.2.0"
        env_err(S.get(f"{A}/v1/network/required-versions?epoch=-1"), "INVALID_POLICY", 400)

    def test_schedule_metrics(self):
        sch = S.get(f"{A}/v1/network/schedule").json()
        assert [x["epoch"] for x in sch] == [100, 200, 300, 400, 900, 1000]
        r = S.get(f"{A}/metrics")
        assert r.status_code == 200 and "text/plain" in r.headers["content-type"] and "audit_outbox_pending_total" in r.text


# ---- validator compliance changes ----
class TestValidatorCompliance:
    def test_too_old_and_channel(self):
        try:
            put_validator(validatorVersion="6.1.0")
            c = S.get(f"{A}/v1/validator/compliance").json()
            assert c["code"] == "VERSION_TOO_OLD" and c["delegationEligible"] is False and c["versionClass"] == "unsupported"
            put_validator(validatorVersion="6.4.1", channel="rc")
            c = S.get(f"{A}/v1/validator/compliance").json()
            assert c["code"] == "INVALID_CHANNEL", c
            env_err(S.put(f"{A}/console/validator", json={"validatorVersion": "abc"}), "INVALID_VERSION", 400)
        finally:
            put_validator(validatorVersion="6.4.1", channel="stable")
        assert S.get(f"{A}/v1/validator/compliance").json()["compliant"] is True


# ---- policies + keys ----
POLICY = {"policyId": "TEST_policy", "version": "1.0.0", "network": "frasberg-mainnet", "epoch": 900,
          "requirements": {"validatorMin": "6.2.0", "validatorRecommended": "6.4.1", "validatorChannel": "stable", "luchiiMin": "4.0.0"},
          "publishedAt": "2026-09-28T00:00:00Z"}


def active_key():
    return next(k for k in S.get(f"{A}/console/keys").json() if k["status"] == "active")


class TestPoliciesKeys:
    def test_sign_verify_variants(self):
        env = S.post(f"{A}/console/policies/sign", json={"policy": POLICY}).json()
        v = lambda p, k=env["keyId"], s=env["signature"]: S.post(f"{A}/console/policies/verify", json={"policy": p, "keyId": k, "signature": s}).json()
        assert v(POLICY)["code"] == "COMPLIANT"
        assert v({**POLICY, "epoch": 901})["code"] == "INVALID_SIGNATURE"
        assert v(POLICY, k="nope-key")["code"] == "KEY_UNKNOWN"
        assert v({**POLICY, "extra": 1})["code"] == "INVALID_POLICY"
        env_err(S.post(f"{A}/console/policies/sign", json={"policy": {**POLICY, "extra": 1}}), "INVALID_POLICY", 400)

    def test_key_lifecycle_and_revoke(self):
        orig = active_key()
        new = S.post(f"{A}/console/keys").json()
        assert new["status"] == "rotating"
        r = S.post(f"{A}/console/keys/rotate", json={"oldKeyId": orig["key_id"], "newKeyId": new["key_id"]})
        assert r.status_code == 200, r.text
        tok1 = r.json()["fencingToken"]
        keys = {k["key_id"]: k for k in S.get(f"{A}/console/keys").json()}
        assert keys[orig["key_id"]]["status"] == "retired" and keys[new["key_id"]]["status"] == "active"
        env = S.post(f"{A}/console/policies/sign", json={"policy": POLICY}).json()
        assert env["keyId"] == new["key_id"]
        # rollback restores original
        r = S.post(f"{A}/console/keys/rollback", json={"oldKeyId": orig["key_id"], "newKeyId": new["key_id"]})
        assert r.status_code == 200, r.text
        keys = {k["key_id"]: k for k in S.get(f"{A}/console/keys").json()}
        assert keys[orig["key_id"]]["status"] == "active" and keys[new["key_id"]]["status"] == "retired"
        assert keys[orig["key_id"]]["fencing_token"] > tok1
        # retired key still verifies
        assert S.post(f"{A}/console/policies/verify", json=env).json()["code"] == "COMPLIANT"
        # revoke -> KEY_REVOKED
        assert S.post(f"{A}/console/keys/{new['key_id']}/revoke").json()["status"] == "revoked"
        assert S.post(f"{A}/console/policies/verify", json={"policy": env["policy"], "keyId": env["keyId"], "signature": env["signature"]}).json()["code"] == "KEY_REVOKED"
        env_err(S.post(f"{A}/console/keys/{new['key_id']}/revoke"), "INVALID_STATE", 409)
        env_err(S.post(f"{A}/console/keys/none-xyz/revoke"), "KEY_UNKNOWN", 404)

    def test_inject_failure_rotation(self):
        orig = active_key()
        new = S.post(f"{A}/console/keys").json()
        r = S.post(f"{A}/console/keys/rotate", json={"oldKeyId": orig["key_id"], "newKeyId": new["key_id"], "injectFailure": True})
        env_err(r, "ROTATION_FAILED", 500)
        recs = S.get(f"{A}/console/audit?event_type=KEY_ROTATION_COMPLETED&limit=500").json()["records"]
        assert not any(x["payload"].get("keyId") == new["key_id"] for x in recs)
        assert active_key()["key_id"] == orig["key_id"]
        S.post(f"{A}/console/keys/{new['key_id']}/revoke")
        # lock was released after failure
        locks = S.get(f"{A}/console/locks").json()["locks"]
        assert not any(l["name"] == f"rotation:{orig['key_id']}" and l["active"] for l in locks)


# ---- locks ----
class TestLocks:
    def test_lock_flow(self):
        n = f"TEST_lock_{uuid.uuid4().hex[:6]}"
        t1 = S.post(f"{A}/console/locks/acquire", json={"name": n, "owner": "node-a"}).json()["token"]
        env_err(S.post(f"{A}/console/locks/acquire", json={"name": n, "owner": "node-b"}), "ROTATION_IN_PROGRESS", 409)
        env_err(S.post(f"{A}/console/locks/renew", json={"name": n, "owner": "node-b"}), "LOCK_NOT_OWNER", 409)
        assert S.post(f"{A}/console/locks/renew", json={"name": n, "owner": "node-a", "token": t1}).json()["renewed"] is True
        S.post(f"{A}/console/locks/expire", json={"name": n})
        t2 = S.post(f"{A}/console/locks/acquire", json={"name": n, "owner": "node-b"}).json()["token"]
        assert t2 > t1
        env_err(S.post(f"{A}/console/locks/release", json={"name": n, "token": t1}), "LOCK_NOT_OWNER", 409)
        assert S.post(f"{A}/console/locks/release", json={"name": n, "token": t2}).json()["released"] is True


# ---- audit ----
class TestAudit:
    def test_verify_and_tamper(self):
        before = S.get(f"{A}/console/audit/verify").json()
        assert before["valid"] is True and before["code"] == "CHAIN_VALID"
        codes = {}
        for m in ["payload", "link", "hash", "gap"]:
            codes[m] = S.post(f"{A}/console/audit/simulate-tamper", json={"mutation": m, "index": 2}).json()["result"]["code"]
        assert codes["payload"] in ("HASH_MISMATCH", "FIELD_MISMATCH") and codes["link"] == "BROKEN_LINKAGE" and codes["gap"] == "SEQUENCE_GAP"
        assert codes["hash"] in ("HASH_MISMATCH", "BROKEN_LINKAGE")
        assert S.get(f"{A}/console/audit/verify").json()["code"] == "CHAIN_VALID"
        assert S.post(f"{A}/console/audit/simulate-tamper", json={"mutation": "bogus"}).status_code in (400, 422)


# ---- outbox ----
class TestOutbox:
    def test_fail_deadletter_replay_drain(self):
        try:
            S.put(f"{A}/console/settings", json={"busMode": "fail"})
            S.post(f"{A}/console/locks/expire", json={"name": "noop"})  # creates an audit? no; ensure at least one pending via key create
            S.post(f"{A}/console/policies/canonicalize", json={"policy": POLICY})
            put_validator(validatorVersion="6.4.1", channel="stable")  # appends audit -> outbox event
            r = S.post(f"{A}/console/outbox/publish", json={}).json()
            assert r["results"].get("retry", 0) >= 1, r
            for _ in range(7):
                S.post(f"{A}/console/outbox/expire-claims")
                S.post(f"{A}/console/outbox/publish", json={})
            st = S.get(f"{A}/console/outbox").json()
            assert st["deadCount"] >= 1, st["deadCount"]
            rep = S.post(f"{A}/console/outbox/replay", json={"all": True}).json()
            assert rep["replayed"] >= 1, rep
            assert S.get(f"{A}/console/outbox").json()["deadCount"] == 0
        finally:
            S.put(f"{A}/console/settings", json={"clearBusMode": True})
        for _ in range(3):
            S.post(f"{A}/console/outbox/expire-claims")
            S.post(f"{A}/console/outbox/publish", json={})
        st = S.get(f"{A}/console/outbox").json()
        assert st["pendingCount"] == 0 and st["deadCount"] == 0
        assert st["consumerProcessed"] <= st["busDeliveries"]
        env_err(S.post(f"{A}/console/outbox/replay", json={"eventId": "nope"}), "DEAD_LETTER_NOT_FOUND", 404)
        env_err(S.put(f"{A}/console/settings", json={"busMode": "weird"}), "INVALID_STATE", 409)


# ---- rollbacks ----
RB = {"level": "L3", "reason": "TEST_ regression in consensus", "ticketId": "TCK-1", "incidentId": "INC-1", "targetVersion": "6.2.0", "requester": "platform-lead"}


class TestRollbacks:
    def test_validation(self):
        env_err(S.post(f"{A}/console/rollbacks", json={**RB, "level": "L5"}), "INVALID_LEVEL", 400)
        env_err(S.post(f"{A}/console/rollbacks", json={**RB, "targetVersion": "9.9.9"}), "TARGET_VERSION_UNKNOWN", 404)
        env_err(S.post(f"{A}/console/rollbacks", json={**RB, "ticketId": ""}), "INVALID_TICKET", 400)

    def test_l3_flow(self):
        try:
            rb = S.post(f"{A}/console/rollbacks", json=RB).json()
            rid = rb["rollback_id"]
            env_err(S.post(f"{A}/console/rollbacks/{rid}/approve", json={"actor": "platform-lead"}), "SELF_APPROVAL_FORBIDDEN", 403)
            S.post(f"{A}/console/rollbacks/{rid}/approve", json={"actor": "sec-1"})
            env_err(S.post(f"{A}/console/rollbacks/{rid}/approve", json={"actor": "sec-1"}), "DUPLICATE_APPROVAL", 409)
            env_err(S.post(f"{A}/console/rollbacks/{rid}/execute"), "INSUFFICIENT_APPROVALS", 403)
            S.post(f"{A}/console/rollbacks/{rid}/approve", json={"actor": "sec-2"})
            assert S.post(f"{A}/console/rollbacks/{rid}/execute").json()["status"] == "executed"
            assert S.get(f"{A}/v1/validator/version").json()["validatorVersion"] == "6.2.0"
            assert S.post(f"{A}/console/rollbacks/{rid}/verify").json()["status"] == "verified"
        finally:
            put_validator(validatorVersion="6.4.1", channel="stable")
            clear_settings()


# ---- SLO window (iteration_2 fix: contention no longer breaks availability; reset-window recovers readiness) ----
def readiness_check(rd, name):
    return next(c for c in rd["checks"] if c["name"] == name)


class TestSloWindow:
    def test_contention_window_and_reset(self):
        S.post(f"{A}/console/metrics/reset-window")
        n = f"TEST_lock_{uuid.uuid4().hex[:6]}"
        t1 = S.post(f"{A}/console/locks/acquire", json={"name": n, "owner": "node-a"}).json()["token"]
        env_err(S.post(f"{A}/console/locks/acquire", json={"name": n, "owner": "node-b"}), "ROTATION_IN_PROGRESS", 409)
        m = S.get(f"{A}/console/metrics").json()
        assert m["window5m"]["denied"] == 1 and m["window5m"]["acquired"] == 1, m["window5m"]
        assert m["burnRate"] == 0 and m["lockAvailability"] == 1.0 and m["burnLevel"] == "healthy"
        assert m["contentionRate"] == 0.5
        rd = S.get(f"{A}/console/readiness").json()
        assert readiness_check(rd, "Lock contention < 1%")["passed"] is False
        assert readiness_check(rd, "Burn rate healthy")["passed"] is True
        assert rd["decision"] == "NO-GO"
        S.post(f"{A}/console/locks/release", json={"name": n, "token": t1})
        r = S.post(f"{A}/console/metrics/reset-window")
        assert r.status_code == 200 and r.json()["samplesCleared"] >= 2, r.text
        m = S.get(f"{A}/console/metrics").json()
        assert m["window5m"] == {"acquired": 0, "denied": 0, "error": 0} and m["contentionRate"] == 0
        rd = S.get(f"{A}/console/readiness").json()
        assert readiness_check(rd, "Lock contention < 1%")["passed"] is True
        recs = S.get(f"{A}/console/audit?event_type=SLO_WINDOW_RESET&limit=500").json()["records"]
        assert len(recs) >= 1


class TestRevokeGuard:
    def test_revoke_active_blocked(self):
        k = active_key()
        env_err(S.post(f"{A}/console/keys/{k['key_id']}/revoke"), "INVALID_STATE", 409)
        assert active_key()["key_id"] == k["key_id"]

    def test_revoke_nonactive_ok_and_safe_mode_blocks(self):
        new = S.post(f"{A}/console/keys").json()
        try:
            S.put(f"{A}/console/settings", json={"safeMode": True})
            env_err(S.post(f"{A}/console/keys/{new['key_id']}/revoke"), "SAFE_MODE", 403)
        finally:
            clear_settings()
        r = S.post(f"{A}/console/keys/{new['key_id']}/revoke")
        assert r.status_code == 200 and r.json()["status"] == "revoked", r.text


# ---- test runner + readiness ----
class TestRunnerReadiness:
    def test_readiness_nogo_when_old(self):
        try:
            put_validator(validatorVersion="6.1.0")
            assert S.get(f"{A}/console/readiness").json()["decision"] == "NO-GO"
        finally:
            put_validator(validatorVersion="6.4.1", channel="stable")

    def test_run_tests_and_go(self):
        r = S.post(f"{A}/console/tests/run", json={}, timeout=180).json()
        assert r["total"] == 72 and r["passed"] == 72, [x for x in r["results"] if x["status"] != "passed"]
        for _ in range(3):
            S.post(f"{A}/console/outbox/expire-claims")
            S.post(f"{A}/console/outbox/publish", json={})
        S.post(f"{A}/console/metrics/reset-window")  # clear contention from TestLocks / TestSloWindow
        rd = S.get(f"{A}/console/readiness").json()
        assert rd["decision"] == "GO", [c for c in rd["checks"] if not c["passed"]]
