import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field, Json, PageHeader, Panel, Pill, Stat, useLoad } from "@/components/kit";
import { call } from "@/lib/api";

export default function Locks() {
  const [d, reload] = useLoad("/console/locks");
  const [f, setF] = useState({ name: "rotation:validator-key-001", owner: "node-a", ttlMs: 30000, token: "" });
  const [log, setLog] = useState([]);
  const push = (entry) => setLog((l) => [{ t: new Date().toISOString().slice(11, 23), ...entry }, ...l].slice(0, 30));
  const body = { ...f, ttlMs: Number(f.ttlMs), token: f.token === "" ? null : Number(f.token) };

  const op = async (path, extra = {}) => {
    const r = await call("post", `/console/locks/${path}`, { ...body, ...extra });
    push({ op: path, owner: f.owner, result: r ? JSON.stringify(r) : "rejected (see toast)" });
    if (r?.token) setF((x) => ({ ...x, token: String(r.token) }));
    reload();
  };
  const race = async () => {
    const owners = ["node-a", "node-b", "node-c"];
    const stamp = Date.now();
    const res = await Promise.all(owners.map((o, i) => call("post", "/console/locks/acquire", { ...body, owner: o, name: `parallel:${stamp}:key-${i}` })));
    push({ op: "race x3", owner: owners.join(","), result: res.map((r, i) => `${owners[i]}=${r ? `token ${r.token}` : "DENIED"}`).join("  ") });
    reload();
  };
  const raceSame = async () => {
    const owners = ["node-a", "node-b", "node-c"];
    const name = `split-brain:${Date.now()}`;
    const res = await Promise.all(owners.map((o) => call("post", "/console/locks/acquire", { ...body, owner: o, name })));
    push({ op: "split-brain x3 (same lock)", owner: owners.join(","), result: res.map((r, i) => `${owners[i]}=${r ? `WIN token ${r.token}` : "denied"}`).join("  ") });
    reload();
  };

  return (
    <div>
      <PageHeader kicker="SET NX PX semantics on MongoDB · fencing tokens" title="Locks & Fencing" />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Stat label="Fencing counter" value={d?.fencingCounter ?? "—"} tone="info" testId="fencing-counter" />
        <Stat label="Active locks" value={d?.locks?.filter((l) => l.active).length ?? 0} tone="ok" testId="active-locks-count" />
        <Stat label="Expired (held)" value={d?.locks?.filter((l) => !l.active).length ?? 0} tone="warn" />
        <Stat label="Lease TTL" value="30s" sub="renew before expiry" />
      </div>
      <div className="grid lg:grid-cols-2 gap-6 mb-6">
        <Panel title="Lock Operations" testId="lock-ops">
          <div className="grid grid-cols-2 gap-3">
            {["name", "owner", "ttlMs", "token"].map((k) => (
              <Field key={k} label={k}><Input data-testid={`lock-input-${k}`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} className="font-mono text-xs bg-[var(--surface)]" /></Field>
            ))}
          </div>
          <div className="flex flex-wrap gap-2 mt-4">
            <Button data-testid="lock-acquire-button" size="sm" onClick={() => op("acquire")}>Acquire</Button>
            <Button data-testid="lock-renew-button" size="sm" variant="secondary" onClick={() => op("renew")}>Renew</Button>
            <Button data-testid="lock-renew-fail-button" size="sm" variant="secondary" onClick={() => op("renew", { fail: true })}>Renew (inject outage)</Button>
            <Button data-testid="lock-release-button" size="sm" variant="secondary" onClick={() => op("release")}>Release</Button>
            <Button data-testid="lock-expire-button" size="sm" variant="secondary" onClick={() => op("expire")}>Force expire</Button>
          </div>
          <div className="flex flex-wrap gap-2 mt-3">
            <Button data-testid="lock-split-brain-button" size="sm" variant="outline" onClick={raceSame}>Split-brain race (3 nodes, 1 lock)</Button>
            <Button data-testid="lock-parallel-button" size="sm" variant="outline" onClick={race}>Parallel (3 nodes, 3 locks)</Button>
          </div>
        </Panel>
        <Panel title="Operation Log" testId="lock-log">
          <div className="terminal p-3 font-mono text-[11px] h-64 overflow-auto scroll-thin space-y-1">
            {log.length === 0 && <div className="text-[var(--text-muted)]">no operations yet</div>}
            {log.map((l, i) => <div key={i}><span className="text-[var(--text-muted)]">{l.t}</span> <span className="text-[var(--brand)]">{l.op}</span> <span className="text-[var(--text-2)]">{l.owner}</span> <span className="text-[var(--ok)]">{l.result}</span></div>)}
          </div>
        </Panel>
      </div>
      <Panel title="Lock Table" testId="lock-table" right={<Pill tone="info">{d?.locks?.length || 0} rows</Pill>}>
        <Json value={d?.locks || []} className="max-h-72" />
      </Panel>
    </div>
  );
}
