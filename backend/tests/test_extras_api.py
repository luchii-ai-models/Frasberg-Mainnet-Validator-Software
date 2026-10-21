"""Frasberg extras regression: fleet view, audit archive export/verify, alert delivery (dry_run), upgrade countdown."""
import hashlib
import json
import os
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


def fleet():
    return {v["validatorId"]: v for v in S.get(f"{A}/console/fleet").json()["validators"]}


SEED_005 = {"validatorId": "fras_val_005", "name": "Ferrous", "operator": "Ferrous Ops", "region": "eu-west", "validatorVersion": "6.1.3",
            "luchiiVersion": "4.0.0", "channel": "stable", "epoch": 900, "commission": 5, "stake": 260000, "uptime": 99.95,
            "voteParticipation": 97.0, "missedBlocks": 1.0, "apiAvailability": 99.9, "securityScanPassed": True, "identityVerified": True,
            "governanceStanding": True}


# ---- fleet ----
class TestFleet:
    def test_seed_list(self):
        d = S.get(f"{A}/console/fleet").json()
        f = {v["validatorId"]: v for v in d["validators"]}
        for i in range(1, 10):
            assert f"fras_val_{i:03d}" in f
        assert f["fras_val_001"]["self"] is True and f["fras_val_001"]["delegationEligible"] is True
        assert f["fras_val_005"]["code"] == "VERSION_TOO_OLD" and not f["fras_val_005"]["delegationEligible"]
        assert f["fras_val_006"]["code"] == "INVALID_CHANNEL"
        assert "maxCommission" in f["fras_val_007"]["failedCriteria"]
        assert f["fras_val_008"]["status"] == "unhealthy" and "minStake" in f["fras_val_008"]["failedCriteria"]
        assert "identityCompliance" in f["fras_val_009"]["failedCriteria"]
        for v in f.values():
            assert {"code", "status", "versionClass", "delegationEligible", "failedCriteria", "nextRequirement"} <= set(v)
            assert v["nextRequirement"]["epoch"] == 1000 and isinstance(v["nextRequirement"]["ready"], bool)
        assert d["summary"]["total"] >= 9 and "_id" not in json.dumps(d)

    def test_create_validation_and_delete(self):
        vid = f"test_{uuid.uuid4().hex[:6]}"
        body = {"validatorId": vid, "name": "TEST node", "validatorVersion": "6.4.1", "luchiiVersion": "4.2.0", "stake": 200000}
        r = S.post(f"{A}/console/fleet", json=body)
        assert r.status_code == 200, r.text
        assert fleet()[vid]["delegationEligible"] is True
        env_err(S.post(f"{A}/console/fleet", json=body), "INVALID_STATE", 409)
        env_err(S.post(f"{A}/console/fleet", json={**body, "validatorId": "fras_val_001"}), "INVALID_STATE", 409)
        env_err(S.post(f"{A}/console/fleet", json={**body, "validatorId": vid + "x", "validatorVersion": "6.4"}), "INVALID_VERSION", 400)
        assert S.post(f"{A}/console/fleet", json={**body, "validatorId": "BAD-ID!"}).status_code == 422
        assert S.delete(f"{A}/console/fleet/{vid}").status_code == 200
        assert vid not in fleet()
        env_err(S.delete(f"{A}/console/fleet/{vid}"), "INVALID_STATE", 409)

    def test_edit_005_and_restore(self):
        try:
            r = S.put(f"{A}/console/fleet/fras_val_005", json={**SEED_005, "validatorVersion": "6.4.1", "luchiiVersion": "4.2.0"})
            assert r.status_code == 200, r.text
            v = fleet()["fras_val_005"]
            assert v["delegationEligible"] is True and v["code"] == "COMPLIANT" and v["nextRequirement"]["ready"] is True
        finally:
            S.put(f"{A}/console/fleet/fras_val_005", json=SEED_005)
        assert fleet()["fras_val_005"]["code"] == "VERSION_TOO_OLD"

    def test_self_not_editable(self):
        body = {**SEED_005, "validatorId": "fras_val_001"}
        env_err(S.put(f"{A}/console/fleet/fras_val_001", json=body), "INVALID_STATE", 409)
        env_err(S.delete(f"{A}/console/fleet/fras_val_001"), "INVALID_STATE", 409)
        assert S.get(f"{A}/v1/validator/version").json()["validatorVersion"] == "6.4.1"


# ---- audit archive ----
class TestArchive:
    @pytest.fixture(scope="class")
    def archive(self):
        before = S.get(f"{A}/console/audit/archives").json()
        r = S.post(f"{A}/console/audit/archives", json={})
        assert r.status_code == 200, r.text
        return before, r.json()

    def test_create_contiguous(self, archive):
        before, a = archive
        assert a["from_sequence"] == before["nextSequence"] and a["count"] == a["to_sequence"] - a["from_sequence"] + 1
        after = S.get(f"{A}/console/audit/archives").json()
        assert after["nextSequence"] == a["to_sequence"] + 1
        assert any(x["archive_id"] == "audit_log_000001_000020" for x in after["archives"])
        # live records never deleted
        assert after["totalRecords"] >= before["totalRecords"]
        assert after["totalRecords"] >= a["to_sequence"]

    def test_download_checksum(self, archive):
        _, a = archive
        r = S.get(f"{A}/console/audit/archives/{a['archive_id']}/download")
        assert r.status_code == 200 and "ndjson" in r.headers["content-type"]
        assert r.headers["X-Checksum-SHA256"] == a["sha256"] == hashlib.sha256(r.content).hexdigest()
        lines = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        assert len(lines) == a["count"] and lines[0]["sequence"] == a["from_sequence"]
        c = S.get(f"{A}/console/audit/archives/{a['archive_id']}/checksum").text
        assert c.strip() == f"{a['sha256']}  {a['archive_id']}.ndjson"

    def test_verify(self, archive):
        _, a = archive
        v = S.post(f"{A}/console/audit/archives/{a['archive_id']}/verify").json()
        assert v["valid"] is True and v["code"] == "CHAIN_VALID" and v["checksumMatches"] and v["matchesLiveLog"]
        v1 = S.post(f"{A}/console/audit/archives/audit_log_000001_000020/verify").json()
        assert v1["valid"] is True

    def test_verify_upload(self, archive):
        _, a = archive
        content = S.get(f"{A}/console/audit/archives/{a['archive_id']}/download").text
        ok = S.post(f"{A}/console/audit/archives/verify-upload", json={"content": content, "sha256": a["sha256"]}).json()
        assert ok["valid"] is True and ok["knownArchive"] == a["archive_id"]
        recs = [json.loads(x) for x in content.splitlines() if x.strip()]
        recs[0]["actor"] = "mallory"
        tampered = "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in recs) + "\n"
        bad = S.post(f"{A}/console/audit/archives/verify-upload", json={"content": tampered}).json()
        assert bad["valid"] is False and bad["code"] in ("HASH_MISMATCH", "MANIFEST_MISMATCH", "FIELD_MISMATCH"), bad
        cs = S.post(f"{A}/console/audit/archives/verify-upload", json={"content": content, "sha256": "0" * 64}).json()
        assert cs["valid"] is False and cs["code"] == "CHECKSUM_MISMATCH"

    def test_verify_upload_garbage(self):
        r = S.post(f"{A}/console/audit/archives/verify-upload", json={"content": "not json\n"})
        assert r.status_code == 200 and r.json()["code"] == "INVALID_NDJSON"
        r = S.post(f"{A}/console/audit/archives/verify-upload", json={"content": '{"a":1}\n'})
        assert r.status_code < 500, f"malformed record -> {r.status_code}"

    def test_missing_archive(self):
        env_err(S.post(f"{A}/console/audit/archives/nope/verify"), "INVALID_STATE", 409)


# ---- alert delivery (dry_run only, no real destinations) ----
class TestAlerts:
    def test_list_routing(self):
        d = S.get(f"{A}/console/alerts").json()
        assert d["config"]["slackConfigured"] is False and d["config"]["pagerdutyConfigured"] is False
        for a in d["alerts"]:
            assert a["receiver"] == {"critical": "pagerduty-critical", "warning": "slack-platform"}[a["severity"]]
        assert "slackWebhookUrl" not in json.dumps(d["config"])

    def test_test_notifications(self):
        n = S.post(f"{A}/console/alerts/test").json()["notifications"]
        assert len(n) == 2 and all(x["result"] == "dry_run" and x["test"] for x in n)
        assert {x["channel"] for x in n} == {"pagerduty", "slack"}

    def test_dispatch_firing_dedup_resolved(self):
        S.post(f"{A}/console/metrics/reset-window")
        S.post(f"{A}/console/alerts/dispatch")
        name = f"TEST_lock_{uuid.uuid4().hex[:6]}"
        tok = S.post(f"{A}/console/locks/acquire", json={"name": name, "owner": "node-a"}).json()["token"]
        S.post(f"{A}/console/locks/acquire", json={"name": name, "owner": "node-b"})
        try:
            d1 = S.post(f"{A}/console/alerts/dispatch").json()
            fired = [x for x in d1["notifications"] if x["alert"] == "LockContentionRateHigh" and x["status"] == "firing"]
            if not fired:  # background auto-dispatch (60s) may have raced us
                log = S.get(f"{A}/console/alerts").json()["deliveries"]
                fired = [x for x in log if x["alert"] == "LockContentionRateHigh" and x["status"] == "firing"]
            assert fired and fired[0]["result"] == "dry_run" and fired[0]["receiver"] == "slack-platform"
            d2 = S.post(f"{A}/console/alerts/dispatch").json()
            assert not [x for x in d2["notifications"] if x["alert"] == "LockContentionRateHigh"]
        finally:
            S.post(f"{A}/console/locks/release", json={"name": name, "token": tok})
            S.post(f"{A}/console/metrics/reset-window")
        d3 = S.post(f"{A}/console/alerts/dispatch").json()
        res = [x for x in d3["notifications"] if x["alert"] == "LockContentionRateHigh" and x["status"] == "resolved"]
        if not res:
            log = S.get(f"{A}/console/alerts").json()["deliveries"]
            res = [x for x in log if x["alert"] == "LockContentionRateHigh" and x["status"] == "resolved"]
        assert res

    def test_bad_slack_url(self):
        env_err(S.put(f"{A}/console/alerts/config", json={"slackWebhookUrl": "https://evil.example.com/x"}), "INVALID_STATE", 409)
        assert S.get(f"{A}/console/alerts").json()["config"]["slackConfigured"] is False


# ---- upgrade countdown ----
class TestUpgrades:
    def test_requirements(self):
        d = S.get(f"{A}/console/upgrades").json()
        assert d["policy"] == {"announcementDays": 30, "upgradeWindowDays": 14, "graceEpochs": 1}
        rows = {r["epoch"]: r for r in d["requirements"]}
        assert {100, 1000} <= set(rows) and max(rows) == 1000
        r = rows[1000]
        assert r["currentPhase"] == "announcement" and r["secondsToEnforcement"] > 0
        assert [p["name"] for p in r["phases"]] == ["announcement", "upgrade_window", "grace"]
        assert r["validator"]["minimum"] == "6.4.0" and r["luchii"]["minimum"] == "4.2.0"
        assert r["selfReady"] is True and 0 <= r["fleetReady"] <= r["fleetTotal"] and r["fleetTotal"] >= 9
        assert r["enforceAt"] and r["graceEndsAt"] > r["enforceAt"]
