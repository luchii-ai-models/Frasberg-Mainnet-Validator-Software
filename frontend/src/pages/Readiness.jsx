import { Button } from "@/components/ui/button";
import { Check, PageHeader, Panel, useLoad } from "@/components/kit";

export default function Readiness() {
  const [d, reload] = useLoad("/console/readiness");
  if (!d) return null;
  const go = d.decision === "GO";
  return (
    <div>
      <PageHeader kicker="ALL GREEN = GO MAINNET · ANY FAILURE = NO-GO" title="Readiness Gate">
        <Button data-testid="readiness-evaluate-button" onClick={reload}>Re-evaluate</Button>
      </PageHeader>
      <div className="grid lg:grid-cols-3 gap-6">
        <section className="panel p-8 flex flex-col justify-between rise" data-testid="readiness-decision"
          style={{ borderColor: go ? "var(--ok-border)" : "var(--bad-border)", background: go ? "var(--ok-bg)" : "var(--bad-bg)" }}>
          <div className="text-xs font-medium text-[var(--text-muted)]">launch decision</div>
          <div data-testid="readiness-decision-value" className="font-heading text-7xl font-extrabold tracking-tight my-6" style={{ color: go ? "var(--ok)" : "var(--bad)" }}>{d.decision}</div>
          <div className="font-mono text-sm text-[var(--text-2)]">{d.passed}/{d.total} checks green</div>
          <div className="font-mono text-[11px] text-[var(--text-muted)] mt-1">{d.evaluatedAt}</div>
          {!go && <p className="text-xs text-[var(--text-2)] mt-4">Run the full test suite and drain the outbox to clear typical blockers.</p>}
        </section>
        <Panel title="Automated Checklist" className="lg:col-span-2" testId="readiness-checklist">
          {d.checks.map((c, i) => <Check key={c.name} ok={c.passed} label={c.name} detail={c.details} testId={`readiness-check-${i}`} />)}
        </Panel>
      </div>
    </div>
  );
}
