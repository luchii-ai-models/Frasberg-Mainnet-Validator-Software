import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dot, Field, PageHeader, Panel, Pill, useLoad } from "@/components/kit";
import { call, short } from "@/lib/api";
import { ArchivePanel } from "@/components/ArchivePanel";
import { ArchiveSchedule } from "@/components/ArchiveSchedule";

export default function Audit() {
  const [d, reload] = useLoad("/console/audit?limit=200");
  const [chain, reloadChain] = useLoad("/console/audit/verify");
  const [mutation, setMutation] = useState("payload");
  const [index, setIndex] = useState("1");
  const [sim, setSim] = useState(null);
  const [open, setOpen] = useState(null);

  const simulate = async () => setSim(await call("post", "/console/audit/simulate-tamper", { mutation, index: Number(index) }));
  const verify = async () => { await reloadChain(); reload(); };

  return (
    <div>
      <PageHeader kicker="INSERT ONLY · NO UPDATE · NO DELETE" title="Audit Chain">
        <Button data-testid="audit-verify-button" onClick={verify}>Verify entire chain</Button>
      </PageHeader>
      <div className="grid lg:grid-cols-3 gap-6 mb-6">
        <Panel title="Chain Integrity" testId="chain-status" className="">
          <div className="flex items-center gap-3">
            <Dot tone={chain?.valid ? "ok" : "bad"} />
            <span data-testid="chain-status-code" className="font-heading text-2xl font-bold" style={{ color: chain?.valid ? "var(--ok)" : "var(--bad)" }}>{chain?.code || "…"}</span>
          </div>
          <div className="font-mono text-[11px] text-[var(--text-2)] mt-3 space-y-1">
            <div>length: {chain?.length ?? d?.total}</div>
            <div className="break-all">head: {chain?.head}</div>
          </div>
        </Panel>
        <Panel title="Tamper Simulator" testId="tamper-simulator" className="lg:col-span-2">
          <div className="grid sm:grid-cols-3 gap-3 items-end">
            <Field label="mutation">
              <Select value={mutation} onValueChange={setMutation}>
                <SelectTrigger data-testid="tamper-mutation-select" className="font-mono text-xs bg-[var(--surface)]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {[["payload", "modify payload"], ["link", "break previous_hash"], ["hash", "tamper current_hash"], ["gap", "delete record (gap)"]].map(([v, l]) => <SelectItem key={v} value={v} data-testid={`tamper-option-${v}`}>{l}</SelectItem>)}
                </SelectContent>
              </Select>
            </Field>
            <Field label="record index"><Input data-testid="tamper-index-input" value={index} onChange={(e) => setIndex(e.target.value)} className="font-mono text-xs bg-[var(--surface)]" /></Field>
            <Button data-testid="tamper-run-button" variant="destructive" onClick={simulate}>Run tamper test</Button>
          </div>
          {sim && (
            <div className="mt-4 terminal p-3 font-mono text-xs" data-testid="tamper-result">
              <span className="text-[var(--text-muted)]">{sim.mutation}@{sim.index} → </span>
              <span style={{ color: sim.result.valid ? "var(--ok)" : "var(--bad)" }}>{sim.result.code}</span>
              {sim.result.sequence && <span className="text-[var(--text-muted)]"> at sequence {sim.result.sequence}</span>}
              <div className="text-[var(--text-muted)] mt-1">{sim.note}</div>
            </div>
          )}
        </Panel>
      </div>
      <ArchivePanel />
      <ArchiveSchedule />
      <Panel title="Hash-Chained Records" testId="audit-records" right={<Pill tone="info">{d?.total || 0} records</Pill>}>
        <div className="max-h-[560px] overflow-auto scroll-thin">
          {(d?.records || []).map((r) => (
            <div key={r.sequence} className="border-b border-[var(--line)] last:border-0">
              <button data-testid={`audit-record-${r.sequence}`} onClick={() => setOpen(open === r.sequence ? null : r.sequence)}
                className="w-full grid grid-cols-12 gap-3 py-2.5 text-left hover:bg-[var(--surface-hover)] transition-colors px-2">
                <span className="col-span-1 font-mono text-xs text-[var(--brand)]">#{r.sequence}</span>
                <span className="col-span-3 font-mono text-xs text-[var(--text)]">{r.event_type}</span>
                <span className="col-span-2 font-mono text-xs text-[var(--text-2)] truncate">{r.actor}</span>
                <span className="col-span-2 font-mono text-[11px] text-[var(--text-muted)]">{short(r.previous_hash, 10)}</span>
                <span className="col-span-2 font-mono text-[11px] text-[var(--ok)]">{short(r.current_hash, 10)}</span>
                <span className="col-span-2 font-mono text-[11px] text-[var(--text-muted)] text-right">{r.retention}</span>
              </button>
              {open === r.sequence && <pre className="terminal mx-2 mb-2 p-3 text-[11px] text-[var(--ok)] overflow-auto">{JSON.stringify(r, null, 2)}</pre>}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
