import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Panel, Pill, useLoad } from "@/components/kit";

const PALETTE = ["#00F0FF", "#00E676", "#FFB300", "#FF3366", "#8B5CF6", "#F472B6", "#38BDF8", "#A3E635", "#FB923C", "#E2E8F0"];
const METRIC_LABEL = { uptime: "Uptime %", voteParticipation: "Vote participation %", missedBlocks: "Missed blocks %" };
const TT = { background: "#0F131D", border: "1px solid #1E2638", fontFamily: "JetBrains Mono", fontSize: 11 };

const Tabs = ({ items, value, onChange, prefix }) => (
  <div className="flex gap-1">
    {items.map((k) => (
      <button key={k} data-testid={`${prefix}-${k}`} onClick={() => onChange(k)}
        className={`font-mono text-[11px] px-2.5 py-1 rounded-sm border transition-colors ${value === k ? "border-cyan-400 text-cyan-300 bg-cyan-400/10" : "border-[#1E2638] text-slate-400 hover:text-slate-200"}`}>{k}</button>
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
            className={`w-full flex items-center justify-between gap-2 px-2 py-1.5 rounded-sm border text-left transition-colors ${focus === v.validatorId ? "border-cyan-400/60 bg-cyan-400/5" : "border-transparent hover:bg-[#161B29]"}`}>
            <span className="flex items-center gap-2 font-mono text-[11px] text-slate-300">
              <span className="w-2 h-2 rounded-full" style={{ background: colorOf(v.validatorId) }} />{v.validatorId}
            </span>
            <span className="flex items-center gap-2">
              <span className="font-mono text-[10px] text-slate-500">{m.change > 0 ? "+" : ""}{m.change}</span>
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
  const colorOf = (id) => PALETTE[(d?.validators.indexOf(id) ?? 0) % PALETTE.length];
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
                <CartesianGrid stroke="#1E2638" strokeDasharray="2 4" />
                <XAxis dataKey="t" tickFormatter={fmtT(range)} stroke="#475569" fontSize={10} fontFamily="JetBrains Mono" minTickGap={30} />
                <YAxis stroke="#475569" fontSize={10} fontFamily="JetBrains Mono" domain={["auto", "auto"]} />
                <Tooltip contentStyle={TT} labelFormatter={(t) => new Date(t).toISOString().slice(0, 16).replace("T", " ")} />
                <ReferenceLine y={d.thresholds[metric]} stroke="#FF3366" strokeDasharray="4 4" label={{ value: "min", fill: "#FF3366", fontSize: 10 }} />
                {d.validators.map((id) => (
                  <Line key={id} type="monotone" dataKey={id} dot={false} isAnimationActive={false} stroke={colorOf(id)}
                    strokeWidth={focus === id ? 2.5 : 1.2} strokeOpacity={!focus || focus === id ? 1 : 0.15} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
        {d && <DriftList drift={d.drift} metric={metric} colorOf={colorOf} focus={focus} setFocus={setFocus} />}
      </div>
      <p className="font-mono text-[10px] text-slate-500 mt-3">sampled every 60s · drift = change between first and last sixth of the window · dashed line = delegation threshold</p>
    </Panel>
  );
};
