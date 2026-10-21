"""Operational artifacts: Grafana dashboard, Prometheus rules, Alertmanager config, DR runbooks."""

RUNBOOK = "https://runbooks.frasberg.com"


def _p(title, kind, expr):
    return {"title": title, "type": kind, "targets": [{"expr": expr}]}


GRAFANA_DASHBOARD = {
    "title": "Frasberg Mainnet Operations", "timezone": "utc", "schemaVersion": 39, "version": 1, "refresh": "30s",
    "templating": {"list": [{"name": n, "type": "query"} for n in ("network", "validator", "environment", "service")]},
    "panels": [
        _p("Lock Availability", "stat", "lock_availability"),
        _p("Lock Contention %", "timeseries", "100 * rate(rotation_lock_acquire_failed_total[5m]) / (rate(rotation_lock_acquired_total[5m]) + rate(rotation_lock_acquire_failed_total[5m]))"),
        _p("Outbox Pending", "stat", "audit_outbox_pending_total"),
        _p("Dead Letter Queue", "stat", "audit_outbox_dead_letter_total"),
        _p("Outbox Publish Failures", "timeseries", "rate(audit_outbox_publish_failures_total[5m])"),
        _p("Replay Failures", "timeseries", "rate(audit_outbox_replay_failure_total[15m])"),
        _p("Idempotency Hits", "timeseries", "rate(audit_outbox_idempotency_hit_total[5m])"),
        _p("Fencing Violations", "timeseries", "increase(rotation_lock_stale_fence_total[1h])"),
        _p("Chaos Pass Rate", "stat", "100 * rate(chaos_test_pass_total[24h]) / (rate(chaos_test_pass_total[24h]) + rate(chaos_test_fail_total[24h]))"),
        _p("Error Budget Burn", "timeseries", "(rate(rotation_lock_acquire_failed_total[1h]) / (rate(rotation_lock_acquired_total[1h]) + rate(rotation_lock_acquire_failed_total[1h]))) / 0.0005"),
    ],
}

PROMETHEUS_RULES = f"""groups:
- name: frasberg-slo
  rules:
  - record: lock_error_rate
    expr: rate(rotation_lock_acquire_failed_total[5m]) / (rate(rotation_lock_acquired_total[5m]) + rate(rotation_lock_acquire_failed_total[5m]))
  - record: lock_availability
    expr: rate(rotation_lock_acquired_total[30d]) / (rate(rotation_lock_acquired_total[30d]) + rate(rotation_lock_acquire_failed_total[30d]))
  - record: lock_availability_safe
    expr: lock_availability OR on() vector(1)
  - record: outbox_publish_failure_rate
    expr: rate(audit_outbox_publish_failures_total[5m]) / rate(audit_outbox_publish_attempts_total[5m])
  - alert: LockAvailabilityLow
    expr: lock_availability_safe < 0.9995
    for: 5m
    labels: {{severity: critical, service: validator-lock, team: platform, slo: lock-availability}}
    annotations: {{summary: Lock availability below SLO, runbook: "{RUNBOOK}/validator-lock-availability"}}
  - alert: LockErrorBudgetFastBurn
    expr: lock_error_rate > (14.4 * 0.0005)
    for: 5m
    labels: {{severity: critical, service: validator-lock, team: platform, budget: availability}}
    annotations: {{summary: 14.4x burn - budget gone in ~2 days, runbook: "{RUNBOOK}/error-budget"}}
  - alert: LockErrorBudgetMediumBurn
    expr: lock_error_rate > (6 * 0.0005)
    for: 30m
    labels: {{severity: critical, service: validator-lock, team: platform, budget: availability}}
    annotations: {{summary: 6x burn - budget gone in ~5 days, runbook: "{RUNBOOK}/error-budget"}}
  - alert: LockErrorBudgetSlowBurn
    expr: lock_error_rate > (3 * 0.0005)
    for: 6h
    labels: {{severity: warning, service: validator-lock, team: platform, budget: availability}}
    annotations: {{summary: 3x burn - budget gone in ~10 days, runbook: "{RUNBOOK}/error-budget"}}
  - alert: LockContentionRateHigh
    expr: rate(rotation_lock_acquire_failed_total[5m]) / rate(rotation_lock_acquired_total[5m]) > 0.01
    for: 10m
    labels: {{severity: warning, service: validator-lock, team: platform, slo: lock-availability}}
    annotations: {{summary: Validator lock contention exceeded SLO, runbook: "{RUNBOOK}/validator-lock-contention"}}
  - alert: StaleFenceDetected
    expr: increase(rotation_lock_stale_fence_total[15m]) > 0
    labels: {{severity: critical, service: validator-lock, team: security}}
    annotations: {{summary: Stale fencing token rejected, runbook: "{RUNBOOK}/stale-fence"}}
  - alert: OutboxBacklog
    expr: audit_outbox_pending_total > 1000
    for: 15m
    labels: {{severity: warning, service: audit-outbox, team: platform, slo: outbox-delivery}}
    annotations: {{summary: Audit outbox backlog, runbook: "{RUNBOOK}/audit-outbox-replay"}}
  - alert: OutboxErrorBudgetFastBurn
    expr: outbox_publish_failure_rate > (14.4 * 0.0001)
    for: 5m
    labels: {{severity: critical, service: audit-outbox, team: platform, slo: outbox-delivery}}
    annotations: {{summary: Outbox fast burn, runbook: "{RUNBOOK}/audit-outbox-replay"}}
  - alert: AuditDeadLetterGrowth
    expr: increase(audit_outbox_dead_letter_total[1h]) > 0
    labels: {{severity: warning, service: audit-outbox, team: platform, slo: outbox-delivery}}
    annotations: {{summary: Dead letter queue growing, runbook: "{RUNBOOK}/audit-outbox-replay"}}
  - alert: ReplayFailures
    expr: rate(audit_outbox_replay_failure_total[15m]) > 0
    labels: {{severity: warning, service: audit-outbox, team: platform, slo: outbox-delivery}}
    annotations: {{summary: Dead-letter replay failing, runbook: "{RUNBOOK}/audit-outbox-replay"}}
  - alert: LockRenewFailures
    expr: increase(rotation_lock_renew_failure_total[15m]) > 0
    labels: {{severity: critical, service: validator-lock, team: platform}}
    annotations: {{summary: Lock renewals failing, runbook: "{RUNBOOK}/lock-renewal"}}
  - alert: MetricCardinalityViolation
    expr: increase(metric_cardinality_violation_total[15m]) > 0
    labels: {{severity: warning, service: observability, team: platform}}
    annotations: {{summary: Label cardinality violation, runbook: "{RUNBOOK}/cardinality"}}
  - alert: ChaosFailure
    expr: increase(chaos_test_fail_total[1h]) > 0
    labels: {{severity: critical, service: chaos, team: reliability}}
    annotations: {{summary: Chaos scenario failed, runbook: "{RUNBOOK}/chaos"}}
"""

ALERTMANAGER = """route:
  receiver: default
  routes:
  - matchers: ['severity="critical"']
    receiver: pagerduty-critical
  - matchers: ['severity="warning"']
    receiver: slack-platform
  - matchers: ['service="audit-outbox"']
    receiver: audit-team
  - matchers: ['service="validator-lock"']
    receiver: validator-team
  - matchers: ['service="chaos"']
    receiver: reliability-team
receivers:
- name: default
- name: pagerduty-critical
  pagerduty_configs: [{routing_key: '${PAGERDUTY_KEY}'}]
- name: slack-platform
  slack_configs: [{channel: '#platform-alerts'}]
- name: audit-team
  slack_configs: [{channel: '#audit-alerts'}]
- name: validator-team
  slack_configs: [{channel: '#validator-alerts'}]
- name: reliability-team
  slack_configs: [{channel: '#chaos-results'}]
inhibit_rules:
- source_matchers: ['alertname="PostgresUnavailable"']
  target_matchers: ['service="audit-outbox"']
  equal: [environment]
- source_matchers: ['alertname="OutboxBacklog"']
  target_matchers: ['alertname="ReplayFailures"']
  equal: [environment]
- source_matchers: ['alertname="LockAvailabilityLow"']
  target_matchers: ['alertname=~"ErrorBudget.*"']
  equal: [service, environment]
- source_matchers: ['alertname="DeploymentInProgress"']
  target_matchers: ['service="chaos"']
  equal: [environment]
"""

RUNBOOKS = [
    {"id": "DR-001", "title": "Audit Chain Recovery", "steps": ["GET /api/console/audit/verify", "Inspect brokenAt sequence", "Restore from WORM archive", "Re-verify: no broken hashes, no sequence gaps"]},
    {"id": "DR-002", "title": "Outbox Recovery", "steps": ["Check backlog (pending count)", "POST /api/console/outbox/recover-claims", "POST /api/console/outbox/replay {all:true}", "Verify no duplicate event_id, backlog < 100"]},
    {"id": "DR-003", "title": "Lock Recovery", "steps": ["Inspect active locks", "Expire stale locks", "Verify fencing counter monotonic (rotation_lock_token_max)"]},
    {"id": "DR-004", "title": "Key-Rotation Recovery", "steps": ["Freeze rotations (L4 safe mode if needed)", "Verify KEY_ROTATION_* audit events", "Roll back rotation if previous key not revoked"]},
]
