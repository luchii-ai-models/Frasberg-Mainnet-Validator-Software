"""Fleet view, audit archive export, live alert delivery and upgrade countdown."""
import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import Response
from pydantic import BaseModel, Field

import core
from core import FrasError
from store import iso, now

ANNOUNCE_DAYS, WINDOW_DAYS = 30, 14
EPOCH_HOURS = 10
SAFE_FIELDS = ["validatorId", "name", "operator", "region", "validatorVersion", "luchiiVersion", "channel", "epoch", "commission", "stake",
               "uptime", "voteParticipation", "missedBlocks", "apiAvailability", "securityScanPassed", "identityVerified", "governanceStanding"]


def _v(vid, name, op, region, ver, lu, ch="stable", com=5, stake=150000, up=99.95, vote=97.0, miss=1.0, api=99.9, sec=True, ident=True, gov=True):
    return {"validatorId": vid, "name": name, "operator": op, "region": region, "validatorVersion": ver, "luchiiVersion": lu, "channel": ch,
            "epoch": 900, "commission": com, "stake": stake, "uptime": up, "voteParticipation": vote, "missedBlocks": miss, "apiAvailability": api,
            "securityScanPassed": sec, "identityVerified": ident, "governanceStanding": gov}


FLEET_SEED = [
    _v("fras_val_002", "Nordlicht", "Aurora Staking", "eu-north", "6.4.1", "4.2.1", stake=420000, up=99.99, vote=99.1, miss=0.4),
    _v("fras_val_003", "Kestrel", "Kestrel Labs", "us-east", "6.3.0", "4.1.0", stake=180000),
    _v("fras_val_004", "Basalt", "Basalt Nodes", "ap-south", "6.2.0", "4.0.0", com=4, stake=310000, up=99.92),
    _v("fras_val_005", "Ferrous", "Ferrous Ops", "eu-west", "6.1.3", "4.0.0", stake=260000),
    _v("fras_val_006", "Halcyon", "Halcyon DAO", "us-west", "6.4.1-rc.1", "4.2.0", ch="rc", stake=120000),
    _v("fras_val_007", "Meridian", "Meridian Capital", "sa-east", "6.4.1", "4.2.0", com=8, stake=900000),
    _v("fras_val_008", "Quill", "Quill Infra", "ap-northeast", "6.4.0", "4.2.0", stake=60000, up=99.7, vote=93.0, miss=4.2, api=99.1),
    _v("fras_val_009", "Obsidian", "Obsidian Security", "eu-central", "6.4.1", "4.2.0", stake=140000, ident=False),
]


class FleetValidator(BaseModel):
    validatorId: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=40)
    operator: str = "independent"
    region: str = "global"
    validatorVersion: str
    luchiiVersion: str
    channel: str = "stable"
    epoch: int = Field(900, ge=0)
    commission: float = Field(5, ge=0, le=100)
    stake: float = Field(100000, ge=0)
    uptime: float = Field(99.9, ge=0, le=100)
    voteParticipation: float = Field(95, ge=0, le=100)
    missedBlocks: float = Field(0, ge=0, le=100)
    apiAvailability: float = Field(99.5, ge=0, le=100)
    securityScanPassed: bool = True
    identityVerified: bool = True
    governanceStanding: bool = True


class ArchiveBody(BaseModel):
    toSequence: Optional[int] = Field(None, ge=1)
    olderThanMinutes: Optional[int] = Field(None, ge=0)


class VerifyUpload(BaseModel):
    content: str
    sha256: Optional[str] = None


class AlertConfig(BaseModel):
    slackWebhookUrl: Optional[str] = None
    pagerdutyRoutingKey: Optional[str] = None
    autoDispatch: Optional[bool] = None
    quietEnabled: Optional[bool] = None
    quietStart: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quietEnd: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quietMode: Optional[str] = Field(None, pattern=r"^(digest|suppress)$")
    escalationMinutes: Optional[int] = Field(None, ge=0, le=1440)


QUIET_DEFAULTS = {"quietEnabled": False, "quietStart": "22:00", "quietEnd": "07:00", "quietMode": "digest", "escalationMinutes": 15}


def in_quiet(cfg, t):
    if not cfg["quietEnabled"]:
        return False
    m = t.hour * 60 + t.minute
    a, b = (int(x[:2]) * 60 + int(x[3:]) for x in (cfg["quietStart"], cfg["quietEnd"]))
    return a <= m < b if a <= b else (m >= a or m < b)


def route_alert(alert):
    """First-match emulation of the Alertmanager route tree in artifacts.ALERTMANAGER."""
    if alert["severity"] == "critical":
        return "pagerduty-critical", "pagerduty"
    if alert["severity"] == "warning":
        return "slack-platform", "slack"
    return {"audit-outbox": "audit-team", "validator-lock": "validator-team", "chaos": "reliability-team"}.get(alert["service"], "default"), "slack"


def register(api, store, db, required_versions, metric_snapshot, get_validator):
    fleet, archives, deliveries, alert_state = db.fleet, db.audit_archives, db.alert_deliveries, db.alert_state

    async def evaluate(v, upcoming):
        req = await required_versions(v["epoch"])
        c = core.evaluate_compliance(v, req)
        nxt = None
        if upcoming:
            nc = core.evaluate_compliance({**v, "epoch": upcoming["epoch"]}, upcoming)
            nxt = {"epoch": upcoming["epoch"], "ready": nc["compliant"], "code": nc["code"]}
        return {**{k: v.get(k) for k in SAFE_FIELDS}, "status": "healthy" if c["healthy"] else "unhealthy", "compliant": c["compliant"],
                "code": c["code"], "versionClass": c["versionClass"], "delegationEligible": c["delegationEligible"],
                "rewardsEnabled": c["rewardsEnabled"], "votingEnabled": c["votingEnabled"], "health": c["health"],
                "failedCriteria": [k for k, ok in c["criteria"].items() if not ok], "nextRequirement": nxt}

    async def next_requirement(epoch):
        s = await store.schedule.find_one({"epoch": {"$gt": epoch}}, {"_id": 0}, sort=[("epoch", 1)])
        return await required_versions(s["epoch"]) if s else None

    # ---------- fleet ----------
    @api.get("/console/fleet")
    async def fleet_list():
        me = await get_validator()
        upcoming = await next_requirement(me["epoch"])
        rows = [{**await evaluate(me, upcoming), "name": "This node", "operator": "self", "region": "local", "self": True}]
        async for d in fleet.find({}, {"_id": 0}).sort("validatorId", 1):
            rows.append({**await evaluate(d, upcoming), "self": False})
        summary = {"total": len(rows), "healthy": sum(r["status"] == "healthy" for r in rows), "compliant": sum(r["compliant"] for r in rows),
                   "eligible": sum(r["delegationEligible"] for r in rows), "readyNext": sum(bool(r["nextRequirement"] and r["nextRequirement"]["ready"]) for r in rows),
                   "stakeEligible": sum(r["stake"] for r in rows if r["delegationEligible"]), "stakeTotal": sum(r["stake"] for r in rows)}
        return {"validators": rows, "summary": summary, "upcoming": upcoming}

    def check_versions(b):
        if not core.semver_valid(b.validatorVersion) or not core.semver_valid(b.luchiiVersion):
            raise FrasError("INVALID_VERSION")
        if b.channel not in core.CHANNELS:
            raise FrasError("INVALID_VERSION", "Unknown release channel")

    @api.post("/console/fleet")
    async def fleet_create(body: FleetValidator):
        check_versions(body)
        if body.validatorId == (await get_validator())["validatorId"] or await fleet.find_one({"validatorId": body.validatorId}):
            raise FrasError("INVALID_STATE", "validatorId already exists")
        await fleet.insert_one(body.model_dump())
        await store.append_audit("VALIDATOR_REGISTERED", "validator-admin", metadata={"validatorId": body.validatorId})
        return await fleet_list()

    @api.put("/console/fleet/{vid}")
    async def fleet_update(vid: str, body: FleetValidator):
        check_versions(body)
        res = await fleet.update_one({"validatorId": vid}, {"$set": {**body.model_dump(), "validatorId": vid}})
        if res.matched_count != 1:
            raise FrasError("INVALID_STATE", "Fleet validator not found (edit this node on Overview)")
        await store.append_audit("VALIDATOR_UPDATED", "validator-admin", metadata={"validatorId": vid})
        return await fleet_list()

    @api.delete("/console/fleet/{vid}")
    async def fleet_delete(vid: str):
        if (await fleet.delete_one({"validatorId": vid})).deleted_count != 1:
            raise FrasError("INVALID_STATE", "Fleet validator not found")
        await store.append_audit("VALIDATOR_DEREGISTERED", "validator-admin", metadata={"validatorId": vid})
        return await fleet_list()

    # ---------- audit archive (write-once, checksummed NDJSON) ----------
    def verify_ndjson(content, manifest_sha=None, start_prev=None, start_seq=None):
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        try:
            recs = [json.loads(line) for line in content.splitlines() if line.strip()]
        except json.JSONDecodeError:
            return {"valid": False, "code": "INVALID_NDJSON", "sha256": sha}
        if not recs:
            return {"valid": False, "code": "EMPTY_ARCHIVE", "sha256": sha}
        need = ("sequence", "previous_hash", "current_hash", "payload")
        if not all(isinstance(r, dict) and all(k in r for k in need) and isinstance(r["sequence"], int) for r in recs):
            return {"valid": False, "code": "INVALID_RECORD", "sha256": sha}
        chain = core.verify_audit_chain(recs, start_prev or recs[0]["previous_hash"], start_seq or recs[0]["sequence"])
        checksum_ok = manifest_sha is None or sha == manifest_sha
        return {"valid": checksum_ok and chain["valid"], "code": chain["code"] if checksum_ok else "CHECKSUM_MISMATCH", "sha256": sha,
                "checksumMatches": checksum_ok, "chain": chain, "records": len(recs),
                "range": [recs[0]["sequence"], recs[-1]["sequence"]]}

    @api.post("/console/audit/archives")
    async def archive_create(body: ArchiveBody):
        last = await archives.find_one({}, sort=[("to_sequence", -1)])
        start = (last["to_sequence"] + 1) if last else 1
        q = {"sequence": {"$gte": start}}
        if body.toSequence:
            q["sequence"]["$lte"] = body.toSequence
        if body.olderThanMinutes is not None:
            q["timestamp"] = {"$lte": iso(now() - timedelta(minutes=body.olderThanMinutes))}
        recs = await store.audit.find(q, {"_id": 0}).sort("sequence", 1).to_list(None)
        if not recs:
            raise FrasError("INVALID_STATE", "No unarchived audit records match")
        # keep the export contiguous
        recs = [r for i, r in enumerate(recs) if r["sequence"] == start + i]
        content = "\n".join(core.canonicalize(r) for r in recs) + "\n"
        check = verify_ndjson(content, None, recs[0]["previous_hash"], start)
        if not check["valid"]:
            raise FrasError("INVALID_STATE", f"Live chain segment invalid: {check['code']}")
        aid = f"audit_log_{recs[0]['sequence']:06d}_{recs[-1]['sequence']:06d}"
        doc = {"archive_id": aid, "from_sequence": recs[0]["sequence"], "to_sequence": recs[-1]["sequence"], "count": len(recs),
               "sha256": check["sha256"], "previous_hash": recs[0]["previous_hash"], "head_hash": recs[-1]["current_hash"],
               "first_timestamp": recs[0]["timestamp"], "last_timestamp": recs[-1]["timestamp"], "bytes": len(content.encode()),
               "content": content, "created_at": iso(), "storage": "worm", "status": "verified"}
        await archives.insert_one(doc)
        await store.append_audit("AUDIT_ARCHIVED", "archive-job", metadata={k: doc[k] for k in ["archive_id", "from_sequence", "to_sequence", "count", "sha256"]})
        return {k: v for k, v in doc.items() if k not in ("_id", "content")}

    @api.get("/console/audit/archives")
    async def archive_list():
        rows = await archives.find({}, {"_id": 0, "content": 0}).sort("from_sequence", -1).to_list(200)
        nxt = (rows[0]["to_sequence"] + 1) if rows else 1
        total = await store.audit.count_documents({})
        return {"archives": rows, "nextSequence": nxt, "unarchived": max(0, total - nxt + 1), "totalRecords": total}

    async def get_archive(aid):
        doc = await archives.find_one({"archive_id": aid})
        if not doc:
            raise FrasError("INVALID_STATE", "Archive not found")
        return doc

    @api.get("/console/audit/archives/{aid}/download")
    async def archive_download(aid: str):
        doc = await get_archive(aid)
        return Response(content=doc["content"], media_type="application/x-ndjson",
                        headers={"Content-Disposition": f'attachment; filename="{aid}.ndjson"', "X-Checksum-SHA256": doc["sha256"],
                                 "Access-Control-Expose-Headers": "X-Checksum-SHA256, Content-Disposition"})

    @api.get("/console/audit/archives/{aid}/checksum", response_class=Response)
    async def archive_checksum(aid: str):
        doc = await get_archive(aid)
        return Response(content=f"{doc['sha256']}  {aid}.ndjson\n", media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{aid}.ndjson.sha256"'})

    @api.post("/console/audit/archives/{aid}/verify")
    async def archive_verify(aid: str):
        doc = await get_archive(aid)
        r = verify_ndjson(doc["content"], doc["sha256"], doc["previous_hash"], doc["from_sequence"])
        live = await store.audit.find({"sequence": {"$gte": doc["from_sequence"], "$lte": doc["to_sequence"]}}, {"_id": 0}).sort("sequence", 1).to_list(None)
        r["matchesLiveLog"] = "\n".join(core.canonicalize(x) for x in live) + "\n" == doc["content"]
        r["valid"] = r["valid"] and r["matchesLiveLog"]
        r["verifiedAt"] = iso()
        return r

    @api.post("/console/audit/archives/verify-upload")
    async def archive_verify_upload(body: VerifyUpload):
        r = verify_ndjson(body.content, body.sha256.strip().split()[0].lower() if body.sha256 else None)
        if r.get("range"):
            m = await archives.find_one({"from_sequence": r["range"][0], "to_sequence": r["range"][1]}, {"_id": 0, "archive_id": 1, "sha256": 1})
            r["knownArchive"] = m["archive_id"] if m else None
            r["matchesManifest"] = bool(m and m["sha256"] == r["sha256"])
            r["valid"] = r["valid"] and r["matchesManifest"]
            if not r["matchesManifest"] and r["code"] == "CHAIN_VALID":
                r["code"] = "MANIFEST_MISMATCH" if m else "UNKNOWN_ARCHIVE"
        return r

    # ---------- live alert delivery ----------
    async def get_config():
        c = await db.settings.find_one({"_id": "alerting"}) or {}
        return {"slackWebhookUrl": c.get("slackWebhookUrl"), "pagerdutyRoutingKey": c.get("pagerdutyRoutingKey"), "autoDispatch": c.get("autoDispatch", True),
                "slackChannel": c.get("slackChannel"), **{k: c.get(k, v) for k, v in QUIET_DEFAULTS.items()}}

    async def routing_for(vid):
        r = await db.validator_routing.find_one({"validatorId": vid}) or {}
        mid = r.get("slackMemberId")
        mention = (f"<!subteam^{mid}>" if mid.startswith("S") else f"<@{mid}>") if mid else ""
        return {"mention": mention, "webhook": r.get("slackWebhookUrl")}

    async def log_delivery(alert, status, receiver, channel, result, detail, summary, test=False):
        doc = {"id": str(uuid.uuid4()), "alert": alert["name"], "status": status, "severity": alert["severity"], "service": alert["service"],
               "receiver": receiver, "channel": channel, "result": result, "detail": detail, "summary": summary, "test": test, "sentAt": iso()}
        await deliveries.insert_one(dict(doc))
        await store.inc("alert_notifications_total", labels={"result": result, "status": status})
        return doc

    async def post_slack(cfg, text):
        if not cfg["slackWebhookUrl"]:
            return "dry_run", "no destination configured"
        try:
            async with httpx.AsyncClient(timeout=8) as http:
                r = await http.post(cfg["slackWebhookUrl"], json={"text": text})
            return ("delivered" if r.status_code < 300 else "failed"), f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            return "failed", type(e).__name__

    async def deliver(alert, status, receiver, channel, cfg, test=False):
        summary = f"[{status.upper()}] {alert['name']} ({alert['severity']}) service={alert['service']} value={alert.get('value')}"
        result, detail = "dry_run", "no destination configured"
        try:
            async with httpx.AsyncClient(timeout=8) as http:
                if channel == "pagerduty" and cfg["pagerdutyRoutingKey"]:
                    r = await http.post("https://events.pagerduty.com/v2/enqueue", json={
                        "routing_key": cfg["pagerdutyRoutingKey"], "event_action": "resolve" if status == "resolved" else "trigger",
                        "dedup_key": f"frasberg-{alert['name']}", "payload": {"summary": summary, "source": "fras_val_001",
                                                                             "severity": "critical" if alert["severity"] == "critical" else "warning",
                                                                             "custom_details": alert}})
                    result, detail = ("delivered" if r.status_code < 300 else "failed"), f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            result, detail = "failed", type(e).__name__
        if channel == "slack" or alert.get("validatorId"):
            pd = (result, detail) if channel == "pagerduty" else None
            text = f":rotating_light: {summary}\nrunbook: https://runbooks.frasberg.com/{alert['name']}"
            target = cfg
            if alert.get("validatorId"):
                r = await routing_for(alert["validatorId"])
                text = f"{r['mention']} {text}".strip()
                if r["webhook"]:
                    target = {**cfg, "slackWebhookUrl": r["webhook"]}
            result, detail = await post_slack(target, text)
            if pd:
                result = "delivered" if "delivered" in (pd[0], result) else "failed" if "failed" in (pd[0], result) else "dry_run"
                detail = f"pagerduty {pd[0]} · slack {detail}"
        return await log_delivery(alert, status, receiver, channel, result, detail, summary, test)

    async def flush_digest(cfg):
        items = await db.alert_digest.find({"sentAt": None}).sort("queuedAt", 1).to_list(None)
        if not items:
            return None
        lines = [f"• {i['queuedAt'][11:16]} [{i['status'].upper()}] {i['alert']} ({i['severity']}) service={i['service']}" for i in items]
        text = f":crescent_moon: Frasberg quiet-hours digest — {len(items)} notification(s)\n" + "\n".join(lines)
        result, detail = await post_slack(cfg, text)
        await db.alert_digest.update_many({"_id": {"$in": [i["_id"] for i in items]}}, {"$set": {"sentAt": iso()}})
        return await log_delivery({"name": "QuietHoursDigest", "severity": "warning", "service": "alerting"}, "digest", "slack-platform", "slack",
                                  result, detail, f"[DIGEST] {len(items)} batched notification(s)")

    async def escalate(cfg, firing):
        mins = cfg["escalationMinutes"]
        if not mins:
            return []
        out, t = [], now()
        for a in firing:
            if a["severity"] != "critical":
                continue
            st = await alert_state.find_one({"_id": a["name"]})
            if not st or not st.get("notified") or st.get("acknowledged") or not st.get("lastPagedAt"):
                continue
            if t - datetime.fromisoformat(st["lastPagedAt"].replace("Z", "+00:00")) < timedelta(minutes=mins):
                continue
            await alert_state.update_one({"_id": a["name"]}, {"$set": {"lastPagedAt": iso()}, "$inc": {"escalations": 1}})
            receiver, channel = route_alert(a)
            out.append(await deliver(a, "escalation", receiver, channel, cfg))
        return out

    sources = []

    async def all_alerts():
        alerts = list((await metric_snapshot())["alerts"])
        for src in sources:
            alerts += await src()
        return alerts

    async def dispatch():
        cfg = await get_config()
        alerts = await all_alerts()
        firing = [a for a in alerts if a["firing"]]
        sent = []
        for a in alerts:
            inhibited_by = next((s["name"] for s in firing if s["name"] != a["name"] and core.is_inhibited(s["name"], a["name"], {"environment": "prod"})), None)
            prev = await alert_state.find_one({"_id": a["name"]}) or {"firing": False, "notified": False}
            regraded = a["firing"] and prev["firing"] and prev.get("severity", a["severity"]) != a["severity"]
            if a["firing"] == prev["firing"] and not regraded:
                continue
            notify = (not inhibited_by) if a["firing"] else prev.get("notified", False)
            status = "firing" if a["firing"] else "resolved"
            receiver, channel = route_alert(a)
            held = notify and a["severity"] != "critical" and in_quiet(cfg, now())
            if held:
                summary = f"[{status.upper()}] {a['name']} ({a['severity']}) service={a['service']} value={a.get('value')}"
                if cfg["quietMode"] == "digest":
                    await db.alert_digest.insert_one({"alert": a["name"], "status": status, "severity": a["severity"], "service": a["service"], "queuedAt": iso(), "sentAt": None})
                    sent.append(await log_delivery(a, status, receiver, channel, "queued", "quiet hours · digest at window end", summary))
                else:
                    sent.append(await log_delivery(a, status, receiver, channel, "suppressed", "quiet hours", summary))
                notify = notify and cfg["quietMode"] == "digest"
            await alert_state.update_one({"_id": a["name"]}, {"$set": {"firing": a["firing"], "since": iso(), "inhibitedBy": inhibited_by,
                                                                    "notified": a["firing"] and notify, "severity": a["severity"], "acknowledged": False, "acknowledgedAt": None, "escalations": 0,
                                                                    "lastPagedAt": iso() if a["firing"] and notify else None}}, upsert=True)
            if notify and not held:
                sent.append(await deliver(a, status, receiver, channel, cfg))
        sent += await escalate(cfg, firing)
        if not in_quiet(cfg, now()):
            d = await flush_digest(cfg)
            if d:
                sent.append(d)
        return {"evaluated": len(alerts), "firing": len(firing), "notifications": sent, "quiet": in_quiet(cfg, now())}

    @api.get("/console/alerts")
    async def alerts_state():
        states = {s["_id"]: s async for s in alert_state.find({})}
        rows = []
        for a in await all_alerts():
            receiver, channel = route_alert(a)
            st = states.get(a["name"], {})
            if a.get("validatorId") and not a["firing"] and not st.get("firing"):
                continue
            rows.append({**a, "receiver": receiver, "channel": channel, "since": st.get("since"), "inhibitedBy": st.get("inhibitedBy"),
                         "acknowledged": st.get("acknowledged", False), "escalations": st.get("escalations", 0), "notified": st.get("notified", False)})
        cfg = await get_config()
        log = await deliveries.find({}, {"_id": 0}).sort("sentAt", -1).limit(50).to_list(None)
        pending = await db.alert_digest.count_documents({"sentAt": None})
        return {"alerts": rows, "deliveries": log, "digestPending": pending, "quietActive": in_quiet(cfg, now()),
                "config": {"slackConfigured": bool(cfg["slackWebhookUrl"]), "slackChannel": cfg["slackChannel"], "pagerdutyConfigured": bool(cfg["pagerdutyRoutingKey"]),
                           "autoDispatch": cfg["autoDispatch"], **{k: cfg[k] for k in QUIET_DEFAULTS}}}

    @api.post("/console/alerts/{name}/ack")
    async def alerts_ack(name: str):
        res = await alert_state.update_one({"_id": name, "firing": True}, {"$set": {"acknowledged": True, "acknowledgedAt": iso()}})
        if res.matched_count != 1:
            raise FrasError("INVALID_STATE", "Alert is not firing")
        await store.append_audit("ALERT_ACKNOWLEDGED", "operator", metadata={"alert": name})
        return {"acknowledged": name}

    @api.post("/console/alerts/digest/flush")
    async def alerts_digest_flush():
        return {"notification": await flush_digest(await get_config())}

    @api.put("/console/alerts/config")
    async def alerts_config(body: AlertConfig):
        upd = body.model_dump(exclude_none=True)
        if upd.get("slackWebhookUrl") and not upd["slackWebhookUrl"].startswith("https://hooks.slack.com/"):
            raise FrasError("INVALID_STATE", "Slack webhook must start with https://hooks.slack.com/")
        for k in ("slackWebhookUrl", "pagerdutyRoutingKey"):
            if k in upd and upd[k] == "":
                upd[k] = None
        if upd.get("slackWebhookUrl", 1) is None:
            upd["slackChannel"] = None
        await db.settings.update_one({"_id": "alerting"}, {"$set": upd}, upsert=True)
        return (await alerts_state())["config"]

    @api.post("/console/alerts/dispatch")
    async def alerts_dispatch():
        return await dispatch()

    @api.post("/console/alerts/test")
    async def alerts_test():
        cfg = await get_config()
        out = []
        for sev, svc in (("critical", "validator-lock"), ("warning", "audit-outbox")):
            a = {"name": "FrasbergTestAlert", "severity": sev, "service": svc, "value": 1, "firing": True}
            receiver, channel = route_alert(a)
            out.append(await deliver(a, "firing", receiver, channel, cfg, test=True))
        return {"notifications": out}

    async def loop():
        while True:
            await asyncio.sleep(60)
            try:
                if (await get_config())["autoDispatch"]:
                    await dispatch()
            except Exception:
                logging.exception("alert dispatch failed")

    # ---------- upgrade countdown ----------
    async def epoch_clock():
        c = await db.settings.find_one({"_id": "epoch_clock"})
        if not c:
            c = {"_id": "epoch_clock", "epoch": 900, "startedAt": now(), "epochHours": EPOCH_HOURS}
            await db.settings.insert_one(c)
        started = c["startedAt"] if c["startedAt"].tzinfo else c["startedAt"].replace(tzinfo=timezone.utc)
        return c["epoch"], started, c["epochHours"]

    @api.get("/console/upgrades")
    async def upgrades():
        base_epoch, started, hours = await epoch_clock()
        me = await get_validator()
        fl = await fleet.find({}, {"_id": 0}).to_list(None)
        t = now()
        rows = []
        for s in await store.schedule.find({}, {"_id": 0}).sort("epoch", 1).to_list(None):
            req = await required_versions(s["epoch"])
            enforce = started + timedelta(hours=hours * (s["enforcement"]["startsEpoch"] - base_epoch))
            grace_end = started + timedelta(hours=hours * (s["enforcement"]["graceEndsEpoch"] - base_epoch + 1))
            window_start = enforce - timedelta(days=WINDOW_DAYS)
            announce = window_start - timedelta(days=ANNOUNCE_DAYS)
            phases = [("announcement", announce, window_start), ("upgrade_window", window_start, enforce), ("grace", enforce, grace_end)]
            current = "scheduled" if t < announce else next((n for n, a, b in phases if a <= t < b), "enforced")
            nxt = next(((n, a) for n, a, _ in phases if a > t), ("enforced", grace_end) if t < grace_end else (None, None))
            ready = lambda v: core.evaluate_compliance({**v, "epoch": s["epoch"], "frozen": False}, req)["compliant"]
            rows.append({"epoch": s["epoch"], "validator": req["validator"], "luchii": req["luchii"], "currentPhase": current,
                         "phases": [{"name": n, "start": iso(a), "end": iso(b)} for n, a, b in phases],
                         "announceAt": iso(announce), "enforceAt": iso(enforce), "graceEndsAt": iso(grace_end),
                         "nextMilestone": nxt[0], "nextMilestoneAt": iso(nxt[1]) if nxt[1] else None,
                         "secondsToEnforcement": max(0, int((enforce - t).total_seconds())),
                         "selfReady": ready(me), "fleetReady": sum(ready(v) for v in fl) + ready(me), "fleetTotal": len(fl) + 1})
        return {"now": iso(t), "epochHours": hours, "clock": {"epoch": base_epoch, "startedAt": iso(started)},
                "policy": {"announcementDays": ANNOUNCE_DAYS, "upgradeWindowDays": WINDOW_DAYS, "graceEpochs": 1}, "requirements": rows}

    async def seed():
        await fleet.create_index("validatorId", unique=True)
        await archives.create_index("archive_id", unique=True)
        if not await fleet.find_one({}):
            await fleet.insert_many([dict(v) for v in FLEET_SEED])
        await epoch_clock()
        asyncio.create_task(loop())

    return seed, {"get_config": get_config, "post_slack": post_slack, "routing_for": routing_for, "sources": sources, "archive_create": archive_create, "upgrades": upgrades}
