"""Iteration 5: per-validator Slack routing, drift alerts, nudge routing fields. Minimises real Slack sends."""
import os

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
A = f"{BASE_URL}/api"
S = requests.Session()
FAKE_HOOK = "https://hooks.slack.com/services/TTEST0000/BTEST0000/notarealtoken"


def _env(path):
    out = {}
    for line in open(path):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            out[k] = v.strip('"')
    return out


BENV = _env("/app/backend/.env")
DB = MongoClient(BENV["MONGO_URL"])[BENV["DB_NAME"]]


def routing():
    r = S.get(f"{A}/console/fleet/routing")
    assert r.status_code == 200
    return {x["validatorId"]: x for x in r.json()["routing"]}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    for vid in routing():
        S.put(f"{A}/console/fleet/{vid}/routing", json={"slackMemberId": "", "slackWebhookUrl": ""})


# ---- routing CRUD + validation ----
class TestRouting:
    def test_list(self):
        rows = routing()
        assert len(rows) >= 9 and "fras_val_003" in rows
        for r in rows.values():
            assert set(r) >= {"validatorId", "name", "slackMemberId", "hasWebhook"}
            assert isinstance(r["hasWebhook"], bool)

    @pytest.mark.parametrize("mid", ["u0123abcd", "X0123ABCD", "U12", "U0123ABCD-", "U" + "A" * 16])
    def test_invalid_member(self, mid):
        r = S.put(f"{A}/console/fleet/fras_val_004/routing", json={"slackMemberId": mid})
        assert r.status_code in (400, 422), r.text

    def test_invalid_webhook(self):
        r = S.put(f"{A}/console/fleet/fras_val_004/routing", json={"slackWebhookUrl": "https://evil.example.com/hook"})
        assert r.status_code in (400, 404, 409) and r.json()["success"] is False

    def test_unknown_vid(self):
        r = S.put(f"{A}/console/fleet/does_not_exist/routing", json={"slackMemberId": "U0123ABCD"})
        assert r.status_code in (400, 404, 409) and "not found" in r.json()["error"]["message"].lower()

    @pytest.mark.parametrize("mid", ["U0TEST1234", "W0TEST1234", "S0TEST1234"])
    def test_member_set_and_clear(self, mid):
        r = S.put(f"{A}/console/fleet/fras_val_004/routing", json={"slackMemberId": mid})
        assert r.status_code == 200
        assert routing()["fras_val_004"]["slackMemberId"] == mid
        r = S.put(f"{A}/console/fleet/fras_val_004/routing", json={"slackMemberId": ""})
        assert r.status_code == 200 and routing()["fras_val_004"]["slackMemberId"] is None

    def test_webhook_set_keeps_member_and_clear(self):
        S.put(f"{A}/console/fleet/fras_val_009/routing", json={"slackMemberId": "U0TEST9999"})
        r = S.put(f"{A}/console/fleet/fras_val_009/routing", json={"slackWebhookUrl": FAKE_HOOK})
        assert r.status_code == 200
        row = routing()["fras_val_009"]
        assert row["hasWebhook"] is True and row["slackMemberId"] == "U0TEST9999"
        assert "slackWebhookUrl" not in row  # secret not leaked

    def test_routing_test_own_channel_fake_hook(self):
        # fake own-channel webhook -> nothing reaches the user's real Slack
        r = S.post(f"{A}/console/fleet/fras_val_009/routing/test")
        assert r.status_code == 200
        d = r.json()
        assert d["target"] == "own channel" and d["mention"] == "<@U0TEST9999>"
        assert d["result"] == "failed" and d["detail"].startswith("HTTP 4")

    def test_clear_webhook(self):
        r = S.put(f"{A}/console/fleet/fras_val_009/routing", json={"slackWebhookUrl": ""})
        assert r.status_code == 200
        row = routing()["fras_val_009"]
        assert row["hasWebhook"] is False and row["slackMemberId"] == "U0TEST9999"
        S.put(f"{A}/console/fleet/fras_val_009/routing", json={"slackMemberId": ""})
        assert routing()["fras_val_009"]["slackMemberId"] is None


# ---- drift alerts ----
class TestDrift:
    def test_alert_rows(self):
        d = S.get(f"{A}/console/alerts").json()
        al = {a["name"]: a for a in d["alerts"]}
        a3, a8 = al["ValidatorDrift_fras_val_003"], al["ValidatorDrift_fras_val_008"]
        assert a3["severity"] == "warning" and a3["firing"] and a3["kind"] == "drift" and "votes" in a3["value"]
        assert a8["severity"] == "critical" and a8["firing"] and a8["kind"] == "breach" and "uptime" in a8["value"]
        assert a3["receiver"] == "slack-platform" and a8["receiver"] == "pagerduty-critical"
        drift_rows = [n for n in al if n.startswith("ValidatorDrift_")]
        assert all(al[n]["firing"] or al[n].get("since") for n in drift_rows)
        assert "ValidatorDrift_fras_val_007" not in al  # stable validator hidden

    def test_delivery_003_delivered(self):
        d = S.get(f"{A}/console/alerts").json()
        x = next(x for x in d["deliveries"] if x["alert"] == "ValidatorDrift_fras_val_003")
        assert x["result"] == "delivered" and x["detail"] == "HTTP 200"

    def test_critical_re_dispatch_has_pd_and_slack_detail(self):
        # reset 008 state so dispatch re-fires it once (ONE real Slack message)
        DB.alert_state.delete_one({"_id": "ValidatorDrift_fras_val_008"})
        r = S.post(f"{A}/console/alerts/dispatch")
        assert r.status_code == 200
        n = [x for x in r.json()["notifications"] if x["alert"] == "ValidatorDrift_fras_val_008"]
        assert n, r.json()
        assert n[0]["channel"] == "pagerduty" and n[0]["detail"].startswith("pagerduty dry_run · slack HTTP")
        assert n[0]["result"] == "delivered"


# ---- nudges (ONE call, ~4 real Slack messages) ----
class TestNudge:
    def test_nudge_resend_fields(self):
        S.put(f"{A}/console/fleet/fras_val_005/routing", json={"slackMemberId": "U0TEST0005", "slackWebhookUrl": FAKE_HOOK})
        r = S.post(f"{A}/console/upgrades/1000/nudge", json={"resend": True})
        assert r.status_code == 200, r.text
        sent = r.json()["sent"]
        assert sent
        by = {s["validatorId"]: s for s in sent}
        for s in sent:
            assert s["target"] in ("main channel", "own channel") and "mention" in s
        if "fras_val_005" in by:
            assert by["fras_val_005"]["target"] == "own channel" and by["fras_val_005"]["mention"] == "<@U0TEST0005>"
            assert by["fras_val_005"]["result"] == "failed"
        others = [s for s in sent if s["validatorId"] != "fras_val_005"]
        assert all(s["target"] == "main channel" and s["mention"] is None for s in others)
        S.put(f"{A}/console/fleet/fras_val_005/routing", json={"slackMemberId": "", "slackWebhookUrl": ""})
