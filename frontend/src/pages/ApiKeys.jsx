import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Dot, PageHeader, Panel, Pill, Stat, useLoad } from "@/components/kit";
import { CopyButton, KeyDialog, RevealDialog, SCOPE_TONE, STATUS_TONE } from "@/components/apikeyKit";
import { API, call } from "@/lib/api";

const when = (s) => (s ? s.slice(0, 16).replace("T", " ") : "never");

function KeyRow({ k, onEdit, onAction, onToggle }) {
  const live = k.status !== "revoked";
  return (
    <div data-testid={`apikey-row-${k.keyId}`} className="grid grid-cols-1 lg:grid-cols-12 gap-3 items-center py-4 border-b border-[var(--line)] last:border-0">
      <div className="lg:col-span-4 min-w-0">
        <div className="flex items-center gap-2 font-medium text-[var(--text)]">{k.name}<Pill tone={SCOPE_TONE[k.scope]} testId={`apikey-scope-pill-${k.keyId}`}>{k.scope === "admin" ? "full" : k.scope}</Pill></div>
        <div className="flex items-center gap-2 mt-1">
          <code className="font-mono text-xs text-[var(--text-muted)] truncate" data-testid={`apikey-masked-${k.keyId}`}>{k.masked}</code>
          <CopyButton text={k.keyId} testId={`apikey-copy-id-${k.keyId}`} />
        </div>
        {k.ownerEmail && <div className="text-xs text-[var(--text-muted)] mt-1">{k.ownerEmail}</div>}
      </div>
      <div className="lg:col-span-2"><Pill tone={STATUS_TONE[k.status]} testId={`apikey-status-${k.keyId}`}>{k.status}</Pill></div>
      <div className="lg:col-span-3 text-xs text-[var(--text-muted)]" data-testid={`apikey-usage-${k.keyId}`}>
        <div>Last used {when(k.lastUsedAt)} · {k.useCount} calls</div>
        {k.lastUsedPath && <div className="font-mono truncate">{k.lastUsedPath}</div>}
        <div>Created {when(k.createdAt)}{k.expiresAt ? ` · expires ${when(k.expiresAt)}` : ""}</div>
      </div>
      <div className="lg:col-span-3 flex flex-wrap items-center justify-start lg:justify-end gap-2">
        {live && <Switch data-testid={`apikey-toggle-${k.keyId}`} checked={k.enabled} onCheckedChange={(c) => onToggle(k, c)} />}
        {live && <Button size="sm" variant="secondary" onClick={() => onEdit(k)} data-testid={`apikey-edit-${k.keyId}`}>Edit</Button>}
        {live && <Button size="sm" variant="secondary" onClick={() => onAction(k, "rotate")} data-testid={`apikey-rotate-${k.keyId}`}>Rotate</Button>}
        {live && <Button size="sm" variant="ghost" className="text-[var(--bad)]" onClick={() => onAction(k, "revoke")} data-testid={`apikey-revoke-${k.keyId}`}>Revoke</Button>}
      </div>
    </div>
  );
}

function VerifyPanel() {
  const [key, setKey] = useState("");
  const [res, setRes] = useState(null);
  const run = async () => { if (!key.trim()) return; setRes(await call("post", "/console/apikeys/verify", { key: key.trim() })); };
  return (
    <Panel title="Verify a key" testId="apikey-verify-panel">
      <p className="text-sm text-[var(--text-2)] mb-4">Paste any key to check live which access it really has. Read, write and full access are each tested with a real request.</p>
      <div className="flex gap-2">
        <Input data-testid="apikey-verify-input" type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="fk_live.…" className="font-mono text-sm" />
        <Button data-testid="apikey-verify-button" onClick={run}>Verify</Button>
      </div>
      {res && (
        <div className="mt-4 space-y-2" data-testid="apikey-verify-result">
          <div className="text-sm">{res.key ? <>Key <b>{res.key.name}</b> · <Pill tone={STATUS_TONE[res.key.status]}>{res.key.status}</Pill></> : <Pill tone="bad">unknown key</Pill>}</div>
          {res.checks.map((c) => (
            <div key={c.level} data-testid={`apikey-verify-${c.level}`} className="flex items-center justify-between gap-3 p-3 rounded-lg bg-[var(--surface-2)]">
              <span className="flex items-center gap-2 text-sm"><Dot tone={c.allowed ? "ok" : "bad"} /><span className="font-medium capitalize">{c.level === "admin" ? "Full access" : `${c.level} access`}</span></span>
              <span className="text-xs text-[var(--text-muted)] font-mono">{c.request} → {c.status}{c.code ? ` ${c.code}` : ""}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function Quickstart({ base }) {
  const read = `curl -H "X-API-Key: $FRAS_KEY" ${base}/v1/validator/status`;
  const write = `curl -X POST -H "X-API-Key: $FRAS_KEY" ${base}/_probe/write`;
  return (
    <Panel title="How to call the API" testId="apikey-quickstart">
      <p className="text-sm text-[var(--text-2)] mb-4">Every endpoint is available under <code className="font-mono text-xs px-1.5 py-0.5 rounded bg-[var(--surface-2)]">{base}</code>. Send the key in the <code className="font-mono text-xs">X-API-Key</code> header (or <code className="font-mono text-xs">Authorization: Bearer</code>).</p>
      {[["Read", read, "read"], ["Write check (no changes made)", write, "write"]].map(([l, cmd, id]) => (
        <div key={id} className="mb-3">
          <div className="text-xs font-medium text-[var(--text-muted)] mb-1">{l}</div>
          <div className="flex items-center gap-2"><code className="terminal flex-1 font-mono text-xs p-3 break-all">{cmd}</code><CopyButton text={cmd} testId={`apikey-copy-${id}-snippet`} /></div>
        </div>
      ))}
    </Panel>
  );
}

export default function ApiKeys() {
  const [d, reload] = useLoad("/console/apikeys");
  const [edit, setEdit] = useState(null);
  const [reveal, setReveal] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [showRevoked, setShowRevoked] = useState(false);
  const [ver, setVer] = useState(0);
  if (!d) return null;
  const base = `${API}/ext`;
  const active = d.keys.filter((k) => k.status === "active");
  const rows = d.keys.filter((k) => showRevoked || k.status !== "revoked");
  const doAction = async () => {
    const { k, action } = confirm;
    setConfirm(null);
    const r = await call("post", `/console/apikeys/${k.keyId}/${action}`, null, action === "revoke" ? "Key revoked" : "Key rotated");
    if (r && action === "rotate") setReveal(r);
    setVer((v) => v + 1);
    reload();
  };
  const toggle = async (k, enabled) => { await call("put", `/console/apikeys/${k.keyId}`, { enabled }, enabled ? "Key enabled" : "Key disabled"); reload(); };
  return (
    <div>
      <PageHeader kicker="Give apps and scripts read, write or full access to the API" title="API Keys">
        <Button data-testid="apikey-create-button" onClick={() => setEdit("new")}>Create API key</Button>
      </PageHeader>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Stat label="Active keys" value={active.length} tone={active.length ? "ok" : "muted"} testId="apikey-active-count" />
        <Stat label="Read" value={active.filter((k) => k.scope === "read").length} />
        <Stat label="Write" value={active.filter((k) => k.scope === "write").length} />
        <Stat label="Full access" value={active.filter((k) => k.scope === "admin").length} />
      </div>
      <Panel title="Keys" testId="apikey-list" className="mb-6"
        right={<label className="flex items-center gap-2 text-xs text-[var(--text-2)]"><Switch data-testid="apikey-show-revoked" checked={showRevoked} onCheckedChange={setShowRevoked} />show revoked</label>}>
        {rows.length === 0 && <div className="text-sm text-[var(--text-muted)] py-6 text-center" data-testid="apikey-empty">{d.keys.length ? `No active keys (${d.keys.length} revoked). Create a new one to call the API.` : "No keys yet. Create one to call the API from your apps."}</div>}
        {rows.map((k) => <KeyRow key={k.keyId} k={k} onEdit={setEdit} onToggle={toggle} onAction={(key, action) => setConfirm({ k: key, action })} />)}
      </Panel>
      <div className="grid lg:grid-cols-2 gap-6 mb-6">
        <VerifyPanel key={ver} />
        <Quickstart base={base} />
      </div>
      <Panel title="Recent API calls" testId="apikey-events">
        <div className="terminal p-3 font-mono text-[11px] max-h-72 overflow-auto scroll-thin space-y-1">
          {d.events.length === 0 && <div className="text-[var(--text-muted)]">no calls yet</div>}
          {d.events.map((e, i) => (
            <div key={i}><span className="text-[var(--text-muted)]">{e.at.slice(11, 19)}</span> <span style={{ color: e.status < 400 ? "var(--ok)" : "var(--bad)" }}>{e.status}</span> <span className="text-[var(--brand)]">{e.name}</span> <span className="text-[var(--text-2)]">{e.method} {e.path}</span></div>
          ))}
        </div>
      </Panel>
      {edit && <KeyDialog initial={edit === "new" ? null : edit} onClose={() => setEdit(null)} onDone={(r) => { setEdit(null); if (r.token) setReveal(r); reload(); }} />}
      {reveal && <RevealDialog k={reveal} onClose={() => setReveal(null)} />}
      <AlertDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent data-testid="apikey-confirm-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-heading">{confirm?.action === "revoke" ? `Revoke ${confirm?.k.name}?` : `Rotate ${confirm?.k.name}?`}</AlertDialogTitle>
            <AlertDialogDescription>{confirm?.action === "revoke" ? "Apps using this key stop working immediately. This cannot be undone." : "A new key with the same access is created and the current key stops working immediately."}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="apikey-confirm-cancel">Cancel</AlertDialogCancel>
            <AlertDialogAction data-testid="apikey-confirm-ok" className={confirm?.action === "revoke" ? "bg-[var(--bad)] hover:bg-[var(--bad)]/80 text-white" : ""} onClick={doAction}>{confirm?.action === "revoke" ? "Revoke" : "Rotate"}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
