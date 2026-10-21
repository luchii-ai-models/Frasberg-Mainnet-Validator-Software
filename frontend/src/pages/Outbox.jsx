import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Field, PageHeader, Panel, Pill, Stat, useLoad } from "@/components/kit";
import { call, short } from "@/lib/api";

const MODES = [["none", "healthy bus"], ["fail", "bus unavailable"], ["timeout", "bus timeout (5s ack)"], ["crash_before_ack", "crash after publish, before ack"]];

const Row = ({ e, action }) => (
  <div className="grid grid-cols-12 gap-2 items-center py-2 border-b border-[#1E2638] last:border-0 font-mono text-[11px]">
    <span className="col-span-3 text-slate-300 truncate">{short(e.event_id, 13)}</span>
    <span className="col-span-3 text-cyan-300 truncate">{e.event_type}</span>
    <span className="col-span-1 text-amber-300">r{e.retry_count}</span>
    <span className="col-span-3 text-slate-500 truncate">{e.claim_owner ? `claimed ${e.claim_owner}` : e.last_error || (e.published_at ? "published" : "pending")}</span>
    <span className="col-span-2 text-right">{action}</span>
  </div>
);

export default function Outbox() {
  const [d, reload] = useLoad("/console/outbox");
  const run = async (method, url, body, msg) => { await call(method, url, body, msg); reload(); };
  if (!d) return null;
  const mode = d.settings.busMode || "none";
  const setMode = (m) => run("put", "/console/settings", m === "none" ? { clearBusMode: true } : { busMode: m }, `Bus mode: ${m}`);

  return (
    <div>
      <PageHeader kicker="commit-only publishing · event_id idempotency" title="Audit Outbox">
        <Button data-testid="outbox-publish-button" onClick={() => run("post", "/console/outbox/publish", {}, "Publisher batch complete")}>Run publisher batch</Button>
      </PageHeader>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        <Stat label="Pending" value={d.pendingCount} tone={d.pendingCount > 100 ? "warn" : "info"} testId="outbox-pending-count" />
        <Stat label="Published" value={d.publishedCount} tone="ok" testId="outbox-published-count" />
        <Stat label="Dead letter" value={d.deadCount} tone={d.deadCount ? "bad" : "ok"} testId="outbox-dead-count" />
        <Stat label="Bus deliveries" value={d.busDeliveries} sub="at-least-once" />
        <Stat label="Consumer processed" value={d.consumerProcessed} sub="exactly-once (dedup)" testId="consumer-processed-count" />
      </div>
      <div className="grid lg:grid-cols-3 gap-6 mb-6">
        <Panel title="Failure Injection" testId="bus-mode-panel">
          <Field label="event bus mode">
            <Select value={mode} onValueChange={setMode}>
              <SelectTrigger data-testid="bus-mode-select" className="font-mono text-xs bg-[#04060A]"><SelectValue /></SelectTrigger>
              <SelectContent>{MODES.map(([v, l]) => <SelectItem key={v} value={v} data-testid={`bus-mode-${v}`}>{l}</SelectItem>)}</SelectContent>
            </Select>
          </Field>
          <div className="flex flex-wrap gap-2 mt-4">
            <Button data-testid="expire-claims-button" size="sm" variant="secondary" onClick={() => run("post", "/console/outbox/expire-claims", null, "Claims expired & retry timers cleared")}>Expire claims</Button>
            <Button data-testid="recover-claims-button" size="sm" variant="secondary" onClick={() => run("post", "/console/outbox/recover-claims", null, "Stale claims recovered")}>Recover stale claims</Button>
          </div>
          <div className="mt-4 font-mono text-[11px] text-slate-500 leading-6">
            retry: {d.retrySchedule.map((s) => (s < 60 ? `${s}s` : s < 3600 ? `${s / 60}m` : `${s / 3600}h`)).join(" → ")} → dead-letter after {d.maxRetries}
          </div>
        </Panel>
        <Panel title="Pending / Claimed" testId="outbox-pending-list" className="lg:col-span-2" right={<Pill tone="info">{d.pendingCount}</Pill>}>
          <div className="max-h-72 overflow-auto scroll-thin">
            {d.pending.length === 0 && <div className="text-sm text-slate-500">Outbox drained.</div>}
            {d.pending.map((e) => <Row key={e.event_id} e={e} />)}
          </div>
        </Panel>
      </div>
      <div className="grid lg:grid-cols-2 gap-6">
        <Panel title="Dead-Letter Queue" testId="dead-letter-list" right={d.deadCount > 0 && <Button data-testid="replay-all-button" size="sm" onClick={() => run("post", "/console/outbox/replay", { all: true }, "Dead letters replayed")}>Replay all</Button>}>
          <div className="max-h-72 overflow-auto scroll-thin">
            {d.deadLetter.length === 0 && <div className="text-sm text-slate-500">Dead-letter queue empty.</div>}
            {d.deadLetter.map((e) => (
              <Row key={e.event_id} e={e} action={<Button data-testid={`replay-${e.event_id}`} size="sm" variant="secondary" onClick={() => run("post", "/console/outbox/replay", { eventId: e.event_id }, "Event replayed")}>Replay</Button>} />
            ))}
          </div>
        </Panel>
        <Panel title="Recently Published" testId="published-list">
          <div className="max-h-72 overflow-auto scroll-thin">{d.recentPublished.map((e) => <Row key={e.event_id} e={e} />)}</div>
        </Panel>
      </div>
    </div>
  );
}
