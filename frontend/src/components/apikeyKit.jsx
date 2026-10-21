import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, Pill } from "@/components/kit";
import { call } from "@/lib/api";

export const SCOPE_TONE = { read: "info", write: "warn", admin: "violet" };
export const STATUS_TONE = { active: "ok", disabled: "muted", revoked: "bad", expired: "bad" };

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const t = document.createElement("textarea");
    t.value = text;
    document.body.appendChild(t);
    t.select();
    document.execCommand("copy");
    t.remove();
  }
  toast.success("Copied to clipboard");
}

export const CopyButton = ({ text, testId, label }) => {
  const [done, setDone] = useState(false);
  return (
    <Button data-testid={testId} size="sm" variant="secondary" onClick={async () => { await copyText(text); setDone(true); setTimeout(() => setDone(false), 1500); }}>
      {done ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}{label && <span className="ml-1.5">{label}</span>}
    </Button>
  );
};

export const ScopePicker = ({ value, onChange }) => (
  <div className="flex gap-1.5">
    {["read", "write", "admin"].map((s) => (
      <button key={s} type="button" data-testid={`apikey-scope-${s}`} onClick={() => onChange(s)}
        className={`text-xs font-medium px-3 py-2 rounded-full border transition-colors ${value === s ? "border-[var(--brand)] text-[var(--brand)] bg-[var(--brand-subtle)]" : "border-[var(--line)] text-[var(--text-2)] hover:text-[var(--text)]"}`}>
        {s === "admin" ? "full (admin)" : s}
      </button>
    ))}
  </div>
);

const EMAIL = /^$|^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function KeyDialog({ initial, onClose, onDone }) {
  const isNew = !initial;
  const [f, setF] = useState(initial ? { name: initial.name, scope: initial.scope, ownerEmail: initial.ownerEmail || "" } : { name: "", scope: "read", ownerEmail: "", expiresInDays: "" });
  const save = async () => {
    if (!f.name.trim()) return toast.error("NAME_REQUIRED", { description: "Give the key a name, e.g. grafana-exporter" });
    if (!EMAIL.test(f.ownerEmail.trim())) return toast.error("INVALID_EMAIL", { description: "Enter a valid owner email or leave it empty" });
    const days = f.expiresInDays === "" || f.expiresInDays === undefined ? null : Number(f.expiresInDays);
    if (isNew && days !== null && (!Number.isInteger(days) || days < 1 || days > 3650)) return toast.error("INVALID_EXPIRY", { description: "Expiry must be 1–3650 days or empty" });
    const body = { name: f.name.trim(), scope: f.scope, ownerEmail: f.ownerEmail.trim(), ...(isNew && days ? { expiresInDays: days } : {}) };
    const r = await call(isNew ? "post" : "put", isNew ? "/console/apikeys" : `/console/apikeys/${initial.keyId}`, body, isNew ? "API key created" : "API key updated");
    if (r) onDone(r);
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg" data-testid="apikey-dialog">
        <DialogHeader>
          <DialogTitle className="font-heading">{isNew ? "Create API key" : `Edit ${initial.name}`}</DialogTitle>
          <DialogDescription>Read keys can call any GET endpoint. Write keys can also change data. Full (admin) keys can also manage keys, alert destinations and Slack routing.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <Field label="Name"><Input data-testid="apikey-name-input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="grafana-exporter" /></Field>
          <Field label="Access"><ScopePicker value={f.scope} onChange={(scope) => setF({ ...f, scope })} /></Field>
          <Field label="Owner email (optional)"><Input data-testid="apikey-email-input" value={f.ownerEmail} onChange={(e) => setF({ ...f, ownerEmail: e.target.value })} placeholder="ops@example.com" /></Field>
          {isNew && <Field label="Expires after (days, empty = never)"><Input data-testid="apikey-expiry-input" type="number" min={1} value={f.expiresInDays} onChange={(e) => setF({ ...f, expiresInDays: e.target.value })} /></Field>}
        </div>
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={onClose} data-testid="apikey-cancel-button">Cancel</Button>
          <Button onClick={save} data-testid="apikey-save-button">{isNew ? "Create key" : "Save"}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function RevealDialog({ k, onClose }) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl" data-testid="apikey-reveal-dialog">
        <DialogHeader>
          <DialogTitle className="font-heading">Copy your new key now</DialogTitle>
          <DialogDescription>This is the only time the full key is shown. Store it in a password or secret manager.</DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-2">
          <code data-testid="apikey-reveal-token" className="flex-1 font-mono text-xs break-all p-3 rounded-lg bg-[var(--surface-2)] border border-[var(--line)]">{k.token}</code>
          <CopyButton text={k.token} testId="apikey-reveal-copy" />
        </div>
        <div className="flex flex-wrap gap-2 items-center"><Pill tone={SCOPE_TONE[k.scope]}>{k.scope}</Pill><span className="text-xs text-[var(--text-muted)]">{k.name}</span></div>
        <div className="flex justify-end"><Button onClick={onClose} data-testid="apikey-reveal-done">I've saved it</Button></div>
      </DialogContent>
    </Dialog>
  );
}
