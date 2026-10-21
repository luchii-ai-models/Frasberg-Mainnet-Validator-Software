import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, Pill } from "@/components/kit";
import { toast } from "sonner";
import { call } from "@/lib/api";

export const RoutingBadge = ({ r, onClick }) => (
  <button data-testid={`fleet-routing-${r?.validatorId}`} onClick={onClick} className="text-left">
    {r?.slackMemberId || r?.hasWebhook
      ? <span className="flex flex-col gap-1">{r.slackMemberId && <Pill tone="info">@{r.slackMemberId}</Pill>}{r.hasWebhook && <Pill tone="violet">own channel</Pill>}</span>
      : <span className="text-xs text-[var(--text-muted)] hover:text-[var(--brand)]">main channel</span>}
  </button>
);

export function RoutingDialog({ r, onClose, onSaved }) {
  const [member, setMember] = useState(r.slackMemberId || "");
  const [hook, setHook] = useState("");
  const save = async () => {
    const body = { slackMemberId: member.trim() };
    if (hook) body.slackWebhookUrl = hook.trim();
    const res = await call("put", `/console/fleet/${r.validatorId}/routing`, body, "Routing saved");
    setHook("");
    if (res) onSaved();
  };
  const clearHook = async () => { if (await call("put", `/console/fleet/${r.validatorId}/routing`, { slackWebhookUrl: "" }, "Own channel removed")) onSaved(); };
  const test = async () => { const t = await call("post", `/console/fleet/${r.validatorId}/routing/test`); if (t) toast[t.result === "failed" ? "error" : "success"](`Test ${t.result}`, { description: `${t.target}${t.mention ? ` · ${t.mention}` : ""} · ${t.detail}` }); };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg" data-testid="fleet-routing-dialog">
        <DialogHeader>
          <DialogTitle className="font-heading">Slack routing · {r.validatorId}</DialogTitle>
          <DialogDescription>Upgrade nudges and drift alerts for this validator tag this person and go to its own channel. Leave empty to use the main channel.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <Field label="Slack member or group ID (U…, W… or S…)">
            <Input data-testid="routing-member-input" value={member} onChange={(e) => setMember(e.target.value.toUpperCase())} placeholder="U0123ABCD" className="font-mono text-sm" />
          </Field>
          <Field label={r.hasWebhook ? "Own channel webhook (configured, paste to replace)" : "Own channel incoming webhook (optional)"}>
            <Input data-testid="routing-webhook-input" type="password" value={hook} onChange={(e) => setHook(e.target.value)} placeholder="https://hooks.slack.com/services/…" className="font-mono text-sm" />
          </Field>
        </div>
        <div className="flex flex-wrap justify-between gap-2 mt-2">
          <div className="flex gap-2">
            <Button variant="secondary" onClick={test} data-testid="routing-test-button">Send test</Button>
            {r.hasWebhook && <Button variant="ghost" className="text-[var(--bad)]" onClick={clearHook} data-testid="routing-clear-webhook-button">Remove own channel</Button>}
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose} data-testid="routing-cancel-button">Cancel</Button>
            <Button onClick={save} data-testid="routing-save-button">Save</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
