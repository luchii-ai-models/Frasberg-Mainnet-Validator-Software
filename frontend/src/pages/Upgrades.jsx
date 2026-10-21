import { useEffect, useState } from "react";
import { PageHeader, Panel, Pill, useLoad } from "@/components/kit";
import { UpgradeNudges } from "@/components/UpgradeNudges";

const PHASE = { scheduled: ["Scheduled", "muted"], announcement: ["30-day notice", "info"], upgrade_window: ["Upgrade window", "warn"], grace: ["Grace epoch", "violet"], enforced: ["Enforced", "ok"] };
const COLOR = { announcement: "var(--brand)", upgrade_window: "var(--warn)", grace: "var(--violet)" };

const fmt = (s) => {
  if (s <= 0) return "in effect";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return `${d}d ${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m ${String(sec).padStart(2, "0")}s`;
};

function Timeline({ r, nowMs }) {
  const start = new Date(r.phases[0].start).getTime();
  const end = new Date(r.phases[2].end).getTime();
  const span = end - start;
  const pos = Math.min(100, Math.max(0, ((nowMs - start) / span) * 100));
  return (
    <div className="mt-4">
      <div className="relative h-3 rounded-sm overflow-hidden flex bg-[var(--surface)] border border-[var(--line)]">
        {r.phases.map((p) => <div key={p.name} style={{ width: `${((new Date(p.end) - new Date(p.start)) / span) * 100}%`, background: `color-mix(in srgb, ${COLOR[p.name]} 22%, transparent)`, borderRight: `1px solid ${COLOR[p.name]}` }} />)}
        <div className="absolute top-0 bottom-0 w-0.5 bg-[var(--text)]" style={{ left: `${pos}%` }} />
      </div>
      <div className="grid grid-cols-3 gap-2 mt-2">
        {r.phases.map((p) => (
          <div key={p.name} className="font-mono text-[10px]">
            <div style={{ color: COLOR[p.name] }}>{PHASE[p.name][0]}</div>
            <div className="text-[var(--text-muted)]">{p.start.slice(0, 16).replace("T", " ")} → {p.end.slice(0, 16).replace("T", " ")}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Upgrades() {
  const [d] = useLoad("/console/upgrades");
  const [nowMs, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  if (!d) return null;
  const upcoming = d.requirements.filter((r) => r.currentPhase !== "enforced");
  const past = d.requirements.filter((r) => r.currentPhase === "enforced");
  return (
    <div>
      <PageHeader kicker={`notice ${d.policy.announcementDays}d · window ${d.policy.upgradeWindowDays}d · grace ${d.policy.graceEpochs} epoch · 1 epoch = ${d.epochHours}h`} title="Upgrade Countdown" />
      <div className="space-y-6 mb-6">
        {upcoming.map((r) => {
          const remaining = Math.max(0, Math.round((new Date(r.enforceAt).getTime() - nowMs) / 1000));
          const [label, tone] = PHASE[r.currentPhase];
          return (
            <Panel key={r.epoch} testId={`upgrade-${r.epoch}`} title={`Epoch ${r.epoch} requirement`}
              right={<><Pill tone={tone} testId={`upgrade-phase-${r.epoch}`}>{label}</Pill><Pill tone={r.selfReady ? "ok" : "bad"} testId={`upgrade-self-ready-${r.epoch}`}>{r.selfReady ? "this node ready" : "this node must upgrade"}</Pill></>}>
              <div className="grid lg:grid-cols-4 gap-6 items-end">
                <div className="lg:col-span-2">
                  <div className="text-xs font-medium text-[var(--text-muted)]">enforcement in</div>
                  <div data-testid={`upgrade-countdown-${r.epoch}`} className="font-heading text-4xl sm:text-5xl font-extrabold tracking-tight text-[var(--text)] mt-1 tabular-nums">{fmt(remaining)}</div>
                  <div className="font-mono text-[11px] text-[var(--text-muted)] mt-1">enforced {r.enforceAt.slice(0, 16).replace("T", " ")} UTC · grace ends {r.graceEndsAt.slice(0, 16).replace("T", " ")}</div>
                </div>
                <div className="font-mono text-xs space-y-1">
                  <div className="text-[var(--text-2)]">core <span className="text-[var(--text)]">{r.validator.minimum}</span> → rec {r.validator.recommended} · max {r.validator.maximum}</div>
                  <div className="text-[var(--text-2)]">luchii <span className="text-[var(--text)]">{r.luchii.minimum}</span> → rec {r.luchii.recommended}</div>
                  <div className="text-[var(--text-2)]">next: <span className="text-[var(--brand)]">{r.nextMilestone ? PHASE[r.nextMilestone][0] : "—"}</span> {r.nextMilestoneAt?.slice(0, 10)}</div>
                </div>
                <div>
                  <div className="text-xs font-medium text-[var(--text-muted)]">fleet ready</div>
                  <div className="font-heading text-3xl font-bold mt-1" style={{ color: r.fleetReady === r.fleetTotal ? "var(--ok)" : "var(--warn)" }} data-testid={`upgrade-fleet-ready-${r.epoch}`}>{r.fleetReady}/{r.fleetTotal}</div>
                </div>
              </div>
              <Timeline r={r} nowMs={nowMs} />
            </Panel>
          );
        })}
      </div>
      {upcoming.length > 0 && <UpgradeNudges epochs={upcoming.map((r) => r.epoch)} />}
      <Panel title="Enforced requirements" testId="upgrades-history">
        {past.map((r) => (
          <div key={r.epoch} className="flex flex-wrap justify-between gap-2 py-2 border-b border-[var(--line)] last:border-0 font-mono text-xs">
            <span className="text-[var(--brand)]">epoch {r.epoch}</span>
            <span className="text-[var(--text-2)]">core ≥ {r.validator.minimum} · luchii ≥ {r.luchii.minimum}</span>
            <span className="text-[var(--text-muted)]">enforced {r.enforceAt.slice(0, 10)}</span>
          </div>
        ))}
      </Panel>
    </div>
  );
}
