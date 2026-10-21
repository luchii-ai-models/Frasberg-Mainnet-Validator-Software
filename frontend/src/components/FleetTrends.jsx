import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Panel, Pill, useChart, useLoad } from "@/components/kit";

const METRIC_LABEL = { uptime: "Uptime %", voteParticipation: "Vote participation %", missedBlocks: "Missed blocks %" };

const Tabs = ({ items, value, onChange, prefix }) => (
  <div className="flex gap-1">
    {items.map((k) => (
      <button key={k} data-testid={`${prefix}-${k}`} onClick={() => onChange(k)}
        className={`text-xs font-medium px-3 py-1.5 rounded-full border transition-colors ${value === k ? "border-[var(--brand)] text-[var(--brand)] bg-[var(--brand-subtle)]" : "border-[var(--line)] text-[var(--text-2)] hover:text-[var(--text)]"}`}>{k}</button>
    ))}
  </div>
);

const fmtT = (range) => (t) => {
  const d = new Date(t).toISOString();
  return range === "7d" ? d.slice(5, 13).replace("T", " ") + "h" : d.slice(11, 16);
};

function DriftList({ drift, metric, colorOf, focus, setFocus }) {
  return (
    <div className="space-y-1" data-testid="fleet-drift-list">
      {drift.map((v) => {
        const m = v.metrics[metric];
        const tone = m.breach ? "bad" : m.drifting ? "warn" : "ok";
        return (
          <button key={v.validatorId} data-testid={`fleet-drift-${v.validatorId}`} onClick={() => setFocus(focus === v.validatorId ? null : v.validatorId)}
            className={`w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded-sm border text-left transition-colors ${focus === v.validatorId ? "border-[var(--brand)] bg-[var(--brand-subtle)]" : "border-transparent hover:bg-[var(--surface-hover)]"}`}>
            <span className="flex items-center gap-2 font-mono text-[11px] text-[var(--text-2)]">
              <span className="w-2 h-2 rounded-full" style={{ background: colorOf(v.validatorId) }} />{v.validatorId}
            </span>
            <span className="flex items-center gap-2">
              <span className="font-mono text-[10px] text-[var(--text-muted)]">{m.change > 0 ? "+" : ""}{m.change}</span>
              <Pill tone={tone}>{m.breach ? "breach" : m.drifting ? "drift" : "stable"}</Pill>
            </span>
          </button>
        );
      })}
    </div>
  );
}

export const FleetTrends = () => {
  const [range, setRange] = useState("24h");
  const [metric, setMetric] = useState("uptime");
  const [focus, setFocus] = useState(null);
  const [d] = useLoad(`/console/fleet/trends?range=${range}`);
  const ch = useChart();
  const colorOf = (id) => ch.palette[(d?.validators.indexOf(id) ?? 0) % ch.palette.length];
  const flagged = d ? d.drift.filter((v) => v.drifting || v.breach).length : 0;
  return (
    <Panel title="Fleet trends" testId="fleet-trends" className="mb-6"
      right={<><Pill tone={flagged ? "warn" : "ok"} testId="fleet-drift-count">{flagged} flagged</Pill><Tabs items={["1h", "24h", "7d"]} value={range} onChange={setRange} prefix="fleet-trend-range" /></>}>
      <div className="mb-4"><Tabs items={Object.keys(METRIC_LABEL)} value={metric} onChange={setMetric} prefix="fleet-trend-metric" /></div>
      <div className="grid lg:grid-cols-4 gap-6">
        <div className="lg:col-span-3 h-72" data-testid="fleet-trend-chart">
          {d && (
            <ResponsiveContainer>
              <LineChart data={d.series[metric] || []} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
                <CartesianGrid stroke={ch.grid} strokeDasharray="3 5" vertical={false} />
                <XAxis dataKey="t" tickFormatter={fmtT(range)} stroke={ch.axis} fontSize={11} minTickGap={30} tickLine={false} />
                <YAxis stroke={ch.axis} fontSize={11} domain={["auto", "auto"]} tickLine={false} axisLine={false} />
                <Tooltip contentStyle={ch.tooltip} labelFormatter={(t) => new Date(t).toISOString().slice(0, 16).replace("T", " ")} />
                <ReferenceLine y={d.thresholds[metric]} stroke={ch.bad} strokeDasharray="4 4" label={{ value: "threshold", fill: ch.bad, fontSize: 10, position: "insideTopRight" }} />
                {d.validators.map((id) => (
                  <Line key={id} type="monotone" dataKey={id} dot={false} isAnimationActive={false} stroke={colorOf(id)}
                    strokeWidth={focus === id ? 2.5 : 1.5} strokeOpacity={!focus || focus === id ? 1 : 0.15} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
        {d && <DriftList drift={d.drift} metric={metric} colorOf={colorOf} focus={focus} setFocus={setFocus} />}
      </div>
      <p className="font-mono text-[10px] text-[var(--text-muted)] mt-3">sampled every 60s · drift = change between first and last sixth of the window · dashed line = delegation threshold</p>
    </Panel>
  );
};
