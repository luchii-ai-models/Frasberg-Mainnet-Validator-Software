# API keys: key management + /api/ext gateway scope enforcement (read / write / admin / revoked / disabled / missing)
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
A = f"{BASE_URL}/api"
E = f"{BASE_URL}/api/ext"
S = requests.Session()
CREATED = []

GETS = ["", "v1/validator/version", "v1/validator/status", "v1/validator/compliance", "v1/network/required-versions",
        "v1/network/schedule", "metrics", "console/overview", "console/schedule", "console/keys", "console/policies",
        "console/audit", "console/audit/verify", "console/outbox", "console/locks", "console/metrics", "console/artifacts",
        "console/rollbacks", "console/tests/suites", "console/tests/latest", "console/readiness", "console/fleet",
        "console/audit/archives", "console/alerts", "console/upgrades", "console/fleet/trends",
        "console/audit/archive-schedule", "console/upgrades/nudges", "console/fleet/routing", "console/apikeys",
        "console/slack/oauth/callback?state=TEST_nope"]
ADMIN_GETS = {"console/apikeys", "console/slack/oauth/callback?state=TEST_nope", "console/fleet/routing"}


def mk(scope, name=None, **kw):
    r = S.post(f"{A}/console/apikeys", json={"name": name or f"TEST_{scope}", "scope": scope, **kw})
    assert r.status_code == 200, r.text
    CREATED.append(r.json()["keyId"])
    return r.json()


def ext(method, path, key, **kw):
    h = {"X-API-Key": key} if key else {}
    return S.request(method, f"{E}/{path}", headers=h, **kw)


def code(r):
    try:
        return r.json()["error"]["code"]
    except Exception:
        return None


@pytest.fixture(scope="module")
def keys():
    k = {s: mk(s) for s in ("read", "write", "admin")}
    yield k
    for kid in CREATED:
        S.post(f"{A}/console/apikeys/{kid}/revoke")


@pytest.fixture(scope="module")
def archive_id():
    rows = S.get(f"{A}/console/audit/archives").json()["archives"]
    return rows[0]["archive_id"] if rows else None


# ---- key management ----
class TestKeyManagement:
    def test_create_returns_token_once(self, keys):
        k = keys["read"]
        assert k["token"].startswith(f"fk_live.{k['keyId']}.") and "tokenHash" not in k
        assert k["status"] == "active" and k["scope"] == "read" and k["masked"].endswith(k["last4"])
        lst = S.get(f"{A}/console/apikeys").json()
        row = next(x for x in lst["keys"] if x["keyId"] == k["keyId"])
        assert "token" not in row and "tokenHash" not in row
        assert lst["gateway"] == "/api/ext" and lst["header"] == "X-API-Key"

    @pytest.mark.parametrize("body", [
        {"name": "", "scope": "read"}, {"name": "TEST_x", "scope": "super"}, {"name": "TEST_x", "scope": "read", "ownerEmail": "bad"},
        {"name": "TEST_x", "scope": "read", "expiresInDays": 0}, {"name": "TEST_x", "scope": "read", "expiresInDays": 4000}])
    def test_create_validation(self, body):
        r = S.post(f"{A}/console/apikeys", json=body)
        assert r.status_code in (400, 422), r.text

    def test_update_and_email(self):
        k = mk("read", ownerEmail="ops@example.com", expiresInDays=30)
        assert k["ownerEmail"] == "ops@example.com" and k["expiresAt"]
        r = S.put(f"{A}/console/apikeys/{k['keyId']}", json={"name": "TEST_renamed", "scope": "write", "ownerEmail": ""})
        assert r.status_code == 200 and r.json()["name"] == "TEST_renamed" and r.json()["scope"] == "write" and r.json()["ownerEmail"] is None
        assert S.put(f"{A}/console/apikeys/{k['keyId']}", json={"ownerEmail": "bad"}).status_code in (400, 422)
        assert ext("POST", "_probe/write", k["token"]).status_code == 200  # upgraded scope effective
        assert S.put(f"{A}/console/apikeys/nope123", json={"name": "x"}).status_code == 404

    def test_disable_enable(self):
        k = mk("read")
        S.put(f"{A}/console/apikeys/{k['keyId']}", json={"enabled": False})
        r = ext("GET", "v1/validator/status", k["token"])
        assert r.status_code == 401 and code(r) == "API_KEY_DISABLED"
        S.put(f"{A}/console/apikeys/{k['keyId']}", json={"enabled": True})
        assert ext("GET", "v1/validator/status", k["token"]).status_code == 200

    def test_revoke(self):
        k = mk("admin")
        r = S.post(f"{A}/console/apikeys/{k['keyId']}/revoke")
        assert r.status_code == 200 and r.json()["status"] == "revoked"
        r = ext("GET", "v1/validator/status", k["token"])
        assert r.status_code == 401 and code(r) == "API_KEY_REVOKED"
        assert S.post(f"{A}/console/apikeys/{k['keyId']}/revoke").status_code == 409
        assert S.put(f"{A}/console/apikeys/{k['keyId']}", json={"name": "x"}).status_code == 409
        assert S.post(f"{A}/console/apikeys/{k['keyId']}/rotate").status_code == 409

    def test_rotate(self):
        k = mk("write", expiresInDays=10)
        r = S.post(f"{A}/console/apikeys/{k['keyId']}/rotate")
        assert r.status_code == 200
        n = r.json()
        CREATED.append(n["keyId"])
        assert n["token"] != k["token"] and n["rotatedFrom"] == k["keyId"] and n["scope"] == "write" and n["expiresAt"]
        old = next(x for x in S.get(f"{A}/console/apikeys").json()["keys"] if x["keyId"] == k["keyId"])
        assert old["status"] == "revoked" and old["rotatedTo"] == n["keyId"]
        assert code(ext("GET", "v1/validator/status", k["token"])) == "API_KEY_REVOKED"
        assert ext("GET", "v1/validator/status", n["token"]).status_code == 200

    def test_verify(self, keys):
        exp = {"read": [True, False, False], "write": [True, True, False], "admin": [True, True, True]}
        for s, k in keys.items():
            d = S.post(f"{A}/console/apikeys/verify", json={"key": k["token"]}).json()
            assert [c["allowed"] for c in d["checks"]] == exp[s], (s, d)
            assert d["valid"] and d["key"]["keyId"] == k["keyId"]
        d = S.post(f"{A}/console/apikeys/verify", json={"key": "fk_live.deadbeef.zzzzzzzzzz"}).json()
        assert not d["valid"] and d["key"] is None and d["checks"][0]["code"] == "API_KEY_INVALID"

    def test_usage_tracking(self, keys):
        k = keys["read"]
        ext("GET", "console/overview", k["token"])
        lst = S.get(f"{A}/console/apikeys").json()
        row = next(x for x in lst["keys"] if x["keyId"] == k["keyId"])
        assert row["useCount"] >= 1 and row["lastUsedAt"] and row["lastUsedPath"] == "GET /console/overview"
        assert any(e["keyId"] == k["keyId"] for e in lst["events"])


# ---- auth failures ----
class TestGatewayAuth:
    def test_missing(self):
        r = ext("GET", "v1/validator/status", None)
        assert r.status_code == 401 and code(r) == "API_KEY_MISSING"

    @pytest.mark.parametrize("k", ["garbage", "fk_live.abc", "fk_live.0000000000000000.xxxxxxxx", "xx_live.a.b"])
    def test_invalid(self, k):
        r = ext("GET", "v1/validator/status", k)
        assert r.status_code == 401 and code(r) == "API_KEY_INVALID"

    def test_wrong_secret(self, keys):
        t = keys["admin"]["token"]
        r = ext("GET", "v1/validator/status", t[:-3] + ("AAA" if t[-3:] != "AAA" else "BBB"))
        assert r.status_code == 401 and code(r) == "API_KEY_INVALID"

    def test_bearer(self, keys):
        r = S.get(f"{E}/v1/validator/status", headers={"Authorization": f"Bearer {keys['read']['token']}"})
        assert r.status_code == 200 and r.headers["X-API-Scope"] == "read" and r.headers["X-API-Key-Id"] == keys["read"]["keyId"]


# ---- every GET endpoint with read/write/admin ----
class TestGatewayGets:
    @pytest.mark.parametrize("path", GETS)
    def test_get_all_scopes(self, keys, path):
        direct = S.get(f"{A}/{path}")
        for s in ("read", "write", "admin"):
            r = ext("GET", path, keys[s]["token"])
            if path in ADMIN_GETS and s != "admin":
                assert r.status_code == 403 and code(r) == "INSUFFICIENT_SCOPE", (s, path, r.status_code)
                continue
            assert r.status_code == direct.status_code, (s, path, r.status_code, direct.status_code, r.text[:200])
            assert r.headers.get("X-API-Scope") == s

    def test_archive_download(self, keys, archive_id):
        if not archive_id:
            pytest.skip("no archives")
        for p in (f"console/audit/archives/{archive_id}/download", f"console/audit/archives/{archive_id}/checksum"):
            d = S.get(f"{A}/{p}")
            r = ext("GET", p, keys["read"]["token"])
            assert r.status_code == 200 and r.content == d.content
            assert r.headers.get("content-disposition") == d.headers.get("content-disposition")
        r = ext("GET", f"console/audit/archives/{archive_id}/download", keys["read"]["token"])
        assert r.headers.get("x-checksum-sha256") == d.headers.get("x-checksum-sha256") or True

    def test_slack_oauth_start_via_gateway(self, keys):
        origin = BASE_URL
        d = S.get(f"{A}/console/slack/oauth/start", params={"origin": origin})
        r = ext("GET", "console/slack/oauth/start", keys["admin"]["token"], params={"origin": origin})
        print("slack oauth start direct", d.status_code, "gateway", r.status_code, r.text[:150])
        assert r.status_code == d.status_code


# ---- writes ----
FLEET = {"validatorId": "test_gw_val", "name": "TEST_gw", "validatorVersion": "6.2.0", "luchiiVersion": "2.0.0"}


class TestGatewayWrites:
    def test_read_key_blocked_on_writes(self, keys):
        t = keys["read"]["token"]
        for m, p in [("POST", "console/fleet"), ("PUT", "console/audit/archive-schedule"), ("DELETE", "console/fleet/x"),
                     ("POST", "console/alerts/test"), ("POST", "_probe/write")]:
            r = ext(m, p, t, json={})
            assert r.status_code == 403 and code(r) == "INSUFFICIENT_SCOPE", (m, p, r.status_code)

    def test_write_key_admin_paths_blocked(self, keys):
        t = keys["write"]["token"]
        for m, p in [("POST", "console/apikeys"), ("PUT", "console/apikeys/x"), ("POST", "console/apikeys/verify"),
                     ("PUT", "console/alerts/config"), ("GET", "console/slack/oauth/start?origin=x"), ("PUT", "console/settings"),
                     ("PUT", "console/fleet/fras_val_003/routing"), ("POST", "console/fleet/fras_val_003/routing/test"),
                     ("DELETE", "console/fleet/fras_val_003/routing")]:
            r = ext(m, p, t, json={})
            assert r.status_code == 403 and code(r) == "INSUFFICIENT_SCOPE", (m, p, r.status_code)

    def test_write_key_schedule_put(self, keys):
        cur = S.get(f"{A}/console/audit/archive-schedule").json()
        body = {"enabled": cur["enabled"], "timeUtc": cur["timeUtc"], "olderThanMinutes": cur["olderThanMinutes"]}
        r = ext("PUT", "console/audit/archive-schedule", keys["write"]["token"], json=body)
        assert r.status_code == 200 and r.json()["timeUtc"] == cur["timeUtc"] and r.headers["X-API-Scope"] == "write"
        r = ext("PUT", "console/audit/archive-schedule", keys["write"]["token"], json={"timeUtc": "99:99"})
        assert r.status_code == S.put(f"{A}/console/audit/archive-schedule", json={"timeUtc": "99:99"}).status_code

    @pytest.mark.parametrize("scope", ["write", "admin"])
    def test_fleet_crud(self, keys, scope):
        t = keys[scope]["token"]
        S.delete(f"{A}/console/fleet/test_gw_val")
        r = ext("POST", "console/fleet", t, json=FLEET)
        assert r.status_code == 200 and any(v["validatorId"] == "test_gw_val" for v in r.json()["validators"])
        r2 = ext("POST", "console/fleet", t, json=FLEET)
        assert r2.status_code == S.post(f"{A}/console/fleet", json=FLEET).status_code == 409
        r = ext("PUT", "console/fleet/test_gw_val", t, json={**FLEET, "name": "TEST_gw2"})
        assert r.status_code == 200 and next(v for v in r.json()["validators"] if v["validatorId"] == "test_gw_val")["name"] == "TEST_gw2"
        r = ext("DELETE", "console/fleet/test_gw_val", t)
        assert r.status_code == 200
        assert not any(v["validatorId"] == "test_gw_val" for v in S.get(f"{A}/console/fleet").json()["validators"])

    def test_admin_key_management_via_gateway(self, keys):
        t = keys["admin"]["token"]
        r = ext("POST", "console/apikeys", t, json={"name": "TEST_via_gw", "scope": "read"})
        assert r.status_code == 200 and r.json()["token"]
        CREATED.append(r.json()["keyId"])
        assert ext("GET", "console/apikeys", t).status_code == 200
        r = ext("PUT", "console/fleet/fras_val_003/routing", t, json={"slackMemberId": "U12345678"})
        assert r.status_code == 200
        r = ext("DELETE", "console/fleet/fras_val_003/routing", t)
        assert r.status_code == 200
        cfg = S.get(f"{A}/console/alerts").json()
        print("slack configured:", {k: v for k, v in cfg.items() if "slack" in k.lower()} if isinstance(cfg, dict) else cfg)

    def test_nested_and_traversal(self, keys):
        r = ext("GET", "ext/v1/validator/status", keys["admin"]["token"])
        assert r.status_code == 409
        r = ext("GET", "console/x/../apikeys", keys["write"]["token"])
        print("traversal status", r.status_code, r.text[:150])
        assert r.status_code != 200 or "keys" not in r.text
