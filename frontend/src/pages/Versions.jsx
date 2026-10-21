import { Json, PageHeader, Panel, Pill, statusTone, useLoad } from "@/components/kit";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const CLASSES = [
  ["supported", "Eligible", "Enabled", "Enabled", "—"],
  ["deprecated", "Eligible", "Enabled", "Enabled", "Issued"],
  ["required", "Required", "Enabled", "Enabled", "—"],
  ["unsupported", "Removed", "Disabled", "Disabled", "—"],
];

export default function Versions() {
  const [d] = useLoad("/console/schedule");
  if (!d) return null;
  const current = d.rows.find((r) => r.epoch === Math.max(...d.rows.filter((x) => x.epoch <= d.currentEpoch).map((x) => x.epoch)));
  return (
    <div>
      <PageHeader kicker="GET /v1/network/required-versions" title="Required Versions"><Pill tone="info">current epoch {d.currentEpoch}</Pill></PageHeader>
      <Panel title="Epoch Schedule" testId="schedule-table" className="mb-6">
        <Table>
          <TableHeader>
            <TableRow>{["Epoch", "Core min", "Core max", "Recommended", "Luchii min", "Luchii max", "Channel", "Enforcement", "Signed by", "This validator"].map((h) => <TableHead key={h} className="font-mono text-[10px] uppercase tracking-widest">{h}</TableHead>)}</TableRow>
          </TableHeader>
          <TableBody>
            {d.rows.map((r) => (
              <TableRow key={r.epoch} data-testid={`schedule-row-${r.epoch}`} className={r === current ? "bg-cyan-400/5" : ""}>
                <TableCell className="font-mono text-cyan-300">{r.epoch}{r === current && " ◂"}</TableCell>
                <TableCell className="font-mono">{r.validator.minimum}</TableCell>
                <TableCell className="font-mono">{r.validator.maximum}</TableCell>
                <TableCell className="font-mono">{r.validator.recommended}</TableCell>
                <TableCell className="font-mono">{r.luchii.minimum}</TableCell>
                <TableCell className="font-mono">{r.luchii.maximum || "—"}</TableCell>
                <TableCell><Pill tone="ok">{r.validator.channel}</Pill></TableCell>
                <TableCell className="font-mono text-xs text-slate-400">{r.enforcement.startsEpoch} → grace {r.enforcement.graceEndsEpoch}</TableCell>
                <TableCell className="font-mono text-xs text-slate-400">{r.signature?.keyId}</TableCell>
                <TableCell><Pill tone={statusTone(r.currentClass)}>{r.currentClass}</Pill></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>
      <div className="grid lg:grid-cols-2 gap-6">
        <Panel title="Version Classes (Policy v1.0)" testId="version-classes">
          <Table>
            <TableHeader><TableRow>{["Status", "Delegation", "Voting", "Rewards", "Warning"].map((h) => <TableHead key={h} className="font-mono text-[10px] uppercase">{h}</TableHead>)}</TableRow></TableHeader>
            <TableBody>
              {CLASSES.map(([s, ...rest]) => (
                <TableRow key={s}><TableCell><Pill tone={statusTone(s)}>{s}</Pill></TableCell>{rest.map((x, i) => <TableCell key={i} className="text-sm text-slate-300">{x}</TableCell>)}</TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="mt-4 text-xs text-slate-400 font-mono leading-6">
            MAJOR = consensus changes · MINOR = validator features · PATCH = bug fixes<br />
            Channels tracked explicitly: {d.channels.join(" / ")} — prereleases are never rejected globally.
          </div>
        </Panel>
        <Panel title="Current Epoch Payload" testId="required-versions-json"><Json value={current} /></Panel>
      </div>
    </div>
  );
}
