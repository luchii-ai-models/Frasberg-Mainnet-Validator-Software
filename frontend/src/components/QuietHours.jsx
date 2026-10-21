import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Field, Panel, Pill } from "@/components/kit";
import { toast } from "sonner";
import { call } from "@/lib/api";

export const QuietHours = ({ cfg, active, pending, reload }) => {
  const [f, setF] = useState(cfg);
  useEffect(() => setF(cfg), [cfg]);
  const save = async (body, msg) => {
    const r = await call("put", "/console/alerts/config", body, msg);
    if (!r) setF(cfg);
    reload();
  };
  const submit = () => {
    const esc = Number(f.escalationMinutes);
    if (f.escalationMinutes === "" || !Number.isInteger(esc) || esc < 0 || esc > 1440) { toast.error("INVALID_INPUT", { description: "Escalation must be a whole number of minutes (0–1440)" }); setF(cfg); return; }
    save({ quietStart: f.quietStart, quietEnd: f.quietEnd, quietMode: f.quietMode, escalationMinutes: esc }, "Quiet hours saved");
  };
  return (
    <Panel title="Quiet hours & escalation" testId="quiet-hours" className="mb-6"
      right={<><Pill tone={active ? "violet" : "muted"} testId="quiet-active-pill">{active ? "quiet now" : "normal"}</Pill>{pending > 0 && <Pill tone="warn" testId="digest-pending-pill">{pending} queued</Pill>}</>}>
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex items-center gap-2 text-xs font-mono text-slate-300 pb-2">
          <Switch data-testid="quiet-enabled-switch" checked={cfg.quietEnabled} onCheckedChange={(c) => save({ quietEnabled: c }, c ? "Quiet hours on" : "Quiet hours off")} />quiet hours
        </label>
        <Field label="start (UTC)"><Input data-testid="quiet-start-input" type="time" value={f.quietStart} onChange={(e) => setF({ ...f, quietStart: e.target.value })} className="font-mono text-xs bg-[#04060A] w-28" /></Field>
        <Field label="end (UTC)"><Input data-testid="quiet-end-input" type="time" value={f.quietEnd} onChange={(e) => setF({ ...f, quietEnd: e.target.value })} className="font-mono text-xs bg-[#04060A] w-28" /></Field>
        <Field label="warnings during quiet">
          <div className="flex gap-1">
            {["digest", "suppress"].map((m) => (
              <button key={m} data-testid={`quiet-mode-${m}`} onClick={() => setF({ ...f, quietMode: m })}
                className={`font-mono text-[11px] px-2.5 py-2 rounded-sm border transition-colors ${f.quietMode === m ? "border-cyan-400 text-cyan-300 bg-cyan-400/10" : "border-[#1E2638] text-slate-400"}`}>{m}</button>
            ))}
          </div>
        </Field>
        <Field label="escalate critical after (min, 0 = off)"><Input data-testid="escalation-minutes-input" type="number" min={0} max={1440} value={f.escalationMinutes} onChange={(e) => setF({ ...f, escalationMinutes: e.target.value })} className="font-mono text-xs bg-[#04060A] w-40" /></Field>
        <Button data-testid="quiet-save-button" size="sm" onClick={submit}>Save</Button>
        {pending > 0 && <Button data-testid="digest-flush-button" size="sm" variant="secondary" onClick={() => call("post", "/console/alerts/digest/flush", null, "Digest sent").then(reload)}>Send digest now</Button>}
      </div>
      <p className="text-[11px] text-slate-500 font-mono leading-5 mt-3">Critical pages always go out immediately. Warnings in quiet hours are batched into one Slack digest at window end (digest) or dropped (suppress). Unacknowledged critical alerts re-page every N minutes while firing.</p>
    </Panel>
  );
};
