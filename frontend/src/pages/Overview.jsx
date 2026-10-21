import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Check, Dot, Field, Json, PageHeader, Panel, Pill, Stat, statusTone, useLoad } from "@/components/kit";
import { call, http } from "@/lib/api";

const ENDPOINTS = ["/v1/validator/version", "/v1/validator/status", "/v1/validator/compliance", "/v1/network/required-versions"];
const NUM = ["epoch", "commission", "stake", "uptime", "voteParticipation", "missedBlocks", "apiAvailability"];
const BOOL = ["securityScanPassed", "identityVerified", "governanceStanding"];

function Endpoints({ tick }) {
  const [active, setActive] = useState(ENDPOINTS[0]);
  const [body, setBody] = useState(null);
  useEffect(() => { http.get(active).then((r) => setBody(r.data)).catch((e) => setBody(e?.response?.data)); }, [active, tick]);
  return (
    <Panel title="Live Validator API" testId="endpoint-explorer" right={<Pill tone="ok">GET</Pill>}>
      <div className="flex flex-wrap gap-2 mb-3">
        {ENDPOINTS.map((e) => (
          <button key={e} data-testid={`endpoint-tab-${e.split("/").pop()}`} onClick={() => setActive(e)}
            className={`text-xs font-medium px-3 py-1.5 rounded-full border transition-colors ${active === e ? "border-[var(--brand)] text-[var(--brand)] bg-[var(--brand-subtle)]" : "border-[var(--line)] text-[var(--text-2)] hover:text-[var(--text)]"}`}>{e}</button>
        ))}
      </div>
      <Json value={body || "loading…"} testId="endpoint-response" />
    </Panel>
  );
}

function Simulator({ v, onSaved }) {
  const [f, setF] = useState(null);
  useEffect(() => { if (v) setF({ ...v }); }, [v]);
  if (!f) return null;
  const save = async () => {
    const body = { validatorVersion: f.validatorVersion, luchiiVersion: f.luchiiVersion, channel: f.channel };
    NUM.forEach((k) => (body[k] = Number(f[k])));
    BOOL.forEach((k) => (body[k] = f[k]));
    if (await call("put", "/console/validator", body, "Validator state updated")) onSaved();
  };
  return (
    <Panel title="Operator State" testId="validator-simulator" right={<Button data-testid="validator-save-button" size="sm" onClick={save}>Apply</Button>}>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        {["validatorVersion", "luchiiVersion", "channel", ...NUM].map((k) => (
          <Field key={k} label={k}><Input data-testid={`validator-input-${k}`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} className="font-mono text-xs bg-[var(--surface)]" /></Field>
        ))}
      </div>
      <div className="flex flex-wrap gap-5 mt-4">
        {BOOL.map((k) => (
          <label key={k} className="flex items-center gap-2 text-xs text-[var(--text-2)] font-mono">
            <Switch data-testid={`validator-switch-${k}`} checked={!!f[k]} onCheckedChange={(c) => setF({ ...f, [k]: c })} />{k}
          </label>
        ))}
      </div>
    </Panel>
  );
}

export default function Overview() {
  const [ov, reload] = useLoad("/console/overview");
  const [tick, setTick] = useState(0);
  if (!ov) return <div className="font-mono text-[var(--text-muted)]">loading validator…</div>;
  const { validator: v, compliance: c, healthRequirements: hr, required: req } = ov;
  const refresh = () => { reload(); setTick((t) => t + 1); };
  return (
    <div>
      <PageHeader kicker={`${v.network} · epoch ${v.epoch}`} title={v.validatorId}>
        <Pill tone={c.healthy ? "ok" : "bad"} testId="validator-health-pill"><Dot tone={c.healthy ? "ok" : "bad"} />{c.healthy ? "healthy" : "unhealthy"}</Pill>
        <Pill tone={statusTone(c.versionClass)} testId="version-class-pill">{c.versionClass}</Pill>
        <Pill tone={c.delegationEligible ? "ok" : "bad"} testId="delegation-pill">delegation {c.delegationEligible ? "eligible" : "removed"}</Pill>
      </PageHeader>

      <div className="grid grid-cols-2 lg:grid-cols-6 gap-4 mb-6">
        <Stat label="Validator" value={v.validatorVersion} sub={v.channel} testId="stat-validator-version" />
        <Stat label="Luchii" value={v.luchiiVersion} sub={`min ${req.luchii.minimum}`} testId="stat-luchii-version" />
        <Stat label="Compliance" value={c.code} tone={c.compliant ? "ok" : "bad"} sub={`min ${req.validator.minimum}`} testId="stat-compliance-code" />
        <Stat label="Uptime" value={`${v.uptime}%`} tone={c.health.uptime ? "ok" : "bad"} sub={`≥ ${hr.uptime}%`} testId="stat-uptime" />
        <Stat label="Commission" value={`${v.commission}%`} tone={c.criteria.maxCommission ? "ok" : "bad"} sub="max 5%" testId="stat-commission" />
        <Stat label="Stake" value={`${(v.stake / 1000).toFixed(0)}k`} tone={c.criteria.minStake ? "ok" : "bad"} sub="min 100k FRAS" testId="stat-stake" />
      </div>

      <div className="grid lg:grid-cols-3 gap-6 mb-6">
        <Panel title="Health Requirements" testId="health-panel">
          <Check ok={c.health.uptime} label="Uptime ≥ 99.9%" detail={`${v.uptime}%`} />
          <Check ok={c.health.voteParticipation} label="Vote participation ≥ 95%" detail={`${v.voteParticipation}%`} />
          <Check ok={c.health.missedBlocks} label="Missed blocks ≤ 3%" detail={`${v.missedBlocks}%`} />
          <Check ok={c.health.apiAvailability} label="API availability ≥ 99.5%" detail={`${v.apiAvailability}%`} />
        </Panel>
        <Panel title="Delegation Criteria" testId="delegation-panel">
          {Object.entries(c.criteria).map(([k, ok]) => <Check key={k} ok={ok} label={k} detail={ok ? "pass" : "fail"} testId={`criteria-${k}`} />)}
        </Panel>
        <Panel title="Enforcement" testId="enforcement-panel">
          <Check ok={c.rewardsEnabled} label="Rewards" detail={c.rewardsEnabled ? "enabled" : "disabled"} />
          <Check ok={c.votingEnabled} label="Governance voting" detail={c.votingEnabled ? "enabled" : "disabled"} />
          <Check ok={!c.effects.warning} label="Upgrade warning" detail={c.effects.warning ? "issued" : "none"} />
          <Check ok={!c.inGracePeriod} label="Grace period" detail={c.inGracePeriod ? `until epoch ${req.enforcement.graceEndsEpoch}` : "not active"} />
          <div className="mt-4 font-mono text-[11px] text-[var(--text-muted)]">Announcement 30d · Upgrade window 14d · Grace 1 epoch</div>
        </Panel>
      </div>

      <div className="grid lg:grid-cols-5 gap-6 mb-6">
        <div className="lg:col-span-3"><Endpoints tick={tick} /></div>
        <Panel title="Validator Stack" className="lg:col-span-2" testId="validator-stack">
          <div className="terminal p-4 font-mono text-xs leading-6">
            {Object.entries(v.stack).map(([name, parts]) => (
              <div key={name} className="mb-2">
                <div className="text-[var(--brand)]">{name} <span className="text-[var(--text-muted)]">{name === "Luchii Runtime" ? v.luchiiVersion : v.validatorVersion}</span></div>
                {parts.map((p, i) => <div key={p} className="text-[var(--text-2)]">{i === parts.length - 1 ? "└──" : "├──"} {p} <span className="text-[var(--ok)]">●</span></div>)}
              </div>
            ))}
          </div>
        </Panel>
      </div>
      <Simulator v={v} onSaved={refresh} />
    </div>
  );
}
