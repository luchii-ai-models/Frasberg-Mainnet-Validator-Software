import { Button } from "@/components/ui/button";
import { Panel, Pill, useLoad } from "@/components/kit";
import { toast } from "sonner";
import { call } from "@/lib/api";

const TONE = { delivered: "ok", failed: "bad", dry_run: "warn" };

export const UpgradeNudges = ({ epochs }) => {
  const [d, reload] = useLoad("/console/upgrades/nudges");
  if (!d) return null;
  const send = async (epoch, resend) => {
    const r = await call("post", `/console/upgrades/${epoch}/nudge`, { resend });
    if (r) toast.success(`${r.sent.length} nudge(s) sent`, { description: r.sent.length ? undefined : "every lagging validator was already nudged — use Re-send all" });
    reload();
  };
  return (
    <Panel title="Fleet upgrade nudges" testId="upgrade-nudges" className="mb-6"
      right={<Pill tone={d.slackConfigured ? "ok" : "warn"} testId="nudges-slack-status">{d.slackConfigured ? `slack ${d.slackChannel || "live"}` : "slack dry-run"}</Pill>}>
      <div className="flex flex-wrap gap-2 mb-4">
        {epochs.map((e) => (
          <span key={e} className="flex gap-2">
            <Button data-testid={`nudge-send-${e}`} size="sm" onClick={() => send(e, false)}>Nudge lagging · epoch {e}</Button>
            <Button data-testid={`nudge-resend-${e}`} size="sm" variant="secondary" onClick={() => send(e, true)}>Re-send all</Button>
          </span>
        ))}
      </div>
      <div className="terminal p-3 font-mono text-[11px] max-h-64 overflow-auto scroll-thin space-y-1" data-testid="nudges-log">
        {d.nudges.length === 0 && <div className="text-slate-600">no nudges yet — lagging validators get one Slack heads-up automatically when the upgrade window opens</div>}
        {d.nudges.map((n) => (
          <div key={`${n.epoch}-${n.validatorId}`} data-testid={`nudge-row-${n.epoch}-${n.validatorId}`} className="flex flex-wrap gap-2 items-center">
            <span className="text-slate-600">{n.sentAt.slice(0, 19).replace("T", " ")}</span>
            <Pill tone={TONE[n.result] || "info"}>{n.result}</Pill>
            <span className="text-cyan-300">{n.validatorId}</span>
            <span className="text-slate-300">{n.name} · {n.operator}</span>
            <span className="text-[#FFB300]">{n.code}</span>
            <span className="text-slate-500">epoch {n.epoch} · {n.trigger} · ×{n.count}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
};
