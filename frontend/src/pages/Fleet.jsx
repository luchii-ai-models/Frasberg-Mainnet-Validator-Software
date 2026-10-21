import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { FleetTrends } from "@/components/FleetTrends";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dot, Field, PageHeader, Panel, Pill, Stat, statusTone, useLoad } from "@/components/kit";
import { call } from "@/lib/api";

const BLANK = { validatorId: "", name: "", operator: "independent", region: "global", validatorVersion: "6.4.1", luchiiVersion: "4.2.0", channel: "stable",
  epoch: 900, commission: 5, stake: 100000, uptime: 99.95, voteParticipation: 97, missedBlocks: 1, apiAvailability: 99.9,
  securityScanPassed: true, identityVerified: true, governanceStanding: true };
const NUMS = ["epoch", "commission", "stake", "uptime", "voteParticipation", "missedBlocks", "apiAvailability"];
const TEXT = ["validatorId", "name", "operator", "region", "validatorVersion", "luchiiVersion", "channel"];
const BOOLS = ["securityScanPassed", "identityVerified", "governanceStanding"];

function Editor({ value, onClose, onSaved }) {
  const [f, setF] = useState(value);
  const isNew = !value.validatorId;
  const save = async () => {
    const body = { ...f };
    NUMS.forEach((k) => (body[k] = Number(f[k])));
    const r = await call(isNew ? "post" : "put", isNew ? "/console/fleet" : `/console/fleet/${f.validatorId}`, body, isNew ? "Validator registered" : "Validator updated");
    if (r) onSaved();
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="bg-[#0F131D] border-[#1E2638] max-w-2xl" data-testid="fleet-editor-dialog">
        <DialogHeader><DialogTitle className="font-heading">{isNew ? "Register validator" : `Edit ${f.validatorId}`}</DialogTitle></DialogHeader>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {[...TEXT, ...NUMS].map((k) => (
            <Field key={k} label={k}><Input data-testid={`fleet-input-${k}`} disabled={k === "validatorId" && !isNew} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} className="font-mono text-xs bg-[#04060A]" /></Field>
          ))}
        </div>
        <div className="flex flex-wrap gap-5 mt-2">
          {BOOLS.map((k) => <label key={k} className="flex items-center gap-2 text-xs font-mono text-slate-300"><Switch data-testid={`fleet-switch-${k}`} checked={!!f[k]} onCheckedChange={(c) => setF({ ...f, [k]: c })} />{k}</label>)}
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <Button variant="secondary" onClick={onClose} data-testid="fleet-cancel-button">Cancel</Button>
          <Button onClick={save} data-testid="fleet-save-button">{isNew ? "Register" : "Save"}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

const Bar = ({ v, min, invert }) => {
  const ok = invert ? v <= min : v >= min;
  return <span className="font-mono text-xs" style={{ color: ok ? "#00E676" : "#FF3366" }}>{v}%</span>;
};

export default function Fleet() {
  const [d, reload] = useLoad("/console/fleet");
  const [edit, setEdit] = useState(null);
  const [filter, setFilter] = useState("all");
  const [removing, setRemoving] = useState(null);
  if (!d) return null;
  const s = d.summary;
  const rows = d.validators.filter((v) => filter === "all" || (filter === "issues" ? !v.delegationEligible : v.delegationEligible));
  const remove = async () => { const id = removing; setRemoving(null); if (await call("delete", `/console/fleet/${id}`, null, `${id} deregistered`)) reload(); };
  return (
    <div>
      <PageHeader kicker={`${s.total} validators${d.upcoming ? ` · next requirement epoch ${d.upcoming.epoch}` : ""}`} title="Validator Fleet">
        {["all", "eligible", "issues"].map((k) => (
          <button key={k} data-testid={`fleet-filter-${k}`} onClick={() => setFilter(k)}
            className={`font-mono text-[11px] px-2.5 py-1 rounded-sm border transition-colors ${filter === k ? "border-cyan-400 text-cyan-300 bg-cyan-400/10" : "border-[#1E2638] text-slate-400"}`}>{k}</button>
        ))}
        <Button data-testid="fleet-add-button" onClick={() => setEdit(BLANK)}>Register validator</Button>
      </PageHeader>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        <Stat label="Healthy" value={`${s.healthy}/${s.total}`} tone={s.healthy === s.total ? "ok" : "warn"} testId="fleet-healthy" />
        <Stat label="Version compliant" value={`${s.compliant}/${s.total}`} tone={s.compliant === s.total ? "ok" : "warn"} testId="fleet-compliant" />
        <Stat label="Delegation eligible" value={`${s.eligible}/${s.total}`} tone={s.eligible === s.total ? "ok" : "warn"} testId="fleet-eligible" />
        <Stat label={`Ready for epoch ${d.upcoming?.epoch ?? "—"}`} value={`${s.readyNext}/${s.total}`} tone={s.readyNext === s.total ? "ok" : "warn"} testId="fleet-ready-next" />
        <Stat label="Eligible stake" value={`${(s.stakeEligible / 1e6).toFixed(2)}M`} sub={`of ${(s.stakeTotal / 1e6).toFixed(2)}M FRAS`} testId="fleet-stake" />
      </div>
      <FleetTrends />
      <Panel title="Side-by-side status" testId="fleet-table">
        <div className="overflow-x-auto scroll-thin">
          <Table>
            <TableHeader><TableRow>{["Validator", "Version", "Luchii", "Class", "Compliance", "Health", "Uptime", "Votes", "Missed", "Comm.", "Stake", "Delegation", `Epoch ${d.upcoming?.epoch ?? ""}`, ""].map((h) => <TableHead key={h} className="font-mono text-[10px] uppercase tracking-widest whitespace-nowrap">{h}</TableHead>)}</TableRow></TableHeader>
            <TableBody>
              {rows.map((v) => (
                <TableRow key={v.validatorId} data-testid={`fleet-row-${v.validatorId}`} className={v.self ? "bg-cyan-400/5" : ""}>
                  <TableCell><div className="font-mono text-cyan-300 text-xs">{v.validatorId}{v.self && " ◂"}</div><div className="text-[11px] text-slate-500">{v.name} · {v.operator} · {v.region}</div></TableCell>
                  <TableCell className="font-mono text-xs">{v.validatorVersion}<div className="text-slate-500">{v.channel}</div></TableCell>
                  <TableCell className="font-mono text-xs">{v.luchiiVersion}</TableCell>
                  <TableCell><Pill tone={statusTone(v.versionClass)}>{v.versionClass}</Pill></TableCell>
                  <TableCell><span className="font-mono text-[11px]" style={{ color: v.compliant ? "#00E676" : "#FF3366" }}>{v.code}</span></TableCell>
                  <TableCell><span className="flex items-center gap-2 text-xs"><Dot tone={v.status === "healthy" ? "ok" : "bad"} />{v.status}</span></TableCell>
                  <TableCell><Bar v={v.uptime} min={99.9} /></TableCell>
                  <TableCell><Bar v={v.voteParticipation} min={95} /></TableCell>
                  <TableCell><Bar v={v.missedBlocks} min={3} invert /></TableCell>
                  <TableCell className="font-mono text-xs" style={{ color: v.commission <= 5 ? "#E2E8F0" : "#FF3366" }}>{v.commission}%</TableCell>
                  <TableCell className="font-mono text-xs" style={{ color: v.stake >= 100000 ? "#E2E8F0" : "#FF3366" }}>{(v.stake / 1000).toFixed(0)}k</TableCell>
                  <TableCell>
                    <Pill tone={v.delegationEligible ? "ok" : "bad"} testId={`fleet-delegation-${v.validatorId}`}>{v.delegationEligible ? "eligible" : "removed"}</Pill>
                    {v.failedCriteria.length > 0 && <div className="font-mono text-[10px] text-[#FF3366] mt-1">{v.failedCriteria.join(", ")}</div>}
                  </TableCell>
                  <TableCell>{v.nextRequirement && <Pill tone={v.nextRequirement.ready ? "ok" : "warn"}>{v.nextRequirement.ready ? "ready" : v.nextRequirement.code}</Pill>}</TableCell>
                  <TableCell className="text-right whitespace-nowrap">
                    {!v.self && <>
                      <Button data-testid={`fleet-edit-${v.validatorId}`} size="sm" variant="secondary" onClick={() => setEdit(v)}>Edit</Button>
                      <Button data-testid={`fleet-delete-${v.validatorId}`} size="sm" variant="ghost" className="text-[#FF3366]" onClick={() => setRemoving(v.validatorId)}>Remove</Button>
                    </>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Panel>
      <AlertDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)}>
        <AlertDialogContent className="bg-[#0F131D] border-[#1E2638]" data-testid="fleet-remove-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle className="font-heading">Deregister {removing}?</AlertDialogTitle>
            <AlertDialogDescription>The validator leaves the fleet view and trends. This action is recorded in the audit log.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel data-testid="fleet-remove-cancel">Cancel</AlertDialogCancel>
            <AlertDialogAction data-testid="fleet-remove-confirm" className="bg-[#FF3366] hover:bg-[#FF3366]/80 text-white" onClick={remove}>Deregister</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {edit && <Editor value={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
    </div>
  );
}
