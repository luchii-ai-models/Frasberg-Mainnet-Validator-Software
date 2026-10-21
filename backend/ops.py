"""Fleet trend sampling, scheduled archive export, upgrade nudges and Slack OAuth connect."""
import asyncio
import logging
import os
import random
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

import core
from core import FrasError
from extras import ArchiveBody
from store import iso, now

METRICS = {"uptime": "u", "voteParticipation": "vp", "missedBlocks": "mb"}
RANGES = {"1h": (3600_000, 60_000), "24h": (86_400_000, 900_000), "7d": (604_800_000, 7_200_000)}
DRIFT = {"uptime": (-0.05, 99.9), "voteParticipation": (-1.0, 95.0), "missedBlocks": (1.0, 3.0)}
# how much higher (+) or lower (-) each metric was 7 days ago, so the series drifts into today's value
PROFILES = {"fras_val_008": {"u": 0.27, "vp": 4.5, "mb": -3.0}, "fras_val_003": {"vp": 1.8, "mb": -0.9},
            "fras_val_005": {"u": 0.04, "mb": -0.6}, "fras_val_002": {"u": -0.01, "vp": -0.4}}
NOISE = {"u": 0.008, "vp": 0.25, "mb": 0.12}
LIMITS = {"u": (0, 100), "vp": (0, 100), "mb": (0, 100)}


class ScheduleBody(BaseModel):
    enabled: Optional[bool] = None
    timeUtc: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    olderThanMinutes: Optional[int] = Field(None, ge=0, le=525600)


class NudgeBody(BaseModel):
    resend: bool = False


class RoutingBody(BaseModel):
    slackMemberId: Optional[str] = Field(None, pattern=r"^$|^[UWS][A-Z0-9]{6,15}$")
    slackWebhookUrl: Optional[str] = None


def _ms(t):
    return int(t.timestamp() * 1000)


def _sample(base, vid, t_ms, rng, age_frac=0.0):
    prof = PROFILES.get(vid, {})
    out = {}
    for field, k in METRICS.items():
        v = base[field] + prof.get(k, 0) * age_frac + rng.gauss(0, NOISE[k])
        lo, hi = LIMITS[k]
        out[k] = round(min(hi, max(lo, v)), 3)
    return {"v": vid, "t": t_ms, **out}


def register(api, store, db, required_versions, get_validator, alerting):
    samples, runs, nudges = db.fleet_samples, db.archive_runs, db.upgrade_nudges

    async def all_validators():
        me = await get_validator()
        rows = [{**me, "name": "This node", "operator": "self", "self": True}]
        rows += [{**d, "self": False} async for d in db.fleet.find({}, {"_id": 0}).sort("validatorId", 1)]
        return rows

    # ---------- fleet trends ----------
    async def backfill():
        if await samples.find_one({}):
            return
        t, docs = now(), []
        for v in await all_validators():
            rng = random.Random(v["validatorId"])
            stamps = sorted({_ms(t - timedelta(minutes=m)) for m in range(1, 61)} |
                            {_ms(t - timedelta(minutes=m)) for m in range(60, 1441, 10)} |
                            {_ms(t - timedelta(hours=h)) for h in range(24, 169)})
            for s in stamps:
                docs.append(_sample(v, v["validatorId"], s, rng, (_ms(t) - s) / RANGES["7d"][0]))
        await samples.insert_many(docs)

    async def sample_now():
        t = _ms(now())
        await samples.insert_many([_sample(v, v["validatorId"], t, random.Random()) for v in await all_validators()])
        await samples.delete_many({"t": {"$lt": t - 8 * 86_400_000}})

    @api.get("/console/fleet/trends")
    async def fleet_trends(range: str = "24h"):
        if range not in RANGES:
            raise FrasError("INVALID_STATE", "range must be 1h, 24h or 7d")
        span, bucket = RANGES[range]
        since = _ms(now()) - span
        pipe = [{"$match": {"t": {"$gte": since}}},
                {"$group": {"_id": {"v": "$v", "b": {"$subtract": ["$t", {"$mod": ["$t", bucket]}]}},
                            **{k: {"$avg": f"${k}"} for k in METRICS.values()}}},
                {"$sort": {"_id.b": 1}}]
        points, per_v = {}, {}
        async for g in samples.aggregate(pipe):
            vid, b = g["_id"]["v"], g["_id"]["b"]
            per_v.setdefault(vid, []).append(g)
            for field, k in METRICS.items():
                points.setdefault(field, {}).setdefault(b, {"t": b})[vid] = round(g[k], 3)
        validators = await all_validators()
        drift = []
        for v in validators:
            rows = per_v.get(v["validatorId"], [])
            if not rows:
                continue
            n = max(1, len(rows) // 6)
            entry = {"validatorId": v["validatorId"], "name": v["name"], "self": v["self"], "metrics": {}}
            for field, k in METRICS.items():
                first = sum(r[k] for r in rows[:n]) / n
                last = sum(r[k] for r in rows[-n:]) / n
                delta, limit = DRIFT[field]
                change = last - first
                drifting = change <= delta if delta < 0 else change >= delta
                breach = last < limit if delta < 0 else last > limit
                entry["metrics"][field] = {"first": round(first, 3), "last": round(last, 3), "change": round(change, 3),
                                           "drifting": drifting, "breach": breach}
            entry["drifting"] = any(m["drifting"] for m in entry["metrics"].values())
            entry["breach"] = any(m["breach"] for m in entry["metrics"].values())
            drift.append(entry)
        return {"range": range, "bucketMs": bucket, "validators": [v["validatorId"] for v in validators],
                "series": {f: sorted(p.values(), key=lambda x: x["t"]) for f, p in points.items()},
                "drift": drift, "thresholds": {f: d[1] for f, d in DRIFT.items()}}

    # ---------- scheduled archive export ----------
    async def schedule_cfg():
        c = await db.settings.find_one({"_id": "archive_schedule"}) or {}
        return {"enabled": c.get("enabled", True), "timeUtc": c.get("timeUtc", "02:00"),
                "olderThanMinutes": c.get("olderThanMinutes", 0), "lastRunDate": c.get("lastRunDate")}

    async def run_archive(trigger):
        cfg = await schedule_cfg()
        doc = {"id": str(uuid.uuid4()), "trigger": trigger, "startedAt": iso(), "olderThanMinutes": cfg["olderThanMinutes"]}
        try:
            a = await alerting["archive_create"](ArchiveBody(olderThanMinutes=cfg["olderThanMinutes"] or None))
            doc.update(status="success", archiveId=a["archive_id"], count=a["count"], sha256=a["sha256"],
                       detail=f"#{a['from_sequence']}–#{a['to_sequence']}")
        except FrasError as e:
            doc.update(status="skipped", detail=e.message if hasattr(e, "message") else str(e))
        except Exception as e:  # noqa: BLE001
            logging.exception("scheduled archive failed")
            doc.update(status="failed", detail=type(e).__name__)
        doc["finishedAt"] = iso()
        await runs.insert_one(dict(doc))
        await store.append_audit("ARCHIVE_SCHEDULE_RUN", "archive-scheduler", metadata={k: doc.get(k) for k in ("trigger", "status", "archiveId", "count")})
        return doc

    async def maybe_run_schedule():
        cfg = await schedule_cfg()
        t = now()
        due = t.replace(hour=int(cfg["timeUtc"][:2]), minute=int(cfg["timeUtc"][3:]), second=0, microsecond=0)
        today = t.date().isoformat()
        if not cfg["enabled"] or t < due or cfg["lastRunDate"] == today:
            return
        claim = await db.settings.update_one({"_id": "archive_schedule", "lastRunDate": {"$ne": today}}, {"$set": {"lastRunDate": today}}, upsert=False)
        if claim.matched_count == 1:
            await run_archive("scheduled")

    async def schedule_view():
        cfg = await schedule_cfg()
        t = now()
        due = t.replace(hour=int(cfg["timeUtc"][:2]), minute=int(cfg["timeUtc"][3:]), second=0, microsecond=0)
        nxt = due + timedelta(days=1) if cfg["lastRunDate"] == t.date().isoformat() else max(due, t)
        history = await runs.find({}, {"_id": 0}).sort("startedAt", -1).limit(30).to_list(None)
        return {**cfg, "nextRunAt": iso(nxt) if cfg["enabled"] else None, "history": history}

    @api.get("/console/audit/archive-schedule")
    async def archive_schedule_get():
        return await schedule_view()

    @api.put("/console/audit/archive-schedule")
    async def archive_schedule_put(body: ScheduleBody):
        upd = body.model_dump(exclude_none=True)
        await db.settings.update_one({"_id": "archive_schedule"}, {"$set": upd}, upsert=True)
        await store.append_audit("ARCHIVE_SCHEDULE_UPDATED", "archive-admin", metadata=upd)
        return await schedule_view()

    @api.post("/console/audit/archive-schedule/run")
    async def archive_schedule_run():
        return await run_archive("manual")

    # ---------- upgrade nudges ----------
    async def lagging(epoch):
        req = await required_versions(epoch)
        out = []
        for v in await all_validators():
            c = core.evaluate_compliance({**v, "epoch": epoch, "frozen": False}, req)
            if not c["compliant"]:
                out.append((v, c["code"]))
        return req, out

    async def send_nudges(row, trigger, resend=False):
        cfg = await alerting["get_config"]()
        req, lag = await lagging(row["epoch"])
        sent = []
        for v, code in lag:
            key = {"epoch": row["epoch"], "validatorId": v["validatorId"]}
            if not resend and await nudges.find_one(key):
                continue
            text = (f":arrow_up: Upgrade heads-up for *{v['validatorId']}* ({v['name']} · {v['operator']})\n"
                    f"Running core {v['validatorVersion']} / luchii {v['luchiiVersion']} — epoch {row['epoch']} requires "
                    f"core ≥ {req['validator']['minimum']} (rec {req['validator']['recommended']}) and luchii ≥ {req['luchii']['minimum']}.\n"
                    f"Status: {code}. Enforcement {row['enforceAt'][:16].replace('T', ' ')} UTC · grace ends {row['graceEndsAt'][:16].replace('T', ' ')} UTC.")
            route = await alerting["routing_for"](v["validatorId"])
            text = f"{route['mention']} {text}".strip()
            result, detail = await alerting["post_slack"]({**cfg, "slackWebhookUrl": route["webhook"] or cfg["slackWebhookUrl"]}, text)
            doc = {**key, "mention": route["mention"] or None, "target": "own channel" if route["webhook"] else "main channel", "id": str(uuid.uuid4()), "name": v["name"], "operator": v["operator"], "code": code,
                   "trigger": trigger, "result": result, "detail": detail, "phase": row["currentPhase"], "sentAt": iso(),
                   "channel": cfg.get("slackChannel")}
            await nudges.update_one(key, {"$set": doc, "$inc": {"count": 1}}, upsert=True)
            await store.append_audit("UPGRADE_NUDGE_SENT", "upgrade-notifier", metadata={k: doc[k] for k in ("epoch", "validatorId", "result", "trigger")})
            sent.append(doc)
        return sent

    async def auto_nudge():
        for row in (await alerting["upgrades"]())["requirements"]:
            if row["currentPhase"] in ("upgrade_window", "grace"):
                await send_nudges(row, "window_opened")

    @api.get("/console/upgrades/nudges")
    async def nudges_list():
        rows = await nudges.find({}, {"_id": 0}).sort("sentAt", -1).to_list(200)
        cfg = await alerting["get_config"]()
        return {"nudges": rows, "slackConfigured": bool(cfg["slackWebhookUrl"]), "slackChannel": cfg.get("slackChannel")}

    @api.post("/console/upgrades/{epoch}/nudge")
    async def nudge_epoch(epoch: int, body: NudgeBody):
        row = next((r for r in (await alerting["upgrades"]())["requirements"] if r["epoch"] == epoch), None)
        if not row or row["currentPhase"] == "enforced":
            raise FrasError("INVALID_STATE", "No pending requirement for that epoch")
        return {"sent": await send_nudges(row, "manual", body.resend)}

    # ---------- per-validator Slack routing ----------
    async def routing_rows():
        stored = {r["validatorId"]: r async for r in db.validator_routing.find({}, {"_id": 0})}
        return [{"validatorId": v["validatorId"], "name": v["name"], "slackMemberId": stored.get(v["validatorId"], {}).get("slackMemberId"),
                 "hasWebhook": bool(stored.get(v["validatorId"], {}).get("slackWebhookUrl"))} for v in await all_validators()]

    @api.get("/console/fleet/routing")
    async def routing_list():
        return {"routing": await routing_rows()}

    @api.put("/console/fleet/{vid}/routing")
    async def routing_put(vid: str, body: RoutingBody):
        if vid not in {v["validatorId"] for v in await all_validators()}:
            raise FrasError("INVALID_STATE", "Validator not found")
        upd = body.model_dump(exclude_none=True)
        if upd.get("slackWebhookUrl") and not upd["slackWebhookUrl"].startswith("https://hooks.slack.com/"):
            raise FrasError("INVALID_STATE", "Slack webhook must start with https://hooks.slack.com/")
        upd = {k: (v or None) for k, v in upd.items()}
        await db.validator_routing.update_one({"validatorId": vid}, {"$set": {**upd, "validatorId": vid}}, upsert=True)
        await store.append_audit("VALIDATOR_ROUTING_UPDATED", "validator-admin",
                                 metadata={"validatorId": vid, "slackMemberId": upd.get("slackMemberId"), "webhookChanged": "slackWebhookUrl" in upd})
        return {"routing": await routing_rows()}

    @api.post("/console/fleet/{vid}/routing/test")
    async def routing_test(vid: str):
        cfg = await alerting["get_config"]()
        r = await alerting["routing_for"](vid)
        result, detail = await alerting["post_slack"]({**cfg, "slackWebhookUrl": r["webhook"] or cfg["slackWebhookUrl"]},
                                                     f"{r['mention']} :wave: Frasberg routing test for *{vid}* — operator notifications will arrive here.".strip())
        return {"result": result, "detail": detail, "target": "own channel" if r["webhook"] else "main channel", "mention": r["mention"] or None}

    # ---------- drift alerts (one per validator: drift → warning, breach → critical) ----------
    LABEL = {"uptime": "uptime", "voteParticipation": "votes", "missedBlocks": "missed"}

    async def drift_alerts():
        tr = await fleet_trends("7d")
        out = []
        for v in tr["drift"]:
            bad = [f for f, m in v["metrics"].items() if m["breach"]]
            drifting = [f for f, m in v["metrics"].items() if m["drifting"]]
            flagged = bad or drifting
            value = ", ".join(f"{LABEL[f]} {v['metrics'][f]['last']} ({v['metrics'][f]['change']:+}/7d)" for f in flagged) or "stable"
            out.append({"name": f"ValidatorDrift_{v['validatorId']}", "severity": "critical" if bad else "warning", "service": "fleet",
                        "value": value, "firing": bool(flagged), "validatorId": v["validatorId"], "kind": "breach" if bad else "drift" if drifting else None})
        return out

    alerting["sources"].append(drift_alerts)

    # ---------- Slack OAuth (incoming-webhook) ----------
    @api.get("/console/slack/oauth/start")
    async def slack_oauth_start(origin: str, request: Request):
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if urlparse(origin).scheme != "https" or urlparse(origin).netloc != host:
            raise FrasError("INVALID_STATE", "origin must be this console's https origin")
        state = secrets.token_urlsafe(24)
        redirect_uri = f"{origin.rstrip('/')}/api/console/slack/oauth/callback"
        await db.slack_oauth_states.insert_one({"_id": state, "redirectUri": redirect_uri, "origin": origin.rstrip("/"), "createdAt": now()})
        q = urlencode({"client_id": os.environ["SLACK_CLIENT_ID"], "scope": "incoming-webhook", "redirect_uri": redirect_uri, "state": state})
        return {"url": f"https://slack.com/oauth/v2/authorize?{q}", "redirectUri": redirect_uri}

    @api.get("/console/slack/oauth/callback")
    async def slack_oauth_callback(state: str, code: Optional[str] = None, error: Optional[str] = None):
        st = await db.slack_oauth_states.find_one_and_delete({"_id": state})
        if not st:
            raise FrasError("INVALID_STATE", "Unknown or expired OAuth state")
        created = st["createdAt"] if st["createdAt"].tzinfo else st["createdAt"].replace(tzinfo=timezone.utc)
        back = f"{st['origin']}/alerts"
        if error or not code or now() - created > timedelta(minutes=15):
            return RedirectResponse(f"{back}?slack=error&reason={error or 'expired'}")
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post("https://slack.com/api/oauth.v2.access", data={
                "client_id": os.environ["SLACK_CLIENT_ID"], "client_secret": os.environ["SLACK_CLIENT_SECRET"],
                "code": code, "redirect_uri": st["redirectUri"]})
        data = r.json()
        hook = (data.get("incoming_webhook") or {})
        if not data.get("ok") or not hook.get("url"):
            return RedirectResponse(f"{back}?slack=error&reason={data.get('error', 'no_webhook')}")
        channel = f"{hook.get('channel', '')} · {(data.get('team') or {}).get('name', '')}".strip(" ·")
        await db.settings.update_one({"_id": "alerting"}, {"$set": {"slackWebhookUrl": hook["url"], "slackChannel": channel}}, upsert=True)
        await store.append_audit("SLACK_CONNECTED", "operator", metadata={"channel": channel})
        return RedirectResponse(f"{back}?slack=connected")

    async def loop():
        while True:
            await asyncio.sleep(60)
            for job in (sample_now, maybe_run_schedule, auto_nudge):
                try:
                    await job()
                except Exception:
                    logging.exception("ops job %s failed", job.__name__)

    async def seed():
        await samples.create_index([("v", 1), ("t", 1)])
        await nudges.create_index([("epoch", 1), ("validatorId", 1)], unique=True)
        await db.settings.update_one({"_id": "archive_schedule"}, {"$setOnInsert": {"enabled": True, "timeUtc": "02:00", "olderThanMinutes": 0, "lastRunDate": None}}, upsert=True)
        await backfill()
        asyncio.create_task(loop())

    return seed
