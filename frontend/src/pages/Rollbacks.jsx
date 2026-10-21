import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Field, PageHeader, Panel, Pill, useLoad } from "@/components/kit";
import { call } from "@/lib/api";

const STATUS_TONE = { requested: "info", executed: "warn", verified: "ok", rejected: "bad" };
const APPROVERS = ["platform-lead", "security-lead", "governance-lead", "validator-ops"];

function RollbackCard({ rb, required, reload }) {
  const [approver, setApprover] = useState("security-lead");
  const act = async (path, body, msg) => { await call("post", `/console/rollbacks/${rb.rollback_id}/${path}`, body, msg); reload(); };
  return (
    <div className="panel p-4" data-testid={`rollback-card-${rb.rollback_id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2"><Pill tone="violet">{rb.level}</Pill><Pill tone={STATUS_TONE[rb.status]} testId={`rollback-status-${rb.rollback_id}`}>{rb.status}</Pill></div>
        <span className="font-mono text-[11px] text-[var(--text-muted)]">{rb.ticket_id} · {rb.incident_id}</span>
      </div>
      <div className="mt-3 text-sm text-[var(--text)]">{rb.reason}</div>
      <div className="font-mono text-[11px] text-[var(--text-2)] mt-1">{rb.current_version} → {rb.target_version} · requested by {rb.requester}</div>
      <div className="font-mono text-[11px] mt-2">approvals {rb.approvals.length}/{required[rb.level]}: <span className="text-[var(--ok)]">{rb.approvals.join(", ") || "none"}</span></div>
      <div className="font-mono text-[10px] text-[var(--text-muted)] mt-1">{rb.history.join(" → ")}</div>
      {rb.status === "requested" && (
        <div className="flex flex-wrap items-center gap-2 mt-3">
          <Select value={approver} onValueChange={setApprover}>
            <SelectTrigger data-testid={`approver-select-${rb.rollback_id}`} className="w-44 h-8 font-mono text-xs bg-[var(--surface)]"><SelectValue /></SelectTrigger>
            <SelectContent>{APPROVERS.map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}</SelectContent>
          </Select>
          <Button data-testid={`approve-${rb.rollback_id}`} size="sm" variant="secondary" onClick={() => act("approve", { actor: approver }, `Approved by ${approver}`)}>Approve</Button>
          <Button data-testid={`reject-${rb.rollback_id}`} size="sm" variant="secondary" onClick={() => act("reject", { actor: approver }, "Rejected")}>Reject</Button>
          <Button data-testid={`execute-${rb.rollback_id}`} size="sm" onClick={() => act("execute", null, "Rollback executed")}>Execute</Button>
        </div>
      )}
      {rb.status === "executed" && <Button data-testid={`verify-${rb.rollback_id}`} size="sm" className="mt-3" onClick={() => act("verify", null, "Rollback verified")}>Verify (audit chain)</Button>}
    </div>
  );
}

export default function Rollbacks() {
  const [d, reload] = useLoad("/console/rollbacks");
  const [f, setF] = useState({ level: "L3", reason: "Consensus divergence after 6.4.1 upgrade", ticketId: "OPS-1042", incidentId: "INC-77", targetVersion: "6.2.0", requester: "platform-lead" });
  const submit = async () => { await call("post", "/console/rollbacks", f, "Rollback requested"); reload(); };
  const clear = async () => { await call("put", "/console/settings", { safeMode: false, governanceFrozen: false, validatorsFrozen: false }, "Freezes cleared"); };
  if (!d) return null;
  return (
    <div>
      <PageHeader kicker="L1 automatic · L2 1 · L3 2 · L4 3 approvals" title="Rollback Gates">
        <Button data-testid="clear-freezes-button" variant="secondary" onClick={clear}>Clear freezes / safe mode</Button>
      </PageHeader>
      <div className="grid lg:grid-cols-3 gap-6">
        <Panel title="Request Rollback" testId="rollback-form">
          <div className="space-y-3">
            <Field label="level">
              <Select value={f.level} onValueChange={(v) => setF({ ...f, level: v })}>
                <SelectTrigger data-testid="rollback-level-select" className="font-mono text-xs bg-[var(--surface)]"><SelectValue /></SelectTrigger>
                <SelectContent>{Object.entries(d.levels).map(([k, v]) => <SelectItem key={k} value={k} data-testid={`rollback-level-${k}`}>{k} — {v}</SelectItem>)}</SelectContent>
              </Select>
            </Field>
            {["reason", "ticketId", "incidentId", "targetVersion", "requester"].map((k) => (
              <Field key={k} label={k}><Input data-testid={`rollback-input-${k}`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} className="font-mono text-xs bg-[var(--surface)]" /></Field>
            ))}
            <Button data-testid="rollback-submit-button" className="w-full" onClick={submit}>Submit request</Button>
            <div className="font-mono text-[10px] text-[var(--text-muted)] leading-5">known versions: {d.knownVersions.join(", ")}</div>
          </div>
        </Panel>
        <div className="lg:col-span-2 space-y-4" data-testid="rollback-list">
          {d.rollbacks.length === 0 && <Panel><div className="text-sm text-[var(--text-muted)]">No rollback requests yet.</div></Panel>}
          {d.rollbacks.map((rb) => <RollbackCard key={rb.rollback_id} rb={rb} required={d.requiredApprovals} reload={reload} />)}
        </div>
      </div>
    </div>
  );
}
