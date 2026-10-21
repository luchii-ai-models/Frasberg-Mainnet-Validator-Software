import { useState } from "react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Check, Dot, Json, PageHeader, Panel, Pill, Stat, useLoad } from "@/components/kit";
import { API, call, pct } from "@/lib/api";

const SLOS = [["Validator Lock", "99.95%"], ["Audit Pipeline", "99.99%"], ["Policy Verification", "99.99%"], ["Outbox Publisher", "99.99%"], ["Validator Compliance", "99.95%"]];

export default function Metrics() {
  const [m, reload] = useLoad("/console/metrics");
  const [a] = useLoad("/console/artifacts");
  const [tab, setTab] = useState("grafana");
  if (!m) return null;
  const burnTone = { healthy: "ok", warning: "warn", high: "warn", critical: "bad" }[m.burnLevel];
  const bars = [
    { n: "acquired", v: m.lockAcquired }, { n: "denied", v: m.lockDenied }, { n: "expired", v: m.lockExpired },
    { n: "stale fence", v: m.staleFence }, { n: "published", v: m.publishSuccess }, { n: "failures", v: m.publishFailures },
    { n: "retries", v: m.retries }, { n: "dead letter", v: m.deadLetterTotal }, { n: "replayed", v: m.replaySuccess }, { n: "dedup hits", v: m.idempotencyHits },
  ];
  return (
    <div>
      <PageHeader kicker="low-cardinality · multi-window burn rate" title="Metrics & SLO">
        <a href={`${API}/metrics`} target="_blank" rel="noreferrer" data-testid="prometheus-link"><Pill tone="info">/api/metrics (prometheus)</Pill></a>
        <Button data-testid="reset-slo-window-button" size="sm" variant="secondary" onClick={async () => { await call("post", "/console/metrics/reset-window", null, "SLO window reset (audited)"); reload(); }}>Reset SLO window</Button>
      </PageHeader>
      <div className="font-mono text-[11px] text-slate-500 -mt-4 mb-6" data-testid="slo-window-info">
        contention window 5m: {m.window5m.acquired} acquired / {m.window5m.denied} denied · availability window 1h: {m.window1h.acquired} ok / {m.window1h.error} backend errors · lifetime denied {m.lockDenied}
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-4 mb-6">
        <Stat label="Lock availability" value={pct(m.lockAvailability, 3)} tone={m.lockAvailability >= 0.9995 ? "ok" : "bad"} sub="SLO 99.95%" testId="metric-lock-availability" />
        <Stat label="Contention" value={pct(m.contentionRate)} tone={m.contentionRate < 0.01 ? "ok" : "warn"} sub="SLO < 1%" testId="metric-contention" />
        <Stat label="Burn rate" value={`${m.burnRate.toFixed(2)}x`} tone={burnTone} sub={m.burnLevel} testId="metric-burn-rate" />
        <Stat label="Error budget" value={pct(m.errorBudgetRemaining, 1)} tone={m.errorBudgetRemaining > 0.5 ? "ok" : "bad"} sub="remaining" />
        <Stat label="Outbox success" value={pct(m.outboxSuccessRate)} tone={m.outboxSuccessRate >= 0.9999 ? "ok" : "warn"} sub="SLO 99.99%" />
        <Stat label="Chaos pass" value={m.chaosPassRate === null ? "—" : pct(m.chaosPassRate, 0)} tone={m.chaosFail ? "bad" : "ok"} sub={`${m.chaosPass}/${m.chaosPass + m.chaosFail}`} />
      </div>
      <div className="grid lg:grid-cols-3 gap-6 mb-6">
        <Panel title="Counters" className="lg:col-span-2" testId="metrics-chart">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={bars}>
                <XAxis dataKey="n" tick={{ fill: "#64748B", fontSize: 10, fontFamily: "JetBrains Mono" }} interval={0} angle={-20} height={50} />
                <YAxis tick={{ fill: "#64748B", fontSize: 10 }} allowDecimals={false} />
                <Tooltip contentStyle={{ background: "#0F131D", border: "1px solid #1E2638", fontFamily: "JetBrains Mono", fontSize: 11 }} cursor={{ fill: "#161B29" }} />
                <Bar dataKey="v" fill="#00F0FF" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
        <Panel title="Alert Evaluation" testId="alerts-panel">
          {m.alerts.map((al) => (
            <div key={al.name} className="flex items-center justify-between py-1.5 border-b border-[#1E2638] last:border-0" data-testid={`alert-${al.name}`}>
              <div className="flex items-center gap-2 text-sm"><Dot tone={al.firing ? "bad" : "ok"} />{al.name}</div>
              <Pill tone={al.firing ? "bad" : "muted"}>{al.firing ? `firing · ${al.severity}` : "ok"}</Pill>
            </div>
          ))}
        </Panel>
      </div>
      <div className="grid lg:grid-cols-3 gap-6">
        <Panel title="Production SLO Matrix" testId="slo-matrix">
          {SLOS.map(([s, v]) => <Check key={s} ok label={s} detail={v} />)}
          <div className="mt-4 font-mono text-[11px] text-slate-500 leading-6">Burn: 1h &gt;10% alert · 6h &gt;25% escalate · 24h &gt;50% freeze deploys · 100% reliability-only<br />Thresholds: 3x warn · 6x high · 14.4x critical</div>
        </Panel>
        <Panel title="Operational Artifacts" className="lg:col-span-2" testId="artifacts-panel">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="bg-[#04060A]">
              {["grafana", "prometheus", "alertmanager", "runbooks", "errors"].map((t) => <TabsTrigger key={t} value={t} data-testid={`artifact-tab-${t}`} className="font-mono text-xs">{t}</TabsTrigger>)}
            </TabsList>
            <TabsContent value="grafana"><Json value={a?.grafana} /></TabsContent>
            <TabsContent value="prometheus"><Json value={a?.prometheusRules} /></TabsContent>
            <TabsContent value="alertmanager"><Json value={a?.alertmanager} /></TabsContent>
            <TabsContent value="runbooks"><Json value={a?.runbooks} /></TabsContent>
            <TabsContent value="errors"><Json value={a?.errorMap} /></TabsContent>
          </Tabs>
        </Panel>
      </div>
    </div>
  );
}
