import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { http } from "@/lib/api";

export const COLORS = { ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)", info: "var(--info)", muted: "var(--muted-c)", violet: "var(--violet)" };
const TONE_VAR = { ok: "ok", warn: "warn", bad: "bad", info: "info", muted: "muted", violet: "violet" };

const CHART = {
  light: { grid: "#E2E8F0", axis: "#94A3B8", tooltipBg: "#FFFFFF", tooltipBorder: "#E2E8F0", text: "#0F172A", brand: "#2563EB", bad: "#DC2626", cursor: "#F1F5F9",
    palette: ["#2563EB", "#059669", "#D97706", "#DC2626", "#7C3AED", "#DB2777", "#0891B2", "#65A30D", "#EA580C", "#475569"] },
  dark: { grid: "#232F44", axis: "#6B7A90", tooltipBg: "#131A27", tooltipBorder: "#232F44", text: "#F1F5F9", brand: "#60A5FA", bad: "#F87171", cursor: "#1A2333",
    palette: ["#60A5FA", "#34D399", "#FBBF24", "#F87171", "#A78BFA", "#F472B6", "#22D3EE", "#A3E635", "#FB923C", "#CBD5E1"] },
};

const ThemeCtx = createContext({ theme: "light", toggle: () => {} });

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => localStorage.getItem("fras-theme") || "light");
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("fras-theme", theme);
  }, [theme]);
  const toggle = () => setTheme((t) => (t === "light" ? "dark" : "light"));
  return <ThemeCtx.Provider value={{ theme, toggle }}>{children}</ThemeCtx.Provider>;
}

export const useTheme = () => useContext(ThemeCtx);
export const useChart = () => {
  const c = CHART[useTheme().theme];
  return { ...c, tooltip: { background: c.tooltipBg, border: `1px solid ${c.tooltipBorder}`, borderRadius: 10, fontSize: 12, color: c.text, boxShadow: "0 6px 16px -4px rgb(0 0 0 / .12)" } };
};

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

export const Dot = ({ tone = "ok" }) => <span className={`dot ${tone === "bad" ? "dot-live" : ""}`} style={{ background: COLORS[tone] }} />;

export const Pill = ({ tone = "info", children, testId }) => {
  const t = TONE_VAR[tone] || "info";
  return (
    <span data-testid={testId} className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap"
      style={{ color: `var(--${t === "muted" ? "muted-c" : t})`, borderColor: `var(--${t}-border)`, background: `var(--${t}-bg)` }}>{children}</span>
  );
};

export const PageHeader = ({ kicker, title, children }) => (
  <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-4 mb-8 rise">
    <div>
      <h1 className="font-heading text-3xl sm:text-4xl font-bold tracking-tight text-[var(--text)]">{title}</h1>
      {kicker && <p className="text-sm text-[var(--text-muted)] mt-1.5">{kicker}</p>}
    </div>
    <div className="flex flex-wrap items-center gap-2">{children}</div>
  </div>
);

export const Panel = ({ title, right, children, className = "", testId }) => (
  <section data-testid={testId} className={`panel p-5 sm:p-6 rise ${className}`}>
    {(title || right) && (
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <h2 className="font-heading text-base md:text-lg font-semibold tracking-tight text-[var(--text)]">{title}</h2>
        <div className="flex flex-wrap items-center gap-2">{right}</div>
      </div>
    )}
    {children}
  </section>
);

export const Stat = ({ label, value, tone, sub, testId }) => (
  <div className="panel p-5 rise min-w-0 overflow-hidden" data-testid={testId}>
    <div className="text-xs font-medium text-[var(--text-muted)] truncate">{label}</div>
    <div title={typeof value === "string" ? value : undefined} className="font-heading text-xl sm:text-2xl font-bold tracking-tight leading-tight mt-2 break-words" style={{ color: tone ? COLORS[tone] : "var(--text)" }}>{value}</div>
    {sub && <div className="text-xs text-[var(--text-muted)] mt-1">{sub}</div>}
  </div>
);

export const Json = ({ value, testId, className = "max-h-80" }) => (
  <pre data-testid={testId} className={`terminal p-4 text-[11px] leading-relaxed text-[var(--ok)] overflow-auto scroll-thin ${className}`}>
    {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
  </pre>
);

export const Check = ({ ok, label, detail, testId }) => (
  <div data-testid={testId} className="flex items-center justify-between gap-3 py-2.5 border-b border-[var(--line)] last:border-0">
    <div className="flex items-center gap-3"><Dot tone={ok ? "ok" : "bad"} /><span className="text-sm text-[var(--text)]">{label}</span></div>
    <span className="font-mono text-[11px] text-[var(--text-muted)] text-right truncate max-w-[50%]">{detail}</span>
  </div>
);

export const Field = ({ label, children }) => (
  <label className="block">
    <span className="text-xs font-medium text-[var(--text-muted)]">{label}</span>
    <div className="mt-1.5">{children}</div>
  </label>
);

export const statusTone = (s) => ({ active: "ok", rotating: "info", retired: "muted", revoked: "bad", supported: "ok", deprecated: "warn", required: "info", unsupported: "bad" }[s] || "info");
