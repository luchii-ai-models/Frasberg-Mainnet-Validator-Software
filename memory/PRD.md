# Frasberg Mainnet Validator Software — PRD

## Original problem statement
"Use my info and build Frasberg Mainnet Validator Software" — a full spec covering required validator versions per epoch, validator stack, health requirements, delegation eligibility, version endpoints, Version Policy v1.0 (supported/deprecated/required/unsupported), semver + explicit release channels, Ed25519 canonical-JSON signed policies, JSON schemas & negative contract tests, key rotation/revocation/rollback, immutable hash-chained audit log, outbox (retry, dead-letter, replay, dedup, crash recovery), distributed locks with fencing tokens, metrics/SLOs/burn-rate alerts, Grafana/Prometheus/Alertmanager artifacts, chaos tests, DR runbooks, rollback approval gates (L1–L4), audit retention, error envelope/HTTP mapping and a mainnet readiness gate.

## User choices
- Validator console + live API
- MongoDB for everything (audit log, outbox, locks + fencing) — no Postgres/Redis
- Identity: fras_val_001, validator 6.4.1, Luchii 4.2.0, epoch 900, 5% commission
- No login (open console)
- Built-in test runner in UI

## Architecture
- Backend FastAPI: `core.py` (pure logic), `store.py` (Mongo stores), `selftests.py` (72 built-in tests incl. seeded property tests with shrinking + chaos), `artifacts.py` (Grafana JSON, Prometheus rules, Alertmanager, runbooks), `server.py` (routes)
- Public: `/api/v1/validator/{version,status,compliance}`, `/api/v1/network/{required-versions,schedule}`, `/api/metrics`
- Console: `/api/console/*` (overview, validator, settings, schedule, keys, policies, audit, outbox, locks, metrics, artifacts, rollbacks, tests, readiness)
- Frontend React: 11 pages (Overview, Required Versions, Signed Policies, Signing Keys, Locks & Fencing, Audit Chain, Outbox, Metrics & SLO, Rollbacks, Test Runner, Readiness Gate); dark tactical theme

## Implemented (2026-06)
- All spec endpoints, compliance codes, version classes, grace period, delegation enforcement
- Ed25519 signing, keyId-based verification, KEY_UNKNOWN/KEY_REVOKED/INVALID_SIGNATURE/INVALID_POLICY
- Fenced key rotation via Mongo lock (SET NX PX semantics), atomic `fencing_token < $token` updates
- Append-only audit chain (optimistic sequence + unique index), simulated tamper verification
- Outbox: lease claims, retry 0/60/300/900/3600s, dead-letter, idempotent replay with claim, consumer dedup on event_id, failure injection modes
- Rollback L1–L4 with approvals, self-approval/duplicate protection, governance freeze, audit trail
- Windowed SLOs (5m contention, 1h availability), alert evaluation, reset SLO window action
- Readiness GO/NO-GO (17 checks)

## Notes / limitations
- Mongo is standalone (no replica set): multi-doc transactions emulated via ordering + atomic single-doc ops
- Event bus is SIMULATED in MongoDB; Ed25519 private keys stored in Mongo (demo — use KMS/HSM in production)

## Backlog
- P1: Multi-validator fleet view; WORM archive export of audit partitions
- P1: Real Prometheus/Grafana push; Alertmanager webhook delivery
- P2: Governance proposals/voting package; split selftests by suite
