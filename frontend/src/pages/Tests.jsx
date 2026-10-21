import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Dot, Field, PageHeader, Panel, Pill, Stat, useLoad } from "@/components/kit";
import { call } from "@/lib/api";

export default function Tests() {
  const [suites] = useLoad("/console/tests/suites");
  const [latest] = useLoad("/console/tests/latest");
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [seed, setSeed] = useState("0");
  const [runs, setRuns] = useState("1000");
  const [sel, setSel] = useState([]);
  const result = run || (latest?.results ? latest : null);

  const go = async () => {
    setBusy(true);
    const r = await call("post", "/console/tests/run", { suites: sel.length ? sel : null, seed: Number(seed), runs: Number(runs) });
    setBusy(false);
    if (r) setRun(r);
  };
  const toggle = (s) => setSel((x) => (x.includes(s) ? x.filter((y) => y !== s) : [...x, s]));
  const grouped = {};
  (result?.results || []).forEach((r) => (grouped[r.suite] = [...(grouped[r.suite] || []), r]));

  return (
    <div>
      <PageHeader kicker="unit · contract · property · race · chaos" title="Test Runner">
        <Button data-testid="run-tests-button" disabled={busy} onClick={go}>{busy ? "Running…" : sel.length ? `Run ${sel.length} suites` : "Run all suites"}</Button>
      </PageHeader>
      <div className="grid lg:grid-cols-4 gap-6 mb-6">
        <Panel title="Configuration" testId="test-config">
          <div className="grid grid-cols-2 gap-3">
            <Field label="FC_SEED"><Input data-testid="test-seed-input" value={seed} onChange={(e) => setSeed(e.target.value)} className="font-mono text-xs bg-[#04060A]" /></Field>
            <Field label="numRuns"><Input data-testid="test-runs-input" value={runs} onChange={(e) => setRuns(e.target.value)} className="font-mono text-xs bg-[#04060A]" /></Field>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {Object.entries(suites?.suites || {}).map(([s, n]) => (
              <button key={s} data-testid={`suite-toggle-${s}`} onClick={() => toggle(s)}
                className={`font-mono text-[11px] px-2 py-1 rounded-sm border transition-colors ${sel.includes(s) ? "border-cyan-400 text-cyan-300 bg-cyan-400/10" : "border-[#1E2638] text-slate-400 hover:text-slate-200"}`}>{s} · {n}</button>
            ))}
          </div>
          <p className="text-[11px] text-slate-500 mt-3 font-mono">Property tests generate → find failure → shrink → report seed. Replay with the same FC_SEED.</p>
        </Panel>
        <div className="lg:col-span-3 grid grid-cols-2 lg:grid-cols-4 gap-4 content-start">
          <Stat label="Passed" value={result?.passed ?? "—"} tone="ok" testId="tests-passed-count" />
          <Stat label="Failed" value={result?.failed ?? "—"} tone={result?.failed ? "bad" : "ok"} testId="tests-failed-count" />
          <Stat label="Duration" value={result ? `${result.durationMs}ms` : "—"} sub={result?.ranAt?.slice(11, 19)} />
          <Stat label="Seed" value={result?.seed ?? "—"} sub={`${result?.runs ?? "-"} runs/property`} />
          {result && <div className="col-span-2 lg:col-span-4"><Progress data-testid="tests-progress" value={(result.passed / Math.max(result.total, 1)) * 100} className="h-1.5" /></div>}
        </div>
      </div>
      <div className="grid lg:grid-cols-2 gap-6" data-testid="test-results">
        {!result && <Panel><div className="text-sm text-slate-500">No runs yet — start the suite to exercise signatures, locks, outbox and chaos scenarios against isolated collections.</div></Panel>}
        {Object.entries(grouped).map(([suite, rows]) => (
          <Panel key={suite} title={suite} testId={`suite-${suite}`} right={<Pill tone={rows.every((r) => r.status === "passed") ? "ok" : "bad"}>{rows.filter((r) => r.status === "passed").length}/{rows.length}</Pill>}>
            {rows.map((r) => (
              <div key={r.name} className="py-1.5 border-b border-[#1E2638] last:border-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-2 text-sm text-slate-200"><span className="mt-1.5"><Dot tone={r.status === "passed" ? "ok" : "bad"} /></span>{r.name}</div>
                  <span className="font-mono text-[10px] text-slate-500 shrink-0">{r.durationMs}ms</span>
                </div>
                {r.detail && <div className={`font-mono text-[10px] ml-5 mt-0.5 ${r.status === "passed" ? "text-slate-500" : "text-[#FF3366]"}`}>{r.detail}</div>}
              </div>
            ))}
          </Panel>
        ))}
      </div>
    </div>
  );
}
