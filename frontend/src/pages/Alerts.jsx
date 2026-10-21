import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dot, Field, PageHeader, Panel, Pill, Stat, useLoad } from "@/components/kit";
import { call } from "@/lib/api";

export default function Alerts() {
  const [d, reload] = useLoad("/console/alerts");
  const [slack, setSlack] = useState("");
  const [pd, setPd] = useState("");
  if (!d) return null;
  const run = async (method, url, body, msg) => { await call(method, url, body, msg); reload(); };
  const saveCfg = () => {
    const body = {};
    if (slack) body.slackWebhookUrl = slack;
    if (pd) body.pagerdutyRoutingKey = pd;
    run("put", "/console/alerts/config", body, "Alert destinations saved").then(() => { setSlack(""); setPd(""); });
  };
  const firing = d.alerts.filter((a) => a.firing).length;
  const live = d.config.slackConfigured || d.config.pagerdutyConfigured;
  return (
    <div>
      <PageHeader kicker="Alertmanager routes · inhibition · dedup" title="Alert Delivery">
        <Button data-testid="alerts-test-button" variant="secondary" onClick={() => run("post", "/console/alerts/test", null, "Test alerts routed")}>Send test alerts</Button>
        <Button data-testid="alerts-dispatch-button" onClick={() => run("post", "/console/alerts/dispatch", null, "Alerts evaluated & dispatched")}>Evaluate & dispatch now</Button>
      </PageHeader>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Stat label="Firing" value={firing} tone={firing ? "bad" : "ok"} testId="alerts-firing-count" />
        <Stat label="Slack" value={d.config.slackConfigured ? "LIVE" : "DRY-RUN"} tone={d.config.slackConfigured ? "ok" : "warn"} testId="alerts-slack-status" />
        <Stat label="PagerDuty" value={d.config.pagerdutyConfigured ? "LIVE" : "DRY-RUN"} tone={d.config.pagerdutyConfigured ? "ok" : "warn"} testId="alerts-pagerduty-status" />
        <Stat label="Auto dispatch" value={d.config.autoDispatch ? "60s" : "off"} tone={d.config.autoDispatch ? "info" : "muted"} />
      </div>
      <div className="grid lg:grid-cols-3 gap-6 mb-6">
        <Panel title="Destinations" testId="alerts-config">
          <div className="space-y-3">
            <Field label="slack incoming webhook"><Input data-testid="alerts-slack-input" type="password" placeholder={d.config.slackConfigured ? "configured — paste to replace" : "https://hooks.slack.com/services/…"} value={slack} onChange={(e) => setSlack(e.target.value)} className="font-mono text-xs bg-[#04060A]" /></Field>
            <Field label="pagerduty events v2 routing key"><Input data-testid="alerts-pagerduty-input" type="password" placeholder={d.config.pagerdutyConfigured ? "configured — paste to replace" : "32-char integration key"} value={pd} onChange={(e) => setPd(e.target.value)} className="font-mono text-xs bg-[#04060A]" /></Field>
            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2 text-xs font-mono text-slate-300"><Switch data-testid="alerts-auto-switch" checked={d.config.autoDispatch} onCheckedChange={(c) => run("put", "/console/alerts/config", { autoDispatch: c })} />auto dispatch</label>
              <Button data-testid="alerts-save-config-button" size="sm" onClick={saveCfg}>Save</Button>
            </div>
            {live && <Button data-testid="alerts-clear-config-button" size="sm" variant="ghost" className="text-[#FF3366]" onClick={() => run("put", "/console/alerts/config", { slackWebhookUrl: "", pagerdutyRoutingKey: "" }, "Destinations cleared")}>Clear destinations</Button>}
            <p className="text-[11px] text-slate-500 font-mono leading-5">Without destinations every notification is routed and logged as dry_run. critical → pagerduty-critical · warning → slack-platform</p>
          </div>
        </Panel>
        <Panel title="Alert Rules" className="lg:col-span-2" testId="alerts-rules">
          {d.alerts.map((a) => (
            <div key={a.name} data-testid={`alert-rule-${a.name}`} className="grid grid-cols-12 gap-2 items-center py-2 border-b border-[#1E2638] last:border-0">
              <span className="col-span-4 flex items-center gap-2 text-sm"><Dot tone={a.firing ? "bad" : "ok"} />{a.name}</span>
              <span className="col-span-2"><Pill tone={a.severity === "critical" ? "bad" : "warn"}>{a.severity}</Pill></span>
              <span className="col-span-3 font-mono text-[11px] text-slate-400">→ {a.receiver}</span>
              <span className="col-span-3 font-mono text-[11px] text-right text-slate-500">{a.inhibitedBy ? `inhibited by ${a.inhibitedBy}` : a.firing ? `since ${a.since?.slice(11, 19) || "now"}` : "ok"}</span>
            </div>
          ))}
        </Panel>
      </div>
      <Panel title="Delivery Log" testId="alerts-delivery-log" right={<Pill tone="info">{d.deliveries.length}</Pill>}>
        <div className="terminal p-3 font-mono text-[11px] max-h-80 overflow-auto scroll-thin space-y-1">
          {d.deliveries.length === 0 && <div className="text-slate-600">no notifications yet — state changes (firing/resolved) are delivered once</div>}
          {d.deliveries.map((x) => (
            <div key={x.id}>
              <span className="text-slate-600">{x.sentAt.slice(11, 19)}</span>{" "}
              <span style={{ color: x.result === "delivered" ? "#00E676" : x.result === "failed" ? "#FF3366" : "#FFB300" }}>{x.result}</span>{" "}
              <span className="text-cyan-300">{x.channel}/{x.receiver}</span> <span className="text-slate-300">{x.summary}</span> <span className="text-slate-600">{x.detail}{x.test ? " · test" : ""}</span>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
