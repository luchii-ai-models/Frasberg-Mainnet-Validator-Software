import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Field, Json, PageHeader, Panel, Pill, statusTone, useLoad } from "@/components/kit";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { call, short } from "@/lib/api";

const RULES = [["active", "yes", "yes"], ["rotating", "no", "yes"], ["retired", "no", "yes"], ["revoked", "no", "no"]];

export default function Keys() {
  const [keys, reload] = useLoad("/console/keys");
  const [form, setForm] = useState({ oldKeyId: "", newKeyId: "", owner: "node-a", injectFailure: false });
  const [last, setLast] = useState(null);
  const active = keys?.find((k) => k.status === "active");
  const f = { ...form, oldKeyId: form.oldKeyId || active?.key_id || "" };

  const act = async (method, url, body, msg) => { const r = await call(method, url, body, msg); if (r) setLast(r); reload(); };

  return (
    <div>
      <PageHeader kicker="active · rotating · retired · revoked" title="Signing Keys">
        <Button data-testid="create-key-button" onClick={() => act("post", "/console/keys", null, "Key created (KEY_CREATED)")}>Create key</Button>
      </PageHeader>
      <Panel title="Key Lifecycle Matrix" testId="keys-table" className="mb-6">
        <Table>
          <TableHeader><TableRow>{["Key ID", "Status", "Public key", "Fencing token", "Created", "Revoked", ""].map((h) => <TableHead key={h} className="font-mono text-[10px] uppercase tracking-widest">{h}</TableHead>)}</TableRow></TableHeader>
          <TableBody>
            {(keys || []).map((k) => (
              <TableRow key={k.key_id} data-testid={`key-row-${k.key_id}`}>
                <TableCell className="font-mono text-cyan-300">{k.key_id}</TableCell>
                <TableCell><Pill tone={statusTone(k.status)} testId={`key-status-${k.key_id}`}>{k.status}</Pill></TableCell>
                <TableCell className="font-mono text-xs text-slate-400">{short(k.public_key, 18)}</TableCell>
                <TableCell className="font-mono">{k.fencing_token}</TableCell>
                <TableCell className="font-mono text-xs text-slate-500">{k.created_at?.slice(0, 19)}</TableCell>
                <TableCell className="font-mono text-xs text-slate-500">{k.revoked_at?.slice(0, 19) || "—"}</TableCell>
                <TableCell className="text-right">
                  {!["revoked", "active"].includes(k.status) && <Button data-testid={`revoke-key-${k.key_id}`} size="sm" variant="destructive" onClick={() => act("post", `/console/keys/${k.key_id}/revoke`, null, `${k.key_id} revoked`)}>Revoke</Button>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>
      <div className="grid lg:grid-cols-3 gap-6">
        <Panel title="Rotate / Roll Back" testId="rotation-panel" className="lg:col-span-2">
          <div className="grid sm:grid-cols-3 gap-3">
            <Field label="old key (active)"><Input data-testid="rotate-old-key-input" value={f.oldKeyId} onChange={(e) => setForm({ ...form, oldKeyId: e.target.value })} className="font-mono text-xs bg-[#04060A]" /></Field>
            <Field label="new key"><Input data-testid="rotate-new-key-input" placeholder="validator-key-002" value={f.newKeyId} onChange={(e) => setForm({ ...form, newKeyId: e.target.value })} className="font-mono text-xs bg-[#04060A]" /></Field>
            <Field label="lock owner node"><Input data-testid="rotate-owner-input" value={f.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} className="font-mono text-xs bg-[#04060A]" /></Field>
          </div>
          <div className="flex flex-wrap items-center gap-3 mt-4">
            <label className="flex items-center gap-2 text-xs font-mono text-slate-300"><Switch data-testid="inject-rotation-failure-switch" checked={f.injectFailure} onCheckedChange={(c) => setForm({ ...form, injectFailure: c })} />inject failure</label>
            <Button data-testid="rotate-key-button" onClick={() => act("post", "/console/keys/rotate", f, "Rotation completed")}>Rotate</Button>
            <Button data-testid="rollback-rotation-button" variant="secondary" onClick={() => act("post", "/console/keys/rollback", f, "Rotation rolled back")}>Roll back rotation</Button>
          </div>
          <p className="text-xs text-slate-500 mt-3 font-mono">Lock rotation:&lt;keyId&gt; → fencing token → KEY_ROTATION_STARTED → atomic fenced state change → KEY_ROTATION_COMPLETED</p>
          {last && <div className="mt-4"><Json value={last} testId="rotation-result" className="max-h-48" /></div>}
        </Panel>
        <Panel title="Revocation Rules" testId="revocation-rules">
          <Table>
            <TableHeader><TableRow>{["Status", "Sign", "Verify"].map((h) => <TableHead key={h} className="font-mono text-[10px] uppercase">{h}</TableHead>)}</TableRow></TableHeader>
            <TableBody>{RULES.map(([s, a, b]) => <TableRow key={s}><TableCell><Pill tone={statusTone(s)}>{s}</Pill></TableCell><TableCell className="font-mono">{a}</TableCell><TableCell className="font-mono">{b}</TableCell></TableRow>)}</TableBody>
          </Table>
          <p className="text-xs text-slate-500 mt-3">Rollback allowed only while the previous key is not revoked.</p>
        </Panel>
      </div>
    </div>
  );
}
