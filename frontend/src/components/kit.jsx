import { useCallback, useEffect, useState } from "react";
import { http } from "@/lib/api";

export const COLORS = { ok: "#00E676", warn: "#FFB300", bad: "#FF3366", info: "#00F0FF", muted: "#64748B", violet: "#8B5CF6" };

export function useLoad(url, deps = []) {
  const [data, setData] = useState(null);
  const reload = useCallback(async () => {
    try {
      const r = await http.get(url);
      setData(r.data);
    } catch {
      setData((d) => d);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, ...deps]);
  useEffect(() => { reload(); }, [reload]);
  return [data, reload];
}

export const Dot = ({ tone = "ok" }) => <span className="dot" style={{ background: COLORS[tone], boxShadow: `0 0 10px ${COLORS[tone]}` }} />;

export const Pill = ({ tone = "info", children, testId }) => (
  <span data-testid={testId} className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-sm font-mono text-[10px] uppercase tracking-widest border"
    style={{ color: COLORS[tone], borderColor: `${COLORS[tone]}55`, background: `${COLORS[tone]}12` }}>{children}</span>
);

export const PageHeader = ({ kicker, title, children }) => (
  <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-4 mb-8 rise">
    <div>
      <div className="font-mono text-[11px] uppercase tracking-[0.25em] text-cyan-400 mb-2">{kicker}</div>
      <h1 className="font-heading text-4xl sm:text-5xl font-extrabold tracking-tight text-slate-50">{title}</h1>
    </div>
    <div className="flex flex-wrap gap-2">{children}</div>
  </div>
);

export const Panel = ({ title, right, children, className = "", testId }) => (
  <section data-testid={testId} className={`panel p-5 rise ${className}`}>
    {(title || right) && (
      <div className="flex items-center justify-between gap-3 mb-4">
        <h2 className="font-heading text-base md:text-lg font-bold tracking-tight text-slate-100">{title}</h2>
        <div className="flex items-center gap-2">{right}</div>
      </div>
    )}
    {children}
  </section>
);

export const Stat = ({ label, value, tone, sub, testId }) => (
  <div className="panel p-4 rise" data-testid={testId}>
    <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">{label}</div>
    <div className="font-heading text-3xl font-bold mt-2" style={{ color: tone ? COLORS[tone] : "#F8FAFC" }}>{value}</div>
    {sub && <div className="text-xs text-slate-400 mt-1 font-mono">{sub}</div>}
  </div>
);

export const Json = ({ value, testId, className = "max-h-80" }) => (
  <pre data-testid={testId} className={`terminal p-4 text-[11px] leading-relaxed text-emerald-300 overflow-auto scroll-thin ${className}`}>
    {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
  </pre>
);

export const Check = ({ ok, label, detail, testId }) => (
  <div data-testid={testId} className="flex items-center justify-between gap-3 py-2 border-b border-[#1E2638] last:border-0">
    <div className="flex items-center gap-3"><Dot tone={ok ? "ok" : "bad"} /><span className="text-sm text-slate-200">{label}</span></div>
    <span className="font-mono text-[11px] text-slate-400 text-right truncate max-w-[50%]">{detail}</span>
  </div>
);

export const Field = ({ label, children }) => (
  <label className="block">
    <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">{label}</span>
    <div className="mt-1.5">{children}</div>
  </label>
);

export const statusTone = (s) => ({ active: "ok", rotating: "info", retired: "muted", revoked: "bad", supported: "ok", deprecated: "warn", required: "info", unsupported: "bad" }[s] || "info");
