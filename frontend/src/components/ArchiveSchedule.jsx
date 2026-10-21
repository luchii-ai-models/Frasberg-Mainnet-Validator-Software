import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Field, Panel, Pill, useLoad } from "@/components/kit";
import { toast } from "sonner";
import { call } from "@/lib/api";

const TONE = { success: "ok", skipped: "muted", failed: "bad" };

export const ArchiveSchedule = () => {
  const [d, reload] = useLoad("/console/audit/archive-schedule");
  const [time, setTime] = useState("");
  const [older, setOlder] = useState("");
  useEffect(() => { if (d) { setTime(d.timeUtc); setOlder(String(d.olderThanMinutes)); } }, [d]);
  if (!d) return null;
  const save = async (body, msg) => {
    const r = await call("put", "/console/audit/archive-schedule", body, msg);
    if (!r) { setTime(d.timeUtc); setOlder(String(d.olderThanMinutes)); }
    reload();
  };
  const submit = () => {
    const n = Number(older);
    if (older === "" || !Number.isInteger(n) || n < 0) { toast.error("INVALID_INPUT", { description: "Age must be a whole number of minutes ≥ 0" }); setOlder(String(d.olderThanMinutes)); return; }
    save({ timeUtc: time, olderThanMinutes: n }, "Schedule saved");
  };
  const runNow = async () => { const r = await call("post", "/console/audit/archive-schedule/run", null); if (r) reload(); };
  return (
    <Panel title="Daily archive schedule" testId="archive-schedule" className="mb-6"
      right={<Pill tone={d.enabled ? "ok" : "muted"} testId="archive-schedule-status">{d.enabled ? `next ${d.nextRunAt?.slice(0, 16).replace("T", " ")} UTC` : "paused"}</Pill>}>
      <div className="flex flex-wrap items-end gap-4 mb-4">
        <label className="flex items-center gap-2 text-xs font-mono text-slate-300 pb-2">
          <Switch data-testid="archive-schedule-enabled-switch" checked={d.enabled} onCheckedChange={(c) => save({ enabled: c }, c ? "Schedule enabled" : "Schedule paused")} />daily export
        </label>
        <Field label="run at (UTC)"><Input data-testid="archive-schedule-time-input" type="time" value={time} onChange={(e) => setTime(e.target.value)} className="font-mono text-xs bg-[#04060A] w-32" /></Field>
        <Field label="only records older than (min)"><Input data-testid="archive-schedule-older-input" type="number" min={0} value={older} onChange={(e) => setOlder(e.target.value)} className="font-mono text-xs bg-[#04060A] w-40" /></Field>
        <Button data-testid="archive-schedule-save-button" size="sm" onClick={submit}>Save</Button>
        <Button data-testid="archive-schedule-run-button" size="sm" variant="secondary" onClick={runNow}>Run now</Button>
      </div>
      <div className="terminal p-3 font-mono text-[11px] max-h-56 overflow-auto scroll-thin space-y-1" data-testid="archive-schedule-history">
        {d.history.length === 0 && <div className="text-slate-600">no runs yet</div>}
        {d.history.map((h) => (
          <div key={h.id} data-testid={`archive-run-${h.id}`} className="flex flex-wrap gap-2 items-center">
            <span className="text-slate-600">{h.startedAt.slice(0, 19).replace("T", " ")}</span>
            <Pill tone={TONE[h.status]}>{h.status}</Pill>
            <span className="text-cyan-300">{h.trigger}</span>
            <span className="text-slate-300">{h.archiveId || ""}</span>
            <span className="text-slate-500">{h.detail}{h.count ? ` · ${h.count} records` : ""}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
};
