"""Pure Frasberg validator logic: semver, channels, compliance, canonical signing, schemas, hash chain, fencing, rollback rules."""
import base64
import hashlib
import json
import re
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from jsonschema import Draft202012Validator, FormatChecker


class FrasError(Exception):
    def __init__(self, code, message=None):
        super().__init__(code)
        self.code = code
        self.message = message or ERROR_MAP.get(code, (500, code))[1]


ERROR_MAP = {
    "INVALID_LEVEL": (400, "Invalid rollback level."),
    "INVALID_TICKET": (400, "Ticket is required."),
    "INVALID_REASON": (400, "Reason must be 10-500 characters."),
    "INVALID_TARGET_VERSION": (400, "Target version is invalid."),
    "INCIDENT_REQUIRED": (400, "Active incident is required."),
    "INVALID_POLICY": (400, "Policy document failed schema validation."),
    "INVALID_VERSION": (400, "Version is not valid semver."),
    "INVALID_ROLLBACK_DIRECTION": (400, "Cannot roll back forward."),
    "INVALID_LABEL": (400, "Metric label not allowed."),
    "TARGET_VERSION_UNKNOWN": (404, "Target version not found."),
    "KEY_UNKNOWN": (404, "Signing key not found."),
    "DEAD_LETTER_NOT_FOUND": (404, "Dead-letter event not found."),
    "ROLLBACK_NOT_FOUND": (404, "Rollback request not found."),
    "INSUFFICIENT_APPROVALS": (403, "Insufficient approvals."),
    "SELF_APPROVAL_FORBIDDEN": (403, "Requester cannot approve own rollback."),
    "GOVERNANCE_FREEZE": (403, "Governance freeze blocks this rollback."),
    "SAFE_MODE": (403, "Network safe mode: writes disabled."),
    "KEY_REVOKED": (403, "Signing key is revoked."),
    "KEY_NOT_ACTIVE": (403, "Signing key cannot sign."),
    "ROLLBACK_NOT_ALLOWED": (409, "Rollback not allowed: previous key revoked."),
    "ROTATION_IN_PROGRESS": (409, "Rotation in progress."),
    "STALE_FENCING_TOKEN": (409, "Stale fencing token rejected."),
    "LOCK_NOT_OWNER": (409, "Lock is owned by another node."),
    "LOCK_EXPIRED": (409, "Lock lease expired."),
    "LOCK_RENEWAL_FAILED": (503, "Lock renewal failed."),
    "REPLAY_IN_PROGRESS": (409, "Replay already claimed by another worker."),
    "DUPLICATE_APPROVAL": (409, "Approver already approved."),
    "INVALID_STATE": (409, "Invalid state transition."),
    "AUTH_REQUIRED": (401, "Sign in to continue."),
    "SESSION_EXPIRED": (401, "Your session has expired. Sign in again."),
    "INVALID_CREDENTIALS": (401, "Email or password is incorrect."),
    "ACCOUNT_LOCKED": (429, "Too many failed attempts. Try again later."),
    "PASSWORD_CHANGE_REQUIRED": (403, "Change your temporary password to continue."),
    "ADMIN_REQUIRED": (403, "Only admins can do this."),
    "WEAK_PASSWORD": (400, "Password needs at least 8 characters, including a letter and a number."),
    "USER_NOT_FOUND": (404, "Operator not found."),
    "API_KEY_MISSING": (401, "Send an API key in the X-API-Key header."),
    "API_KEY_INVALID": (401, "API key is not valid."),
    "API_KEY_REVOKED": (401, "API key has been revoked."),
    "API_KEY_DISABLED": (401, "API key is disabled."),
    "INSUFFICIENT_SCOPE": (403, "API key scope does not allow this request."),
    "API_KEY_NOT_FOUND": (404, "API key not found."),
    "ROTATION_FAILED": (500, "Rotation failed; no completion recorded."),
    "INTERNAL_ERROR": (500, "Unexpected internal error."),
}


def error_envelope(request_id, code, message=None):
    return {"success": False, "requestId": request_id,
            "error": {"code": code, "message": message or ERROR_MAP.get(code, (500, code))[1]}}


# ---------- semver ----------
_IDENT = r"(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
SEMVER_PATTERN = (r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
                  rf"(?:-({_IDENT}(?:\.{_IDENT})*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")
SEMVER_RE = re.compile(SEMVER_PATTERN)
CHANNELS = ["stable", "rc", "beta", "alpha"]


def parse_semver(v):
    m = SEMVER_RE.match(v) if isinstance(v, str) else None
    if not m:
        return None
    pre = m.group(4).split(".") if m.group(4) else []
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), pre


def semver_valid(v):
    return parse_semver(v) is not None


def _cmp_ident(a, b):
    an, bn = a.isdigit(), b.isdigit()
    if an and bn:
        return (int(a) > int(b)) - (int(a) < int(b))
    if an != bn:
        return -1 if an else 1
    return (a > b) - (a < b)


def semver_cmp(a, b):
    pa, pb = parse_semver(a), parse_semver(b)
    if pa is None or pb is None:
        raise FrasError("INVALID_VERSION")
    for x, y in zip(pa[:3], pb[:3]):
        if x != y:
            return 1 if x > y else -1
    ra, rb = pa[3], pb[3]
    if not ra and not rb:
        return 0
    if not ra:
        return 1
    if not rb:
        return -1
    for x, y in zip(ra, rb):
        c = _cmp_ident(x, y)
        if c:
            return c
    return (len(ra) > len(rb)) - (len(ra) < len(rb))


def compare_versions(a, b):
    """Spec comparator: numeric MAJOR.MINOR.PATCH only."""
    ap = [int(x) if x.isdigit() else 0 for x in a.split("-")[0].split(".")]
    bp = [int(x) if x.isdigit() else 0 for x in b.split("-")[0].split(".")]
    for i in range(3):
        left = ap[i] if i < len(ap) else 0
        right = bp[i] if i < len(bp) else 0
        if left != right:
            return 1 if left > right else -1
    return 0


def within_max(version, max_pattern):
    """'6.x' / '4.9.x' style ceilings."""
    p = parse_semver(version)
    if not p or not max_pattern:
        return bool(p)
    parts = max_pattern.split(".")
    nums = list(p[:3])
    for i, part in enumerate(parts):
        if part == "x":
            return True
        if nums[i] != int(part):
            return nums[i] < int(part)
    return True


def channel_of(version):
    p = parse_semver(version)
    if not p or not p[3]:
        return "stable"
    tag = p[3][0].lower()
    return tag if tag in CHANNELS else "alpha"


def validate_version(current, minimum, required_channel, current_channel):
    if not semver_valid(current):
        return {"compliant": False, "code": "INVALID_VERSION"}
    if not semver_valid(minimum) or semver_cmp(current, minimum) < 0:
        return {"compliant": False, "code": "VERSION_TOO_OLD"}
    if required_channel != current_channel:
        return {"compliant": False, "code": "INVALID_CHANNEL"}
    return {"compliant": True, "code": "COMPLIANT"}


def is_validator_compliant(validator, required):
    vp = compare_versions(validator["validatorVersion"], required["validator"]["minimum"]) >= 0
    lp = compare_versions(validator["luchiiVersion"], required["luchii"]["minimum"]) >= 0
    return {"compliant": vp and lp, "validatorPass": vp, "luchiiPass": lp}


def version_class(version, req):
    """Supported / Deprecated / Required / Unsupported per Version Policy v1.0."""
    v = req["validator"]
    if not semver_valid(version) or semver_cmp(version, v["minimum"]) < 0 or not within_max(version, v.get("maximum")):
        return "unsupported"
    if semver_cmp(version, v.get("recommended", v["minimum"])) >= 0:
        return "supported"
    if compare_versions(version, v["minimum"]) == 0:
        return "required"
    return "deprecated"


CLASS_EFFECTS = {
    "supported": {"delegation": "eligible", "voting": True, "rewards": True, "warning": False},
    "deprecated": {"delegation": "eligible", "voting": True, "rewards": True, "warning": True},
    "required": {"delegation": "required", "voting": True, "rewards": True, "warning": False},
    "unsupported": {"delegation": "removed", "voting": False, "rewards": False, "warning": True},
}

HEALTH_REQUIREMENTS = {"uptime": 99.9, "voteParticipation": 95.0, "missedBlocks": 3.0, "apiAvailability": 99.5}
DELEGATION_CRITERIA = {"maxCommission": 5, "minStake": 100000}


def health_checks(v):
    return {
        "uptime": v["uptime"] >= HEALTH_REQUIREMENTS["uptime"],
        "voteParticipation": v["voteParticipation"] >= HEALTH_REQUIREMENTS["voteParticipation"],
        "missedBlocks": v["missedBlocks"] <= HEALTH_REQUIREMENTS["missedBlocks"],
        "apiAvailability": v["apiAvailability"] >= HEALTH_REQUIREMENTS["apiAvailability"],
    }


def evaluate_compliance(v, req):
    vr = validate_version(v["validatorVersion"], req["validator"]["minimum"], req["validator"].get("channel", "stable"), v["channel"])
    lr = validate_version(v["luchiiVersion"], req["luchii"]["minimum"], "stable", channel_of(v["luchiiVersion"]))
    cls = version_class(v["validatorVersion"], req) if vr["compliant"] else "unsupported"
    within_max_ok = within_max(v["validatorVersion"], req["validator"].get("maximum"))
    version_ok = vr["compliant"] and lr["compliant"] and within_max_ok
    grace = (not version_ok) and v["epoch"] < req["enforcement"]["graceEndsEpoch"]
    health = health_checks(v)
    healthy = all(health.values())
    criteria = {
        "versionCompliance": version_ok,
        "healthCompliance": healthy,
        "securityCompliance": v["securityScanPassed"],
        "identityCompliance": v["identityVerified"],
        "governanceStanding": v["governanceStanding"],
        "maxCommission": v["commission"] <= DELEGATION_CRITERIA["maxCommission"],
        "minStake": v["stake"] >= DELEGATION_CRITERIA["minStake"],
    }
    eligible = all(criteria.values()) and not v.get("frozen", False)
    code = vr["code"] if not vr["compliant"] else (lr["code"] if not lr["compliant"] else ("COMPLIANT" if within_max_ok else "VERSION_TOO_NEW"))
    enabled = version_ok or grace
    return {
        "compliant": version_ok, "code": code, "versionClass": cls, "effects": CLASS_EFFECTS[cls],
        "inGracePeriod": grace, "health": health, "healthy": healthy, "criteria": criteria,
        "delegationEligible": eligible, "rewardsEnabled": enabled and eligible, "votingEnabled": enabled and v["governanceStanding"],
        "validator": {"current": v["validatorVersion"], "required": req["validator"]["minimum"], "code": vr["code"]},
        "luchii": {"current": v["luchiiVersion"], "required": req["luchii"]["minimum"], "code": lr["code"]},
    }


# ---------- canonical JSON + Ed25519 ----------
def canonicalize(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def b64(b):
    return base64.b64encode(b).decode()


def generate_keypair():
    sk = Ed25519PrivateKey.generate()
    priv = sk.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64(priv), b64(pub)


def sign_policy(policy, private_b64):
    canonical = canonicalize(policy)
    sk = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return b64(sk.sign(canonical.encode("utf-8")))


def verify_policy(policy, signature_b64, public_b64):
    try:
        canonical = canonicalize(policy)
        pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
        pk.verify(base64.b64decode(signature_b64), canonical.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ---------- JSON schemas ----------
_fmt = FormatChecker(formats=[])


@_fmt.checks("date-time", raises=ValueError)
def _is_datetime(v):
    if not isinstance(v, str):
        return True
    if "T" not in v:
        raise ValueError("no T")
    datetime.fromisoformat(v.replace("Z", "+00:00"))
    return True


CHANNEL_ENUM = {"enum": CHANNELS}
SEMVER_STR = {"type": "string", "pattern": SEMVER_PATTERN}

VALIDATOR_POLICY_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://schemas.frasberg.com/validator-policy.json",
    "type": "object", "additionalProperties": False,
    "required": ["policyId", "version", "network", "epoch", "requirements", "publishedAt"],
    "properties": {
        "policyId": {"type": "string", "minLength": 1},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
        "network": {"type": "string"},
        "epoch": {"type": "integer", "minimum": 0},
        "publishedAt": {"type": "string", "format": "date-time"},
        "requirements": {
            "type": "object", "additionalProperties": False,
            "required": ["validatorMin", "validatorRecommended", "validatorChannel", "luchiiMin"],
            "properties": {"validatorMin": SEMVER_STR, "validatorRecommended": SEMVER_STR,
                           "validatorChannel": CHANNEL_ENUM, "luchiiMin": SEMVER_STR},
        },
    },
}

VALIDATOR_VERSION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "required": ["validatorId", "validatorVersion", "channel", "luchiiVersion", "epoch"],
    "properties": {"validatorId": {"type": "string"}, "validatorVersion": {"type": "string"},
                   "channel": CHANNEL_ENUM, "luchiiVersion": {"type": "string"}, "epoch": {"type": "integer"},
                   "network": {"type": "string"}, "coreVersion": {"type": "string"}, "runtimeVersion": {"type": "string"}},
}

REQUIRED_VERSIONS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object", "additionalProperties": False,
    "required": ["network", "epoch", "validator", "luchii", "enforcement", "publishedAt"],
    "properties": {
        "network": {"type": "string"},
        "epoch": {"type": "integer", "minimum": 0},
        "validator": {"type": "object", "additionalProperties": False, "required": ["minimum", "channel"],
                      "properties": {"minimum": SEMVER_STR, "recommended": SEMVER_STR,
                                     "maximum": {"type": "string", "pattern": r"^\d+(\.(\d+|x)){0,2}(\.x)?$"},
                                     "channel": CHANNEL_ENUM}},
        "luchii": {"type": "object", "additionalProperties": False, "required": ["minimum"],
                   "properties": {"minimum": SEMVER_STR, "recommended": SEMVER_STR,
                                  "maximum": {"type": "string"}}},
        "core": {"type": "object"},
        "enforcement": {"type": "object", "additionalProperties": False, "required": ["startsEpoch", "graceEndsEpoch"],
                        "properties": {"startsEpoch": {"type": "integer", "minimum": 0},
                                       "graceEndsEpoch": {"type": "integer", "minimum": 0}}},
        "publishedAt": {"type": "string", "format": "date-time"},
        "policyId": {"type": "string"},
        "signature": {"type": "object"},
    },
}

SCHEMAS = {"validator-policy": VALIDATOR_POLICY_SCHEMA, "validator-version": VALIDATOR_VERSION_SCHEMA,
           "required-versions": REQUIRED_VERSIONS_SCHEMA}
_validators = {k: Draft202012Validator(s, format_checker=_fmt) for k, s in SCHEMAS.items()}


def schema_errors(name, doc):
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in _validators[name].iter_errors(doc)]


def schema_valid(name, doc):
    return not schema_errors(name, doc)


def validate_response(name, doc):
    errs = schema_errors(name, doc)
    if errs:
        raise FrasError("INVALID_POLICY", "; ".join(errs[:3]))
    return True


# ---------- audit hash chain ----------
GENESIS = "GENESIS"


def hash_audit_record(previous_hash, payload):
    return hashlib.sha256(previous_hash.encode() + canonicalize(payload).encode()).hexdigest()


def verify_audit_chain(records, start_prev=GENESIS, start_seq=1):
    prev = start_prev
    expected_seq = start_seq
    for i, r in enumerate(records):
        if r["sequence"] != expected_seq:
            return {"valid": False, "code": "SEQUENCE_GAP", "brokenAt": i, "sequence": r["sequence"]}
        if r["previous_hash"] != prev:
            return {"valid": False, "code": "BROKEN_LINKAGE", "brokenAt": i, "sequence": r["sequence"]}
        pl = r["payload"] if isinstance(r.get("payload"), dict) else {}
        if any(k in r and r[k] != pl.get(pk) for k, pk in (("event_id", "id"), ("event_type", "type"), ("actor", "actor"), ("timestamp", "timestamp"))):
            return {"valid": False, "code": "FIELD_MISMATCH", "brokenAt": i, "sequence": r["sequence"]}
        if hash_audit_record(r["previous_hash"], r["payload"]) != r["current_hash"]:
            return {"valid": False, "code": "HASH_MISMATCH", "brokenAt": i, "sequence": r["sequence"]}
        prev = r["current_hash"]
        expected_seq += 1
    return {"valid": True, "code": "CHAIN_VALID", "length": len(records), "head": prev}


# ---------- fencing ----------
def validate_fence(current_token, supplied_token):
    return supplied_token >= current_token


def ensure_fence(current_token, incoming_token):
    if incoming_token < current_token:
        raise FrasError("STALE_FENCING_TOKEN")


SIGNING_RULES = {"active": (True, True), "rotating": (False, True), "retired": (False, True), "revoked": (False, False)}


def can_sign(status):
    return SIGNING_RULES.get(status, (False, False))[0]


def can_verify(status):
    return SIGNING_RULES.get(status, (False, False))[1]


# ---------- outbox retry ----------
RETRY_SECONDS = [0, 60, 300, 900, 3600]
MAX_RETRIES = 5


def next_retry_delay(retry_count):
    return RETRY_SECONDS[min(retry_count, len(RETRY_SECONDS) - 1)]


# ---------- rollback ----------
ROLLBACK_LEVELS = ["L1", "L2", "L3", "L4"]
REQUIRED_APPROVALS = {"L1": 0, "L2": 1, "L3": 2, "L4": 3}
ROLLBACK_LEVEL_INFO = {
    "L1": "Freeze validators & registrations (automatic)",
    "L2": "Freeze governance changes (1 approval)",
    "L3": "Roll back validator software (2 approvals)",
    "L4": "Network safe mode (3 approvals)",
}


def validate_rollback(req, known_versions, current_version):
    if req.get("level") not in ROLLBACK_LEVELS:
        raise FrasError("INVALID_LEVEL")
    if not req.get("ticketId"):
        raise FrasError("INVALID_TICKET")
    if not req.get("incidentId"):
        raise FrasError("INCIDENT_REQUIRED")
    reason = req.get("reason") or ""
    if not (10 <= len(reason) <= 500):
        raise FrasError("INVALID_REASON")
    target = req.get("targetVersion")
    if not isinstance(target, str) or not re.match(r"^\d+\.\d+\.\d+$", target):
        raise FrasError("INVALID_TARGET_VERSION")
    if target not in known_versions:
        raise FrasError("TARGET_VERSION_UNKNOWN")
    if semver_cmp(target, current_version) >= 0:
        raise FrasError("INVALID_ROLLBACK_DIRECTION")
    return True


def validate_rollback_target(target, current=None, known_versions=None):
    if known_versions is not None and target not in known_versions:
        raise FrasError("TARGET_VERSION_UNKNOWN")
    if current and semver_cmp(target, current) >= 0:
        raise FrasError("INVALID_ROLLBACK_DIRECTION")
    return True


def validate_approvals(level, approvals, requester=None):
    if level not in REQUIRED_APPROVALS:
        raise FrasError("INVALID_LEVEL")
    if requester and requester in approvals:
        raise FrasError("SELF_APPROVAL_FORBIDDEN")
    if len(set(approvals)) < REQUIRED_APPROVALS[level]:
        raise FrasError("INSUFFICIENT_APPROVALS")
    return True


def validate_rollback_history(history, level="L2"):
    if not history or history[0] != "ROLLBACK_REQUESTED":
        raise FrasError("INVALID_STATE", "ROLLBACK_REQUESTED must be first")
    approvals = 0
    executed = False
    for ev in history[1:]:
        if ev == "ROLLBACK_APPROVED":
            approvals += 1
        elif ev == "ROLLBACK_EXECUTED":
            if approvals < REQUIRED_APPROVALS[level]:
                raise FrasError("INSUFFICIENT_APPROVALS")
            executed = True
        elif ev == "ROLLBACK_VERIFIED" and not executed:
            raise FrasError("INVALID_STATE", "ROLLBACK_VERIFIED requires execution")
    if not executed:
        raise FrasError("INSUFFICIENT_APPROVALS", "Lifecycle never reached ROLLBACK_EXECUTED")
    return True


# ---------- retention ----------
RETENTION = {}
for _e in ["KEY_CREATED", "KEY_ROTATION_STARTED", "KEY_ROTATION_COMPLETED", "KEY_ROTATION_ROLLED_BACK", "KEY_ROTATED",
           "KEY_REVOKED", "POLICY_SIGNED", "POLICY_VERIFIED",
           "ROLLBACK_REQUESTED", "ROLLBACK_APPROVED", "ROLLBACK_REJECTED", "ROLLBACK_EXECUTED", "ROLLBACK_VERIFIED",
           "ROLLBACK_VALIDATION_FAILED", "ROLLBACK_APPROVAL_DENIED",
           "APPROVAL_DENIED", "SELF_APPROVAL_FORBIDDEN", "SIGNATURE_FAILURE", "STALE_FENCING_TOKEN"]:
    RETENTION[_e] = "FOREVER"
RETENTION["AUDIT_ARCHIVED"] = "FOREVER"
for _e in ["CHAOS_TEST", "READINESS_CHECK", "DEPLOYMENT_EVENT", "VALIDATOR_UPDATED", "TEST_RUN"]:
    RETENTION[_e] = "7Y"


def retention(event_type):
    return RETENTION.get(event_type, "7Y")


# ---------- metric labels ----------
ALLOWED_LABELS = ["service", "environment", "network", "event_type", "status", "result", "channel"]


def validate_labels(labels):
    for k in labels:
        if k not in ALLOWED_LABELS:
            raise FrasError("INVALID_LABEL", f"INVALID_LABEL:{k}")
    return True


def validate_label_value_count(n):
    if n > 100:
        raise FrasError("LABEL_CARDINALITY_LIMIT", "LABEL_CARDINALITY_LIMIT")
    return True


# ---------- alerting ----------
ALERT_THRESHOLDS = {
    "LockAvailabilityLow": ("lt", 0.9995, "critical", "validator-lock"),
    "OutboxBacklog": ("gt", 1000, "warning", "audit-outbox"),
    "ErrorBudgetFastBurn": ("gt", 14.4, "critical", "validator-lock"),
    "ErrorBudgetMediumBurn": ("gt", 6, "critical", "validator-lock"),
    "ErrorBudgetSlowBurn": ("gt", 3, "warning", "validator-lock"),
    "ReplayFailures": ("gt", 0, "warning", "audit-outbox"),
    "StaleFenceDetected": ("gt", 0, "critical", "validator-lock"),
    "AuditDeadLetterGrowth": ("gt", 0, "warning", "audit-outbox"),
    "LockContentionRateHigh": ("gt", 0.01, "warning", "validator-lock"),
    "ChaosFailure": ("gt", 0, "critical", "chaos"),
}


def alert_fires(name, value):
    if value is None:
        return False
    op, thr, _, _ = ALERT_THRESHOLDS[name]
    return value < thr if op == "lt" else value > thr


INHIBITION_RULES = [
    {"source": "PostgresUnavailable", "target_service": "audit-outbox", "equal": ["environment"]},
    {"source": "OutboxBacklog", "target_alert": "ReplayFailures", "equal": ["environment"]},
    {"source": "LockAvailabilityLow", "target_prefix": "ErrorBudget", "equal": ["service", "environment"]},
    {"source": "DeploymentInProgress", "target_service": "chaos", "equal": ["environment"]},
]


def is_inhibited(source_alert, target_alert, source_labels, target_labels=None):
    target_labels = target_labels if target_labels is not None else source_labels
    t_service = ALERT_THRESHOLDS.get(target_alert, (None, None, None, target_labels.get("service")))[3]
    s_service = ALERT_THRESHOLDS.get(source_alert, (None, None, None, source_labels.get("service")))[3]
    for r in INHIBITION_RULES:
        if r["source"] != source_alert:
            continue
        match = (r.get("target_alert") == target_alert or r.get("target_service") == t_service
                 or (r.get("target_prefix") and target_alert.startswith(r["target_prefix"])))
        if not match:
            continue
        sl = {**source_labels, "service": s_service}
        tl = {**target_labels, "service": t_service}
        if all(sl.get(k) == tl.get(k) for k in r["equal"]):
            return True
    return False
