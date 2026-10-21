"""Frasberg ops iteration: fleet trends, archive schedule, alert quiet hours/escalation/ack/digest, upgrade nudges, slack oauth start."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
A = f"{BASE_URL}/api"
S = requests.Session()


def _env(path):
    out = {}
    for line in open(path):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            out[k] = v.strip('"')
    return out


BENV = _env("/app/backend/.env")
DB = MongoClient(BENV["MONGO_URL"])[BENV["DB_NAME"]]


def env_err(r, code, status=None):
    if status:
        assert r.status_code == status, r.text
    d = r.json()
    assert d["success"] is False and d["error"]["code"] == code, d


# ---- fleet trends ----
class TestTrends:
    @pytest.mark.parametrize("rng", ["1h", "24h", "7d"])
    def test_ranges(self, rng):
        r = S.get(f"{A}/console/fleet/trends", params={"range": rng})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["range"] == rng and len(d["validators"]) >= 9
        for m in ("uptime", "voteParticipation", "missedBlocks"):
            s = d["series"][m]
            assert len(s) > 3 and all("t" in p for p in s)
            assert "fras_val_008" in s[-1]
            assert [p["t"] for p in s] == sorted(p["t"] for p in s)
        dv = {x["validatorId"]: x for x in d["drift"]}
        for m in ("uptime", "voteParticipation", "missedBlocks"):
            assert {"change", "drifting", "breach"} <= set(dv["fras_val_008"]["metrics"][m])
        assert dv["fras_val_008"]["breach"] is True
        if rng == "7d":  # 24h change is below drift thresholds by design (profile spreads over 7d)
            assert dv["fras_val_008"]["drifting"] is True

    def test_invalid_range(self):
        env_err(S.get(f"{A}/console/fleet/trends", params={"range": "30d"}), "INVALID_STATE", 409)


# ---- archive schedule ----
class TestArchiveSchedule:
    def test_get_put_run(self):
        orig = S.get(f"{A}/console/audit/archive-schedule").json()
        assert {"enabled", "timeUtc", "olderThanMinutes", "nextRunAt", "history"} <= set(orig)
        try:
            r = S.put(f"{A}/console/audit/archive-schedule", json={"timeUtc": "23:58", "olderThanMinutes": 0, "enabled": True})
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["timeUtc"] == "23:58" and d["nextRunAt"]
            assert S.get(f"{A}/console/audit/archive-schedule").json()["timeUtc"] == "23:58"
            run = S.post(f"{A}/console/audit/archive-schedule/run").json()
            assert run["status"] == "success" and run["archiveId"] and run["count"] >= 1 and run["trigger"] == "manual"
            hist = S.get(f"{A}/console/audit/archive-schedule").json()["history"]
            assert hist[0]["id"] == run["id"]
            # nothing older than a year -> skipped
            S.put(f"{A}/console/audit/archive-schedule", json={"olderThanMinutes": 525600})
            sk = S.post(f"{A}/console/audit/archive-schedule/run").json()
            assert sk["status"] == "skipped", sk
            paused = S.put(f"{A}/console/audit/archive-schedule", json={"enabled": False}).json()
            assert paused["enabled"] is False and paused["nextRunAt"] is None
        finally:
            S.put(f"{A}/console/audit/archive-schedule", json={k: orig[k] for k in ("enabled", "timeUtc", "olderThanMinutes")})

    @pytest.mark.parametrize("bad", [{"timeUtc": "25:00"}, {"timeUtc": "7:5"}, {"olderThanMinutes": -1}])
    def test_invalid(self, bad):
        r = S.put(f"{A}/console/audit/archive-schedule", json=bad)
        assert r.status_code in (400, 422), r.text


# ---- alerts quiet hours / ack / digest ----
def hhmm(t):
    return t.strftime("%H:%M")


@pytest.fixture
def alert_cfg():
    orig = S.get(f"{A}/console/alerts").json()["config"]
    S.put(f"{A}/console/alerts/config", json={"autoDispatch": False})
    S.post(f"{A}/console/metrics/reset-window")
    S.post(f"{A}/console/alerts/dispatch")
    yield
    S.post(f"{A}/console/metrics/reset-window")
    DB.lock_samples.delete_many({"TEST": True})
    S.put(f"{A}/console/alerts/config", json={"quietEnabled": False})
    S.post(f"{A}/console/alerts/dispatch")
    S.put(f"{A}/console/alerts/config", json={k: orig[k] for k in ("quietStart", "quietEnd", "quietMode", "escalationMinutes", "autoDispatch")})


def quiet_now(mode):
    t = datetime.now(timezone.utc)
    return S.put(f"{A}/console/alerts/config", json={"quietEnabled": True, "quietStart": hhmm(t - timedelta(hours=1)),
                                                     "quietEnd": hhmm(t + timedelta(hours=1)), "quietMode": mode})


def contention():
    name = f"TEST_lock_{uuid.uuid4().hex[:6]}"
    tok = S.post(f"{A}/console/locks/acquire", json={"name": name, "owner": "node-a"}).json()["token"]
    S.post(f"{A}/console/locks/acquire", json={"name": name, "owner": "node-b"})
    S.post(f"{A}/console/locks/release", json={"name": name, "token": tok})


def pick(d, alert, status=None):
    return [x for x in d["notifications"] if x["alert"] == alert and (status is None or x["status"] == status)]


class TestQuietHours:
    def test_config_fields(self, alert_cfg):
        r = S.put(f"{A}/console/alerts/config", json={"quietEnabled": False, "quietStart": "21:30", "quietEnd": "06:15", "quietMode": "suppress", "escalationMinutes": 7})
        assert r.status_code == 200, r.text
        g = S.get(f"{A}/console/alerts").json()
        c = g["config"]
        assert (c["quietStart"], c["quietEnd"], c["quietMode"], c["escalationMinutes"]) == ("21:30", "06:15", "suppress", 7)
        assert g["quietActive"] is False and isinstance(g["digestPending"], int)
        assert {"acknowledged", "escalations", "notified"} <= set(g["alerts"][0])
        for bad in ({"quietMode": "loud"}, {"quietStart": "24:00"}, {"escalationMinutes": -1}):
            assert S.put(f"{A}/console/alerts/config", json=bad).status_code in (400, 422)

    def test_ack_not_firing(self):
        env_err(S.post(f"{A}/console/alerts/LockContentionRateHigh/ack"), "INVALID_STATE", 409)

    def test_digest_flow(self, alert_cfg):
        assert quiet_now("digest").json()["quietEnabled"] is True
        assert S.get(f"{A}/console/alerts").json()["quietActive"] is True
        contention()
        d1 = S.post(f"{A}/console/alerts/dispatch").json()
        f = pick(d1, "LockContentionRateHigh", "firing")
        assert f and f[0]["result"] == "queued", d1
        g = S.get(f"{A}/console/alerts").json()
        assert g["digestPending"] >= 1
        row = next(a for a in g["alerts"] if a["name"] == "LockContentionRateHigh")
        assert row["firing"] and row["notified"] is True
        # resolve during quiet -> queued resolved
        S.post(f"{A}/console/metrics/reset-window")
        d2 = S.post(f"{A}/console/alerts/dispatch").json()
        r = pick(d2, "LockContentionRateHigh", "resolved")
        assert r and r[0]["result"] == "queued", d2
        # end quiet -> digest delivered on next dispatch
        S.put(f"{A}/console/alerts/config", json={"quietEnabled": False})
        d3 = S.post(f"{A}/console/alerts/dispatch").json()
        dg = pick(d3, "QuietHoursDigest", "digest")
        assert dg and dg[0]["result"] == "dry_run" and "2" in dg[0]["summary"], d3
        assert S.get(f"{A}/console/alerts").json()["digestPending"] == 0

    def test_manual_flush(self, alert_cfg):
        quiet_now("digest")
        contention()
        S.post(f"{A}/console/alerts/dispatch")
        fl = S.post(f"{A}/console/alerts/digest/flush").json()
        assert fl["notification"] and fl["notification"]["status"] == "digest"
        assert S.post(f"{A}/console/alerts/digest/flush").json()["notification"] is None

    def test_suppress_flow(self, alert_cfg):
        quiet_now("suppress")
        contention()
        d1 = S.post(f"{A}/console/alerts/dispatch").json()
        f = pick(d1, "LockContentionRateHigh", "firing")
        assert f and f[0]["result"] == "suppressed", d1
        row = next(a for a in S.get(f"{A}/console/alerts").json()["alerts"] if a["name"] == "LockContentionRateHigh")
        assert row["notified"] is False
        S.post(f"{A}/console/metrics/reset-window")
        d2 = S.post(f"{A}/console/alerts/dispatch").json()
        assert not pick(d2, "LockContentionRateHigh"), d2  # no resolved for un-notified firing

    def test_critical_immediate_and_ack(self, alert_cfg):
        quiet_now("suppress")
        S.put(f"{A}/console/alerts/config", json={"escalationMinutes": 1})
        DB.lock_samples.insert_many([{"ts": datetime.now(timezone.utc), "result": "error", "TEST": True} for _ in range(3)])
        d1 = S.post(f"{A}/console/alerts/dispatch").json()
        f = pick(d1, "LockAvailabilityLow", "firing")
        assert f and f[0]["result"] == "dry_run" and f[0]["channel"] == "pagerduty", d1
        row = next(a for a in S.get(f"{A}/console/alerts").json()["alerts"] if a["name"] == "LockAvailabilityLow")
        assert row["notified"] is True and row["acknowledged"] is False
        # escalation: backdate lastPagedAt
        DB.alert_state.update_one({"_id": "LockAvailabilityLow"}, {"$set": {"lastPagedAt": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")}})
        d2 = S.post(f"{A}/console/alerts/dispatch").json()
        e = pick(d2, "LockAvailabilityLow", "escalation")
        assert e, d2
        row = next(a for a in S.get(f"{A}/console/alerts").json()["alerts"] if a["name"] == "LockAvailabilityLow")
        assert row["escalations"] >= 1
        ack = S.post(f"{A}/console/alerts/LockAvailabilityLow/ack")
        assert ack.status_code == 200 and ack.json()["acknowledged"] == "LockAvailabilityLow"
        DB.alert_state.update_one({"_id": "LockAvailabilityLow"}, {"$set": {"lastPagedAt": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")}})
        d3 = S.post(f"{A}/console/alerts/dispatch").json()
        assert not pick(d3, "LockAvailabilityLow", "escalation"), d3
        DB.lock_samples.delete_many({"TEST": True})
        S.post(f"{A}/console/metrics/reset-window")
        d4 = S.post(f"{A}/console/alerts/dispatch").json()
        res = pick(d4, "LockAvailabilityLow", "resolved")
        assert res and res[0]["result"] == "dry_run", d4


# ---- upgrade nudges ----
class TestNudges:
    def test_nudge_dedup_resend(self):
        g = S.get(f"{A}/console/upgrades/nudges").json()
        assert g["slackConfigured"] is False and isinstance(g["nudges"], list)
        S.post(f"{A}/console/upgrades/1000/nudge", json={"resend": False})
        before = {n["validatorId"]: n["count"] for n in S.get(f"{A}/console/upgrades/nudges").json()["nudges"] if n["epoch"] == 1000}
        assert {"fras_val_003", "fras_val_004", "fras_val_005", "fras_val_006"} <= set(before)  # 008 meets 6.4.0/4.2.0 for epoch 1000
        assert "fras_val_001" not in before
        again = S.post(f"{A}/console/upgrades/1000/nudge", json={"resend": False}).json()
        assert again["sent"] == []
        rs = S.post(f"{A}/console/upgrades/1000/nudge", json={"resend": True}).json()["sent"]
        assert {x["validatorId"] for x in rs} == set(before) and all(x["result"] == "dry_run" for x in rs)
        after = {n["validatorId"]: n["count"] for n in S.get(f"{A}/console/upgrades/nudges").json()["nudges"] if n["epoch"] == 1000}
        assert all(after[k] == before[k] + 1 for k in before)

    def test_nudge_unknown_epoch(self):
        env_err(S.post(f"{A}/console/upgrades/123456/nudge", json={}), "INVALID_STATE", 409)


# ---- slack oauth start ----
class TestSlackOAuth:
    def test_start(self):
        d = S.get(f"{A}/console/slack/oauth/start", params={"origin": BASE_URL}).json()
        u = urlparse(d["url"])
        q = parse_qs(u.query)
        assert u.netloc == "slack.com" and u.path == "/oauth/v2/authorize"
        assert q["client_id"][0] == BENV["SLACK_CLIENT_ID"] and q["scope"][0] == "incoming-webhook" and q["state"][0]
        assert q["redirect_uri"][0] == f"{BASE_URL}/api/console/slack/oauth/callback"

    def test_bad_origin_and_state(self):
        env_err(S.get(f"{A}/console/slack/oauth/start", params={"origin": "http://evil.com"}), "INVALID_STATE", 409)
        env_err(S.get(f"{A}/console/slack/oauth/callback", params={"state": "nope", "code": "x"}, allow_redirects=False), "INVALID_STATE", 409)

    def test_callback_error_redirect(self):
        d = S.get(f"{A}/console/slack/oauth/start", params={"origin": BASE_URL}).json()
        st = parse_qs(urlparse(d["url"]).query)["state"][0]
        r = S.get(f"{A}/console/slack/oauth/callback", params={"state": st, "error": "access_denied"}, allow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == f"{BASE_URL}/alerts?slack=error&reason=access_denied"
